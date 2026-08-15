"""SINSUNG G2B DATA VIEW 2.0 clean collector.

Single production collection engine for:
- 나라장터쇼핑몰 납품요구상세
- 물품 입찰공고
- 용역공고

2.0 principles:
- no v2.5.3~v2.6.3 collector monkey-patch chain
- one parser, one request path, one UPSERT path
- explicit API errors (including nkoneps.com.response.ResponseError)
- exact 12 procurement detail-item numbers
- historical backfill intentionally disabled until a later verified release
"""
import datetime as dt
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from db import connect, finish_sync_log, get_setting, new_sync_log, set_setting

VERSION = "2.0"
SHOP_BASE_URL = "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService"
SHOP_OPERATION = "getDlvrReqDtlInfoList"
BID_BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"

LED_DETAIL_ITEM_NOS = frozenset({
    "3911151502", "3911160302", "3911160304", "3911160501",
    "3911160802", "3911161102", "3911210201",
})
SOLAR_PANEL_DETAIL_ITEM_NOS = frozenset({"2611160701", "3912110101"})
POLE_DETAIL_ITEM_NOS = frozenset({"3911152601", "3911152602", "3911152607"})
SHOP_DETAIL_ITEM_NOS = LED_DETAIL_ITEM_NOS | SOLAR_PANEL_DETAIL_ITEM_NOS | POLE_DETAIL_ITEM_NOS

BID_TARGETS = ("LED", "조명", "가로등", "보안등", "투광등", "다운라이트", "경관", "보행신호")
SERVICE_TARGETS = ("LED", "조명", "전기", "경관", "가로등", "보안등", "조명설계", "전기설계")

SHOP_LOCK = threading.RLock()
BID_LOCK = threading.RLock()


class G2BApiError(RuntimeError):
    def __init__(self, code="", message=""):
        self.code = str(code or "")
        self.message = str(message or "")
        super().__init__(f"API 오류 {self.code}: {self.message}".strip())


class ApiQuotaReached(G2BApiError):
    pass


class ApiRateLimited(G2BApiError):
    pass


class IncompleteSyncError(RuntimeError):
    pass


def _pick(d, *names, default=""):
    for name in names:
        value = d.get(name) if isinstance(d, dict) else None
        if value not in (None, ""):
            return value
    return default


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _num(value, default=0):
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return default


def _date(value):
    s = _digits(value)
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return str(value or "")[:10]


def normalize_region(value=""):
    text = str(value or "").strip()
    if not text:
        return ""
    pairs = [
        ("서울", "서울특별시"), ("부산", "부산광역시"), ("대구", "대구광역시"),
        ("인천", "인천광역시"), ("광주", "광주광역시"), ("대전", "대전광역시"),
        ("울산", "울산광역시"), ("세종", "세종특별자치시"), ("경기", "경기도"),
        ("강원", "강원특별자치도"), ("충북", "충청북도"), ("충남", "충청남도"),
        ("전북", "전북특별자치도"), ("전남", "전라남도"), ("경북", "경상북도"),
        ("경남", "경상남도"), ("제주", "제주특별자치도"),
    ]
    for _, full in pairs:
        if full in text:
            return full
    for short, full in pairs:
        if short in text:
            return full
    return ""


def infer_region(org_name="", address="", direct_region=""):
    return normalize_region(direct_region) or normalize_region(address) or normalize_region(org_name)


def normalize_top_org(org):
    text = str(org or "").strip()
    if not text:
        return ""
    if "한국토지주택공사" in text or text.startswith("LH"):
        return "한국토지주택공사"
    if "서울주택도시공사" in text or text.startswith("SH"):
        return "서울주택도시공사"
    if "인천광역시교육청" in text:
        return "인천광역시교육청"
    return text


def _quota_keys(kind):
    key = "shop" if kind == "shop" else "bid"
    return f"api_calls_{key}_date", f"api_calls_{key}_count"


def _quota_take(kind):
    limit = max(1, int(float(get_setting("api_daily_limit", "900") or 900)))
    dkey, ckey = _quota_keys(kind)
    today = dt.date.today().isoformat()
    saved_day = get_setting(dkey, "")
    count = int(float(get_setting(ckey, "0") or 0)) if saved_day == today else 0
    if count >= limit:
        raise ApiQuotaReached("22", f"{kind.upper()} API 일일 안전한도 {limit:,}회 도달")
    set_setting(dkey, today)
    set_setting(ckey, str(count + 1))
    return count + 1, limit


def api_usage(kind):
    dkey, ckey = _quota_keys(kind)
    today = dt.date.today().isoformat()
    count = int(float(get_setting(ckey, "0") or 0)) if get_setting(dkey, "") == today else 0
    limit = max(1, int(float(get_setting("api_daily_limit", "900") or 900)))
    return count, limit


def _raise_api_error(code, message):
    code = str(code or "")
    message = str(message or "")
    if code in ("", "0", "00"):
        return
    if code == "22":
        raise ApiQuotaReached(code, message)
    if code == "23":
        raise ApiRateLimited(code, message)
    raise G2BApiError(code, message)


def _find_header(node):
    if isinstance(node, dict):
        if any(k in node for k in ("resultCode", "resultCd")):
            return node
        for key in ("header", "response", "nkoneps.com.response.ResponseError", "ResponseError", "responseError"):
            if key in node:
                found = _find_header(node.get(key))
                if found:
                    return found
        for value in node.values():
            found = _find_header(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_header(value)
            if found:
                return found
    return None


def _find_body(node):
    if isinstance(node, dict):
        body = node.get("body")
        if isinstance(body, dict):
            return body
        response = node.get("response")
        if isinstance(response, dict):
            body = response.get("body")
            if isinstance(body, dict):
                return body
        if "items" in node or "totalCount" in node:
            return node
        for value in node.values():
            found = _find_body(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_body(value)
            if found:
                return found
    return None


def _extract_items(body):
    if not isinstance(body, dict):
        return []
    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", items)
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _parse_response(raw):
    raw = bytes(raw or b"").strip()
    if not raw:
        raise RuntimeError("API가 빈 응답을 반환했습니다.")

    if raw.startswith((b"{", b"[")):
        data = json.loads(raw.decode("utf-8-sig"))
        header = _find_header(data) or {}
        code = str(header.get("resultCode", header.get("resultCd", "00")))
        message = str(header.get("resultMsg", header.get("resultMessage", "")))
        set_setting("last_api_result_code", code)
        set_setting("last_api_result_message", message)
        _raise_api_error(code, message)
        body = _find_body(data)
        if not isinstance(body, dict):
            return [], 0
        items = _extract_items(body)
        total = int(_num(body.get("totalCount", len(items)), len(items)))
        return items, total

    root = ET.fromstring(raw)
    code = root.findtext(".//resultCode") or root.findtext(".//resultCd") or "00"
    message = root.findtext(".//resultMsg") or root.findtext(".//resultMessage") or ""
    set_setting("last_api_result_code", str(code))
    set_setting("last_api_result_message", str(message))
    _raise_api_error(code, message)
    items = [{c.tag: (c.text or "") for c in list(item)} for item in root.findall(".//item")]
    total = int(_num(root.findtext(".//totalCount"), len(items)))
    return items, total


def _request(url, kind, timeout=45, retries=3):
    last = None
    for attempt in range(retries):
        _quota_take(kind)
        req = urllib.request.Request(url, headers={"User-Agent": "SINSUNG-G2B-DATA-VIEW/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return _parse_response(response.read())
        except ApiQuotaReached:
            raise
        except ApiRateLimited as exc:
            last = exc
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
        except G2BApiError:
            raise
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            if body:
                try:
                    return _parse_response(body)
                except (ApiQuotaReached, ApiRateLimited, G2BApiError):
                    raise
                except Exception as parsed:
                    last = parsed
            else:
                last = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise RuntimeError(f"HTTP {exc.code}: {last}") from exc
            time.sleep(1.5 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt == retries - 1:
                raise RuntimeError(f"API 네트워크 오류: {exc}") from exc
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"API 요청 실패: {last}")


def normalize_shop_item(d):
    contract_date = _date(_pick(d, "cntrctDt", "contractDt", "contractDate", "IntlCntrctDlvrReqDate", "intlCntrctDlvrReqDate", "intllCntrctDlvrReqDate"))
    delivery_req_date = _date(_pick(d, "dlvrReqRcptDate", "dlvrReqDt", "deliveryReqDt", "reqDt", "dlvrReqDate", "IntlCntrctDlvrReqDate", "intlCntrctDlvrReqDate", "intllCntrctDlvrReqDate"))
    base_date = delivery_req_date or contract_date or _date(_pick(d, "baseDt"))
    demand_org = _pick(d, "dminsttNm", "demandInsttNm", "demandOrgNm", "orderInsttNm", "insttNm")
    direct_region = _pick(d, "dminsttRgnNm", "demandRegionNm", "rgnNm", "regionName")
    address = _pick(d, "dminsttAddr", "demandInsttAddr", "dlvrDstnAddr", "addr")
    vendor = _pick(d, "corpNm", "cntrctCorpNm", "entrpsNm", "vendorNm", "supplierNm", "cntrctCorpName")
    detail_item_no = _digits(_pick(d, "dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo"))
    detail_item_name = _pick(d, "dtilPrdctClsfcNoNm", "dtilPrdctClsfcNm", "detailPrdctNm", "detailItemName", "prdctClsfcNoNm", "prdctClsfcNm")
    item_id = _digits(_pick(d, "prdctIdntNo", "goodsIdntNo", "itemId", "identificationNo"))
    item_name = _pick(d, "prdctIdntNoNm", "prdctIdntNm", "goodsIdntNm", "itemName", "prdctNm")
    model_name = _pick(d, "modelNm", "modelName", "goodsModelNm", "prdctSpecNm", "specNm")
    unit = _pick(d, "prdctUnit", "unit", "unitNm", "dlvrUnit")
    unit_price = int(round(_num(_pick(d, "prdctUprc", "unitPric", "unitPrice", "cntrctUnitPric", "cntrctPrce", "prc"))))
    quantity = _num(_pick(d, "prdctQty", "dlvrReqQty", "reqQty", "quantity", "qty"))
    calculated_amount = int(round(unit_price * quantity)) if unit_price and quantity else 0
    line_amount = int(round(_num(_pick(d, "prdctAmt", "supplyAmount", "amount", "dlvrAmt", "dlvrReqAmt", "reqAmt"))))
    amount = calculated_amount or line_amount
    contract_name = _pick(d, "dlvrReqNm", "cntrctNm", "contractNm", "deliveryReqNm", "bizNm", "dlvrReqSj")
    contract_no = str(_pick(d, "cntrctNo", "contractNo"))
    delivery_req_no = str(_pick(d, "dlvrReqNo", "deliveryReqNo", "reqNo"))
    detail_seq = str(_pick(d, "prdctSno", "dlvrReqDtlSeq", "dlvrReqDtlSn", "dlvrReqSeq", "dlvrReqChgOrd", "detailSeq", "seq"))
    bizno = str(_pick(d, "cntrctCorpBizno", "corpBizno", "bizno", "bizrno"))
    final_yn = str(_pick(d, "fnlDlvrReqYn", "lastDlvrReqYn", "finalDlvrReqYn", "finalYn", "lastYn", "fnlYn"))
    contract_method = _pick(d, "cntrctCnclsStleNm", "cntrctMthdNm", "contractMthdNm", "contractMethodNm", "cntrctMthd")
    delivery_deadline = _date(_pick(d, "dlvrTmlmtDate", "dlvrTmlmtDt", "deliveryDeadline", "dlvrDueDt", "deliveryDueDate"))
    if delivery_req_no:
        rawkey = "|".join(["DLVR", delivery_req_no, detail_seq, item_id, contract_no, vendor])
    else:
        rawkey = "|".join(["FALLBACK", base_date, demand_org, vendor, item_id, contract_no, contract_name])
    return {
        "base_date": base_date, "contract_date": contract_date, "delivery_req_date": delivery_req_date,
        "final_yn": final_yn, "demand_org": demand_org,
        "demand_region": infer_region(demand_org, address, direct_region), "top_org": normalize_top_org(demand_org),
        "contract_name": contract_name, "contract_method": contract_method, "delivery_deadline": delivery_deadline,
        "detail_item_no": detail_item_no, "detail_item_name": detail_item_name, "item_id": item_id,
        "item_name": item_name, "model_name": model_name, "unit": unit, "unit_price": unit_price,
        "quantity": quantity, "supply_amount": amount, "vendor_name": vendor, "vendor_bizno": bizno,
        "contract_no": contract_no, "delivery_req_no": delivery_req_no,
        "delivery_req_detail_seq": detail_seq,
        "source_key": hashlib.sha1(rawkey.encode("utf-8")).hexdigest(),
    }


SHOP_PROFILES = (
    ("date", "inqryBgnDate", "inqryEndDate", False),
    ("datetime", "inqryBgnDt", "inqryEndDt", True),
)


def _shop_params(start_date, end_date, page, rows, profile):
    profile_name, begin_key, end_key, with_time = profile
    begin = start_date.replace("-", "")
    end = end_date.replace("-", "")
    if with_time:
        begin += "0000"
        end += "2359"
    return profile_name, {
        "serviceKey": get_setting("api_key"), "pageNo": int(page), "numOfRows": int(rows),
        "type": "json", "inqryDiv": "1", begin_key: begin, end_key: end,
    }


def fetch_shop_page(start_date, end_date, page=1, rows=999):
    key = (get_setting("api_key") or "").strip()
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    preferred = get_setting("shop_request_profile", "")
    profiles = list(SHOP_PROFILES)
    if preferred:
        profiles.sort(key=lambda p: 0 if p[0] == preferred else 1)
    errors = []
    for profile in profiles:
        profile_name, params = _shop_params(start_date, end_date, page, rows, profile)
        url = f"{SHOP_BASE_URL}/{SHOP_OPERATION}?" + urllib.parse.urlencode(params, safe="%")
        try:
            items, total = _request(url, "shop")
            set_setting("shop_request_profile", profile_name)
            set_setting("last_shop_request_profile", profile_name)
            return items, total
        except G2BApiError as exc:
            errors.append(f"{profile_name}:{exc.code} {exc.message}".strip())
            if exc.code not in ("08", "10"):
                raise
    raise RuntimeError("납품요구상세 요청 형식 오류: " + " / ".join(errors))


def _matches(text, terms):
    value = str(text or "").casefold()
    return any(str(term).casefold() in value for term in terms)


def upsert_shop(items, target_only=True):
    count = matched = skipped = 0
    with connect() as conn:
        for raw in items:
            x = normalize_shop_item(raw)
            if not x["base_date"] or not x["item_id"]:
                skipped += 1
                continue
            if target_only and x["detail_item_no"] not in SHOP_DETAIL_ITEM_NOS:
                continue
            matched += 1
            conn.execute("""
                INSERT INTO shopping_contracts(
                    base_date,contract_date,delivery_req_date,final_yn,demand_org,demand_region,top_org,
                    contract_name,contract_method,delivery_deadline,detail_item_no,detail_item_name,item_id,item_name,model_name,unit,
                    unit_price,quantity,supply_amount,vendor_name,vendor_bizno,contract_no,delivery_req_no,delivery_req_detail_seq,source_key,is_sample
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT DO UPDATE SET
                    base_date=excluded.base_date,contract_date=excluded.contract_date,delivery_req_date=excluded.delivery_req_date,
                    final_yn=excluded.final_yn,demand_org=excluded.demand_org,demand_region=excluded.demand_region,
                    top_org=excluded.top_org,contract_name=excluded.contract_name,contract_method=excluded.contract_method,
                    delivery_deadline=excluded.delivery_deadline,detail_item_no=excluded.detail_item_no,
                    detail_item_name=excluded.detail_item_name,item_id=excluded.item_id,item_name=excluded.item_name,
                    model_name=excluded.model_name,unit=excluded.unit,unit_price=excluded.unit_price,
                    quantity=excluded.quantity,supply_amount=excluded.supply_amount,vendor_name=excluded.vendor_name,
                    vendor_bizno=excluded.vendor_bizno,contract_no=excluded.contract_no,
                    delivery_req_no=excluded.delivery_req_no,delivery_req_detail_seq=excluded.delivery_req_detail_seq,
                    is_sample=0,updated_at=CURRENT_TIMESTAMP
            """, tuple(x[k] for k in [
                "base_date","contract_date","delivery_req_date","final_yn","demand_org","demand_region","top_org",
                "contract_name","contract_method","delivery_deadline","detail_item_no","detail_item_name","item_id","item_name","model_name","unit",
                "unit_price","quantity","supply_amount","vendor_name","vendor_bizno","contract_no","delivery_req_no","delivery_req_detail_seq","source_key"
            ]))
            count += 1
    return count, matched, skipped


def _date_chunks(start_date, end_date, max_days=31):
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    if start > end:
        start, end = end, start
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=max_days - 1), end)
        yield cur.isoformat(), chunk_end.isoformat()
        cur = chunk_end + dt.timedelta(days=1)


def sync_shopping_period(start_date, end_date, max_pages=2000):
    with SHOP_LOCK:
        log_id = new_sync_log("SHOPPING", start_date, end_date)
        processed = seen = matched = skipped = 0
        try:
            for chunk_start, chunk_end in _date_chunks(start_date, end_date, 31):
                page = 1
                chunk_seen = 0
                total = None
                while page <= max_pages:
                    items, total = fetch_shop_page(chunk_start, chunk_end, page=page, rows=999)
                    if page == 1 and total and math.ceil(total / 999) > max_pages:
                        raise IncompleteSyncError(f"{chunk_start}~{chunk_end} 원본 {total:,}건으로 페이지 한도 {max_pages:,} 초과")
                    if not items:
                        break
                    if not get_setting("last_shop_first_fields", ""):
                        set_setting("last_shop_first_fields", ", ".join(sorted(str(k) for k in items[0].keys())))
                    saved_now, matched_now, skipped_now = upsert_shop(items, target_only=True)
                    processed += saved_now; matched += matched_now; skipped += skipped_now
                    seen += len(items); chunk_seen += len(items)
                    set_setting("last_shop_raw_count", str(seen))
                    set_setting("last_shop_matched_count", str(matched))
                    set_setting("last_shop_saved_count", str(processed))
                    set_setting("last_shop_skipped_count", str(skipped))
                    if total is not None and chunk_seen >= total:
                        break
                    page += 1
                    time.sleep(0.12)
                if total and chunk_seen < total:
                    raise IncompleteSyncError(f"{chunk_start}~{chunk_end}: 원본 {total:,}건 중 {chunk_seen:,}건만 조회")
            now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            profile = get_setting("last_shop_request_profile", "-")
            set_setting("last_sync", now)
            set_setting("last_shop_error", "")
            result = (f"{start_date} ~ {end_date}: 원본 {seen:,}건 / 세부품명번호 대상 {matched:,}건 / "
                      f"저장·갱신 {processed:,}건 / 필수값 누락 {skipped:,}건 / 요청형식 {profile}")
            set_setting("last_sync_result", result)
            finish_sync_log(log_id, "OK", processed, result)
            return processed
        except Exception as exc:
            set_setting("last_shop_error", str(exc))
            finish_sync_log(log_id, "ERROR", processed, str(exc))
            raise


def normalize_bid(d, business_type_override=""):
    notice_no = str(_pick(d, "bidNtceNo", "bidNoticeNo"))
    order = str(_pick(d, "bidNtceOrd", "bidNoticeOrd", default="000"))
    notice_date = _date(_pick(d, "bidNtceDt", "bidNoticeDate"))
    close_date = _date(_pick(d, "bidClseDt", "bidCloseDate"))
    open_date = _date(_pick(d, "opengDt", "openDate"))
    notice_name = _pick(d, "bidNtceNm", "bidNoticeName")
    notice_org = _pick(d, "ntceInsttNm", "noticeInsttNm", "noticeOrgName")
    demand_org = _pick(d, "dminsttNm", "demandInsttNm", "demandOrgName")
    direct_region = _pick(d, "prtcptPsblRgnNm", "dminsttRgnNm", "regionName")
    business_type = business_type_override or _pick(d, "bsnsDivNm", "businessTypeName", default="")
    method = _pick(d, "bidMethdNm", "bidMethodNm")
    budget = int(round(_num(_pick(d, "asignBdgtAmt", "budgetAmount", "bdgtAmt"))))
    estimated = int(round(_num(_pick(d, "presmptPrce", "estimatedPrice"))))
    url = _pick(d, "bidNtceDtlUrl", "detailUrl")
    prefix = "S|" if "용역" in business_type else ""
    rawkey = f"{prefix}{notice_no}|{order}"
    return {"notice_no":notice_no,"notice_order":order,"notice_date":notice_date,"close_date":close_date,
            "open_date":open_date,"notice_name":notice_name,"notice_org":notice_org,"demand_org":demand_org,
            "region":infer_region(demand_org, notice_org, direct_region),"business_type":business_type,
            "method_name":method,"budget_amount":budget,"estimated_price":estimated,"url":url,
            "source_key":hashlib.sha1(rawkey.encode("utf-8")).hexdigest()}


def fetch_bid_page(start_date, end_date, page=1, rows=999, operation="getBidPblancListInfoThng"):
    key = (get_setting("api_key") or "").strip()
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    params = {"serviceKey": key, "pageNo": int(page), "numOfRows": int(rows), "type": "json", "inqryDiv": "1",
              "inqryBgnDt": start_date.replace("-", "") + "0000", "inqryEndDt": end_date.replace("-", "") + "2359"}
    return _request(f"{BID_BASE_URL}/{operation}?" + urllib.parse.urlencode(params, safe="%"), "bid")


def upsert_bids(items, target_terms=BID_TARGETS, business_type_override="물품"):
    count = 0
    with connect() as conn:
        for raw in items:
            x = normalize_bid(raw, business_type_override=business_type_override)
            if not x["notice_no"]:
                continue
            if target_terms and not _matches(x["notice_name"], target_terms):
                continue
            conn.execute("""
                INSERT INTO bids(notice_no,notice_order,notice_date,close_date,open_date,notice_name,notice_org,demand_org,region,
                    business_type,method_name,budget_amount,estimated_price,url,source_key,is_sample)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT DO UPDATE SET
                    notice_date=excluded.notice_date,close_date=excluded.close_date,open_date=excluded.open_date,
                    notice_name=excluded.notice_name,notice_org=excluded.notice_org,demand_org=excluded.demand_org,
                    region=excluded.region,business_type=excluded.business_type,method_name=excluded.method_name,
                    budget_amount=excluded.budget_amount,estimated_price=excluded.estimated_price,url=excluded.url,
                    is_sample=0,updated_at=CURRENT_TIMESTAMP
            """, tuple(x[k] for k in ["notice_no","notice_order","notice_date","close_date","open_date","notice_name",
                "notice_org","demand_org","region","business_type","method_name","budget_amount","estimated_price","url","source_key"]))
            count += 1
    return count


def _sync_bid_operation(start_date, end_date, operation, target_terms, business_type, log_type, max_pages=500):
    log_id = new_sync_log(log_type, start_date, end_date)
    processed = 0
    try:
        for chunk_start, chunk_end in _date_chunks(start_date, end_date, 28):
            page = 1; seen = 0; total = None
            while page <= max_pages:
                items, total = fetch_bid_page(chunk_start, chunk_end, page=page, rows=999, operation=operation)
                if page == 1 and total and math.ceil(total / 999) > max_pages:
                    raise IncompleteSyncError(f"{log_type} 원본 {total:,}건으로 페이지 한도 초과")
                if not items:
                    break
                processed += upsert_bids(items, target_terms=target_terms, business_type_override=business_type)
                seen += len(items)
                if total is not None and seen >= total:
                    break
                page += 1
                time.sleep(0.12)
            if total and seen < total:
                raise IncompleteSyncError(f"{log_type} {chunk_start}~{chunk_end}: 원본 {total:,}건 중 {seen:,}건만 조회")
        finish_sync_log(log_id, "OK", processed, f"{start_date} ~ {end_date}: {processed:,}건 저장·갱신")
        return processed
    except Exception as exc:
        finish_sync_log(log_id, "ERROR", processed, str(exc))
        raise


def sync_bids_period(start_date, end_date, max_pages=500):
    with BID_LOCK:
        count = _sync_bid_operation(start_date, end_date, "getBidPblancListInfoThng", BID_TARGETS, "물품", "BIDS", max_pages=max_pages)
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_setting("last_bid_sync", now)
        set_setting("last_bid_sync_result", f"{start_date} ~ {end_date}: 조명 물품 입찰공고 {count:,}건 저장·갱신")
        return count


def sync_services_period(start_date, end_date, max_pages=500):
    with BID_LOCK:
        count = _sync_bid_operation(start_date, end_date, "getBidPblancListInfoServc", SERVICE_TARGETS, "용역", "SERVICES", max_pages=max_pages)
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_setting("last_service_sync", now)
        set_setting("last_service_sync_result", f"{start_date} ~ {end_date}: 조명/전기 관련 용역공고 {count:,}건 저장·갱신")
        return count


def test_shopping_api():
    end = dt.date.today()
    start = end - dt.timedelta(days=14)
    set_setting("last_shop_first_fields", "")
    items, total = fetch_shop_page(start.isoformat(), end.isoformat(), 1, 100)
    if not items and int(total or 0) == 0:
        raise RuntimeError(f"납품요구상세 조회가 0건입니다({start.isoformat()} ~ {end.isoformat()}). "
                           f"요청형식={get_setting('last_shop_request_profile', '-')}, "
                           f"resultCode={get_setting('last_api_result_code', '-')}")
    set_setting("last_shop_first_fields", ", ".join(sorted(str(k) for k in items[0].keys())) if items else "")
    set_setting("last_shop_raw_count", str(total or len(items)))
    return len(items), int(total or len(items))


def backfill_three_years(progress=None):
    set_setting("backfill_status", "비활성")
    set_setting("backfill_progress", "0")
    set_setting("backfill_message", "2.0에서는 과거 구축을 비활성화했습니다. 최근 실데이터 수집 검증 후 별도 안정 버전에서 활성화합니다.")
    raise RuntimeError("2.0에서는 과거자료 구축을 실행하지 않습니다. 최근 실데이터 수집 검증이 먼저입니다.")
