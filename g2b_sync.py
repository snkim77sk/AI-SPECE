"""조달청 OpenAPI 수집 모듈 - v2.3 reviewed.

공식 서비스 기본값
- 나라장터쇼핑몰 품목정보: /1230000/at/ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList
- 나라장터 입찰공고정보: /1230000/ad/BidPublicInfoService/getBidPblancListInfoThng
- 용역공고: /1230000/ad/BidPublicInfoService/getBidPblancListInfoServc

운영 안정화 포인트
- 재시도/지수 백오프
- 일일 API 호출 안전한도(기본 900회/서비스)
- 페이지 누락 감지
- 3년 구축 중 호출한도 도달 시 중단 후 재개 가능
- 동시수집 방지
- 쇼핑몰 조회기간 월 단위 분할(조달청 조회기간 상한 회피)
- 쇼핑몰 inqryDiv 자동 판별
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

LED_DETAIL_ITEM_NOS = frozenset({
    '3910161601', '3911160301', '3911160501', '3911160801',
    '3911161101', '3911210201', '3911210301',
})
SOLAR_PANEL_DETAIL_ITEM_NOS = frozenset({'2611160701', '3912110101'})
POLE_DETAIL_ITEM_NOS = frozenset({'3911152601', '3911152602', '3911152607'})
SHOP_DETAIL_ITEM_NOS = LED_DETAIL_ITEM_NOS | SOLAR_PANEL_DETAIL_ITEM_NOS | POLE_DETAIL_ITEM_NOS
BID_TARGETS = ('LED', '조명', '가로등', '보안등', '투광등', '다운라이트', '경관', '보행신호')
SERVICE_TARGETS = ('LED', '조명', '전기', '경관', '가로등', '보안등', '조명설계', '전기설계')

# 쇼핑몰 조회구분 후보. 0건 응답이 오면 순서대로 시도한 뒤 성공한 값을 설정에 저장한다.
SHOP_INQRY_DIV_CANDIDATES = ('1', '2', '3')

SHOP_LOCK = threading.RLock()
BID_LOCK = threading.RLock()


class ApiQuotaReached(RuntimeError):
    pass


class ApiRateLimited(RuntimeError):
    pass


class IncompleteSyncError(RuntimeError):
    pass


def _pick(d, *names, default=''):
    for n in names:
        v = d.get(n) if isinstance(d, dict) else None
        if v not in (None, ''):
            return v
    return default


def _num(v, default=0):
    try:
        return float(str(v).replace(',', '').strip())
    except Exception:
        return default


def _date(v):
    s = ''.join(ch for ch in str(v or '') if ch.isdigit())
    if len(s) >= 8:
        return f'{s[:4]}-{s[4:6]}-{s[6:8]}'
    return str(v or '')[:10]


def normalize_region(value=''):
    text = str(value or '').strip()
    if not text:
        return ''
    pairs = [
        ('서울','서울특별시'),('부산','부산광역시'),('대구','대구광역시'),('인천','인천광역시'),
        ('광주','광주광역시'),('대전','대전광역시'),('울산','울산광역시'),('세종','세종특별자치시'),
        ('경기','경기도'),('강원','강원특별자치도'),('충북','충청북도'),('충청','충청북도'),
        ('충남','충청남도'),('전북','전북특별자치도'),('전남','전라남도'),('경북','경상북도'),
        ('경남','경상남도'),('제주','제주특별자치도')
    ]
    # 긴 정식명 우선
    for _, full in pairs:
        if full in text:
            return full
    for short, full in pairs:
        if short in text:
            return full
    return ''


def infer_region(org_name='', address='', direct_region=''):
    return normalize_region(direct_region) or normalize_region(address) or normalize_region(org_name)


def normalize_top_org(org):
    s = (org or '').strip()
    if not s:
        return ''
    if '한국토지주택공사' in s or s.startswith('LH'):
        return '한국토지주택공사'
    if '서울주택도시공사' in s or s.startswith('SH'):
        return '서울주택도시공사'
    if '인천광역시교육청' in s:
        return '인천광역시교육청'
    return s


def _quota_keys(kind):
    k = 'shop' if kind == 'shop' else 'bid'
    return f'api_calls_{k}_date', f'api_calls_{k}_count'


def _quota_take(kind):
    """실제 HTTP 호출 직전에 1회 차감한다.

    공공데이터포털 개발계정 기본 1,000회보다 여유를 둔 900회를 기본 안전한도로 사용한다.
    운영계정 증설 후 G2B_API_DAILY_LIMIT / api_daily_limit 값을 높일 수 있다.
    """
    limit = max(1, int(float(get_setting('api_daily_limit', '900') or 900)))
    dkey, ckey = _quota_keys(kind)
    today = dt.date.today().isoformat()
    saved_day = get_setting(dkey, '')
    count = int(float(get_setting(ckey, '0') or 0)) if saved_day == today else 0
    if count >= limit:
        raise ApiQuotaReached(f'{kind.upper()} API 일일 안전한도 {limit:,}회에 도달했습니다. 다음 날 자동/수동 재개하세요.')
    set_setting(dkey, today)
    set_setting(ckey, str(count + 1))
    return count + 1, limit


def api_usage(kind):
    dkey, ckey = _quota_keys(kind)
    today = dt.date.today().isoformat()
    count = int(float(get_setting(ckey, '0') or 0)) if get_setting(dkey, '') == today else 0
    limit = max(1, int(float(get_setting('api_daily_limit', '900') or 900)))
    return count, limit


def _parse_response(raw):
    raw = raw.strip()
    if not raw:
        return [], 0
    if raw.startswith(b'{') or raw.startswith(b'['):
        data = json.loads(raw.decode('utf-8-sig'))
        header = data.get('response', {}).get('header', data.get('header', {})) if isinstance(data, dict) else {}
        if isinstance(header, dict):
            code = str(header.get('resultCode', header.get('resultCd', '00')))
            msg = header.get('resultMsg', header.get('resultMessage', ''))
            if code not in ('00', '0', ''):
                if code == '22':
                    raise ApiQuotaReached(f'API 일일 호출 제한 {code}: {msg}')
                if code == '23':
                    raise ApiRateLimited(f'API 초당 호출 제한 {code}: {msg}')
                raise RuntimeError(f'API 오류 {code}: {msg}')
        body = data.get('response', {}).get('body', data.get('body', data)) if isinstance(data, dict) else data
        if not isinstance(body, dict):
            return [], 0
        items = body.get('items', [])
        if isinstance(items, dict):
            items = items.get('item', items)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            items = []
        return items, int(_num(body.get('totalCount', len(items)), len(items)))

    root = ET.fromstring(raw)
    result_code = root.findtext('.//resultCode') or root.findtext('.//resultCd') or '00'
    result_msg = root.findtext('.//resultMsg') or root.findtext('.//resultMessage') or ''
    if str(result_code) not in ('00', '0', ''):
        if str(result_code) == '22':
            raise ApiQuotaReached(f'API 일일 호출 제한 {result_code}: {result_msg}')
        if str(result_code) == '23':
            raise ApiRateLimited(f'API 초당 호출 제한 {result_code}: {result_msg}')
        raise RuntimeError(f'API 오류 {result_code}: {result_msg}')
    items = [{c.tag: (c.text or '') for c in list(item)} for item in root.findall('.//item')]
    total = root.findtext('.//totalCount')
    return items, int(_num(total, len(items)))


def _request(url, kind, timeout=45, retries=3):
    last = None
    for attempt in range(retries):
        _quota_take(kind)
        req = urllib.request.Request(url, headers={'User-Agent': 'LightingSketch-G2B-Dashboard/2.3-reviewed'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return _parse_response(r.read())
        except ApiQuotaReached:
            raise
        except ApiRateLimited as exc:
            last = exc
            if attempt == retries - 1:
                raise RuntimeError(f'API 초당 호출 제한이 반복됩니다: {exc}') from exc
            time.sleep(2.0 * (attempt + 1))
            continue
        except urllib.error.HTTPError as exc:
            body = b''
            try:
                body = exc.read()
            except Exception:
                pass
            # 공공데이터 게이트웨이에서 JSON/XML 오류를 HTTP 4xx/5xx와 함께 주는 경우 우선 파싱
            if body:
                try:
                    return _parse_response(body)
                except ApiQuotaReached:
                    raise
                except ApiRateLimited as parsed:
                    last = parsed
                    if attempt < retries - 1:
                        time.sleep(2.0 * (attempt + 1))
                        continue
                except Exception as parsed:
                    last = parsed
            else:
                last = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise RuntimeError(f'HTTP {exc.code}: {last}') from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt == retries - 1:
                raise RuntimeError(f'API 네트워크 오류: {exc}') from exc
        time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f'API 요청 실패: {last}')


def normalize_shop_item(d):
    contract_date = _date(_pick(d, 'cntrctDt', 'contractDt', 'contractDate'))
    delivery_req_date = _date(_pick(d, 'dlvrReqDt', 'deliveryReqDt', 'reqDt', 'dlvrReqDate'))
    base_date = delivery_req_date or contract_date or _date(_pick(d, 'baseDt'))
    demand_org = _pick(d, 'dminsttNm', 'demandInsttNm', 'demandOrgNm', 'orderInsttNm', 'insttNm')
    direct_region = _pick(d, 'dminsttRgnNm', 'demandRegionNm', 'rgnNm', 'regionName')
    address = _pick(d, 'dminsttAddr', 'demandInsttAddr', 'dlvrDstnAddr', 'addr')
    vendor = _pick(d, 'corpNm', 'cntrctCorpNm', 'entrpsNm', 'vendorNm', 'supplierNm', 'cntrctCorpName')
    detail_item_no = str(_pick(d, 'dtilPrdctClsfcNo', 'detailPrdctClsfcNo', 'detailItemNo', 'prdctClsfcNo'))
    detail_item = _pick(d, 'dtilPrdctClsfcNm', 'dtilPrdctClsfcNoNm', 'detailPrdctNm', 'detailItemName', 'prdctClsfcNm')
    item_id = str(_pick(d, 'prdctIdntNo', 'goodsIdntNo', 'itemId', 'identificationNo'))
    item_name = _pick(d, 'prdctIdntNm', 'goodsIdntNm', 'itemName', 'prdctNm')
    model_name = _pick(d, 'modelNm', 'modelName', 'goodsModelNm', 'prdctSpecNm', 'specNm')
    unit = _pick(d, 'prdctUnit', 'unit', 'unitNm', 'dlvrUnit')
    unit_price = int(round(_num(_pick(d, 'unitPric', 'unitPrice', 'cntrctUnitPric', 'cntrctPrce', 'prc'))))
    q = _num(_pick(d, 'dlvrReqQty', 'reqQty', 'quantity', 'qty'))
    # 상세 행은 언제나 품목 단가×수량을 우선한다. 납품요구 전체 금액 필드는
    # 여러 상세 행에 반복되는 경우가 있어 합계를 부풀릴 수 있다.
    calculated_amount = int(round(unit_price * q)) if unit_price and q else 0
    amount = calculated_amount or int(round(_num(_pick(d, 'supplyAmount', 'amount'))))
    contract_name = _pick(d, 'cntrctNm', 'contractNm', 'dlvrReqNm', 'deliveryReqNm', 'bizNm', 'dlvrReqSj')
    contract_no = str(_pick(d, 'cntrctNo', 'contractNo'))
    delivery_req_no = str(_pick(d, 'dlvrReqNo', 'deliveryReqNo', 'reqNo'))
    detail_seq = str(_pick(d, 'dlvrReqDtlSeq', 'dlvrReqDtlSn', 'dlvrReqSeq', 'detailSeq', 'seq'))
    bizno = str(_pick(d, 'corpBizno', 'cntrctCorpBizno', 'bizno', 'bizrno'))
    final_yn = str(_pick(d, 'lastDlvrReqYn', 'finalDlvrReqYn', 'finalYn', 'lastYn', 'fnlYn'))
    contract_method = _pick(d, 'cntrctMthdNm', 'contractMthdNm', 'contractMethodNm', 'cntrctMthd')
    delivery_deadline = _date(_pick(d, 'dlvrTmlmtDt', 'deliveryDeadline', 'dlvrDueDt', 'deliveryDueDate'))
    # 금액/수량은 변경될 수 있으므로 식별키에 넣지 않는다. 납품요구번호+상세순번+식별번호를 우선 사용.
    if delivery_req_no:
        rawkey = '|'.join(['DLVR', delivery_req_no, detail_seq, item_id, contract_no, vendor])
    else:
        rawkey = '|'.join(['FALLBACK', base_date, demand_org, vendor, item_id, contract_no, contract_name])
    return {
        'base_date': base_date,
        'contract_date': contract_date,
        'delivery_req_date': delivery_req_date,
        'final_yn': final_yn,
        'demand_org': demand_org,
        'demand_region': infer_region(demand_org, address, direct_region),
        'top_org': normalize_top_org(demand_org),
        'contract_name': contract_name,
        'contract_method': contract_method,
        'delivery_deadline': delivery_deadline,
        'detail_item_no': detail_item_no,
        'detail_item_name': detail_item,
        'item_id': item_id,
        'item_name': item_name,
        'model_name': model_name,
        'unit': unit,
        'unit_price': unit_price,
        'quantity': q,
        'supply_amount': amount,
        'vendor_name': vendor,
        'vendor_bizno': bizno,
        'contract_no': contract_no,
        'delivery_req_no': delivery_req_no,
        'delivery_req_detail_seq': detail_seq,
        'source_key': hashlib.sha1(rawkey.encode('utf-8')).hexdigest(),
    }


def shop_inqry_div():
    """현재 사용 중인 쇼핑몰 조회구분 값."""
    return str(get_setting('shop_inqry_div', '1') or '1')


def fetch_shop_page(start_date, end_date, page=1, rows=999, inqry_div=None):
    key = get_setting('api_key')
    base = get_setting('shop_api_base_url').rstrip('/')
    op = get_setting('shop_api_operation').strip('/')
    if not key:
        raise RuntimeError('공공데이터포털 서비스키가 설정되지 않았습니다.')
    if not op:
        raise RuntimeError('종합쇼핑몰 납품요구상세 오퍼레이션명이 비어 있습니다. getDlvrReqDtlInfoList를 사용하세요.')
    div = str(inqry_div) if inqry_div not in (None, '') else shop_inqry_div()
    params = {
        'serviceKey': key,
        'numOfRows': rows,
        'pageNo': page,
        'type': 'json',
        'inqryDiv': div,
        'inqryBgnDate': start_date.replace('-', ''),
        'inqryEndDate': end_date.replace('-', ''),
    }
    return _request(f'{base}/{op}?' + urllib.parse.urlencode(params, safe='%'), 'shop')


def probe_shop_inqry_div(start_date, end_date, rows=999):
    """0건 응답이 나올 때 inqryDiv 후보를 순회하며 실제로 데이터가 오는 값을 찾는다.

    조달청 오퍼레이션마다 조회구분 코드 의미가 달라 1이 날짜 기준이 아닌 경우가 있다.
    성공한 값은 shop_inqry_div 설정에 저장해 이후 호출에 계속 사용한다.
    시도 내역은 last_shop_probe 설정에 남겨 설정 화면에서 확인할 수 있다.
    """
    current = shop_inqry_div()
    tried = []
    for div in SHOP_INQRY_DIV_CANDIDATES:
        if div == current:
            tried.append(f'{div}:0건(기본값)')
            continue
        try:
            items, total = fetch_shop_page(start_date, end_date, 1, rows, inqry_div=div)
        except ApiQuotaReached:
            set_setting('last_shop_probe', ' | '.join(tried + [f'{div}:호출한도']))
            raise
        except Exception as exc:
            tried.append(f'{div}:오류({exc})')
            continue
        tried.append(f'{div}:{len(items)}건(total {total})')
        if items:
            set_setting('shop_inqry_div', div)
            set_setting('last_shop_probe', ' | '.join(tried) + f' → inqryDiv={div} 채택')
            return div, items, total
    set_setting(
        'last_shop_probe',
        ' | '.join(tried) + ' → 모든 조회구분에서 0건. 활용신청 승인 상태를 확인하세요.'
    )
    return None, [], 0


def _matches(text, terms):
    text = str(text or '').casefold()
    return any(str(k).casefold() in text for k in terms)


def month_chunks(start_date, end_date):
    """조회 구간을 월 단위로 나눈다.

    조달청 API는 조회기간 상한이 있어 장기간을 한 번에 요청하면 0건이나 오류가 난다.
    입찰 수집은 27일 단위로 나누고 있었으나 쇼핑몰 수집은 전 구간을 한 번에
    요청하고 있었다. 같은 방식으로 맞춘다.
    """
    s = dt.date.fromisoformat(start_date)
    e = dt.date.fromisoformat(end_date)
    if s > e:
        s, e = e, s
    out = []
    cur = s
    while cur <= e:
        nxt = dt.date(cur.year + 1, 1, 1) if cur.month == 12 else dt.date(cur.year, cur.month + 1, 1)
        out.append((cur, min(nxt - dt.timedelta(days=1), e)))
        cur = nxt
    return out


def upsert_shop(items, target_only=True):
    count = 0
    matched = 0
    skipped = 0
    with connect() as conn:
        for raw in items:
            x = normalize_shop_item(raw)
            if not x['base_date'] or not x['item_id']:
                skipped += 1
                continue
            if target_only and x['detail_item_no'] not in SHOP_DETAIL_ITEM_NOS:
                continue
            matched += 1
            conn.execute('''
                INSERT INTO shopping_contracts(
                    base_date,contract_date,delivery_req_date,final_yn,demand_org,demand_region,top_org,
                    contract_name,contract_method,delivery_deadline,detail_item_no,detail_item_name,item_id,item_name,model_name,unit,
                    unit_price,quantity,supply_amount,vendor_name,vendor_bizno,contract_no,delivery_req_no,delivery_req_detail_seq,source_key,is_sample
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT DO UPDATE SET
                    base_date=excluded.base_date,contract_date=excluded.contract_date,delivery_req_date=excluded.delivery_req_date,
                    final_yn=excluded.final_yn,demand_org=excluded.demand_org,demand_region=excluded.demand_region,
                    top_org=excluded.top_org,contract_name=excluded.contract_name,detail_item_name=excluded.detail_item_name,
                    contract_method=excluded.contract_method,delivery_deadline=excluded.delivery_deadline,
                    detail_item_no=excluded.detail_item_no,
                    item_id=excluded.item_id,item_name=excluded.item_name,model_name=excluded.model_name,
                    unit=excluded.unit,
                    unit_price=excluded.unit_price,quantity=excluded.quantity,supply_amount=excluded.supply_amount,
                    vendor_name=excluded.vendor_name,vendor_bizno=excluded.vendor_bizno,contract_no=excluded.contract_no,
                    delivery_req_no=excluded.delivery_req_no,delivery_req_detail_seq=excluded.delivery_req_detail_seq,
                    is_sample=0,updated_at=CURRENT_TIMESTAMP
            ''', tuple(x[k] for k in [
                'base_date','contract_date','delivery_req_date','final_yn','demand_org','demand_region','top_org',
                'contract_name','contract_method','delivery_deadline','detail_item_no','detail_item_name','item_id','item_name','model_name','unit',
                'unit_price','quantity','supply_amount','vendor_name','vendor_bizno','contract_no','delivery_req_no','delivery_req_detail_seq','source_key'
            ]))
            count += 1
    return count, matched, skipped


def sync_shopping_period(start_date, end_date, max_pages=2000):
    """쇼핑몰 납품요구 상세 수집. 조회 구간을 월 단위로 나눠 호출한다."""
    with SHOP_LOCK:
        log_id = new_sync_log('SHOPPING', start_date, end_date)
        processed = 0
        seen = 0
        matched = 0
        skipped = 0
        probed = False
        rows_per_page = 999
        chunks = month_chunks(start_date, end_date)
        try:
            for chunk_index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
                cs = chunk_start.isoformat()
                ce = chunk_end.isoformat()
                page = 1
                total = None
                chunk_seen = 0
                set_setting('last_shop_chunk', f'{cs} ~ {ce} ({chunk_index}/{len(chunks)})')
                while page <= max_pages:
                    items, total = fetch_shop_page(cs, ce, page=page, rows=rows_per_page)
                    # 첫 구간 첫 페이지가 비어 있으면 조회구분 값을 한 번만 자동 판별한다.
                    if page == 1 and not items and not probed:
                        probed = True
                        found, probe_items, probe_total = probe_shop_inqry_div(cs, ce, rows=rows_per_page)
                        if found:
                            items, total = probe_items, probe_total
                    if page == 1 and total and math.ceil(total / rows_per_page) > max_pages:
                        raise IncompleteSyncError(
                            f'{cs}~{ce} 원본 {total:,}건으로 {math.ceil(total/rows_per_page):,}페이지가 필요해 '
                            f'안전한도 {max_pages:,}페이지를 초과합니다. 기간을 더 짧게 수집하세요.'
                        )
                    if not items:
                        break
                    saved_now, matched_now, skipped_now = upsert_shop(items, target_only=True)
                    processed += saved_now
                    matched += matched_now
                    skipped += skipped_now
                    if seen == 0 and items:
                        set_setting('last_shop_first_fields', ', '.join(sorted(str(k) for k in items[0].keys())))
                    chunk_seen += len(items)
                    seen += len(items)
                    set_setting('last_shop_raw_count', str(seen))
                    set_setting('last_shop_matched_count', str(matched))
                    set_setting('last_shop_saved_count', str(processed))
                    set_setting('last_shop_skipped_count', str(skipped))
                    if total is not None and chunk_seen >= total:
                        break
                    page += 1
                    time.sleep(0.15)
                if total and chunk_seen < total:
                    raise IncompleteSyncError(
                        f'{cs}~{ce}: 원본 {total:,}건 중 {chunk_seen:,}건만 조회했습니다.'
                    )
            now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            set_setting('last_sync', now)
            set_setting('last_shop_error', '')
            set_setting('last_shop_chunk', '')
            set_setting('last_sync_result', (
                f'{start_date} ~ {end_date} ({len(chunks)}개 구간): 원본 {seen:,}건 / 조명 대상 {matched:,}건 / '
                f'저장·갱신 {processed:,}건 / 필수값 누락 {skipped:,}건 · inqryDiv={shop_inqry_div()}'
            ))
            finish_sync_log(log_id, 'OK', processed, get_setting('last_sync_result'))
            return processed
        except ApiQuotaReached as e:
            finish_sync_log(log_id, 'PAUSED', processed, str(e))
            raise
        except Exception as e:
            set_setting('last_shop_error', str(e))
            finish_sync_log(log_id, 'ERROR', processed, str(e))
            raise


def normalize_bid(d, business_type_override=''):
    notice_no = str(_pick(d, 'bidNtceNo', 'bidNoticeNo'))
    order = str(_pick(d, 'bidNtceOrd', 'bidNoticeOrd', default='000'))
    notice_date = _date(_pick(d, 'bidNtceDt', 'bidNoticeDate'))
    close_date = _date(_pick(d, 'bidClseDt', 'bidCloseDate'))
    open_date = _date(_pick(d, 'opengDt', 'openDate'))
    notice_name = _pick(d, 'bidNtceNm', 'bidNoticeName')
    notice_org = _pick(d, 'ntceInsttNm', 'noticeInsttNm', 'noticeOrgName')
    demand_org = _pick(d, 'dminsttNm', 'demandInsttNm', 'demandOrgName')
    direct_region = _pick(d, 'prtcptPsblRgnNm', 'dminsttRgnNm', 'regionName')
    business_type = business_type_override or _pick(d, 'bsnsDivNm', 'businessTypeName', default='')
    method = _pick(d, 'bidMethdNm', 'bidMethodNm')
    budget = int(round(_num(_pick(d, 'asignBdgtAmt', 'budgetAmount', 'bdgtAmt'))))
    estimated = int(round(_num(_pick(d, 'presmptPrce', 'estimatedPrice'))))
    url = _pick(d, 'bidNtceDtlUrl', 'detailUrl')
    prefix = 'S|' if '용역' in business_type else ''
    raw = f'{prefix}{notice_no}|{order}'
    return dict(
        notice_no=notice_no, notice_order=order, notice_date=notice_date, close_date=close_date, open_date=open_date,
        notice_name=notice_name, notice_org=notice_org, demand_org=demand_org,
        region=infer_region(demand_org, notice_org, direct_region),
        business_type=business_type, method_name=method, budget_amount=budget, estimated_price=estimated, url=url,
        source_key=hashlib.sha1(raw.encode()).hexdigest()
    )


def fetch_bid_page(start_date, end_date, page=1, rows=999, operation='getBidPblancListInfoThng'):
    key = get_setting('api_key')
    if not key:
        raise RuntimeError('공공데이터포털 서비스키가 설정되지 않았습니다.')
    base = get_setting('bid_api_base_url').rstrip('/')
    params = {
        'serviceKey': key,
        'pageNo': page,
        'numOfRows': rows,
        'type': 'json',
        'inqryDiv': '1',
        'inqryBgnDt': start_date.replace('-', '') + '0000',
        'inqryEndDt': end_date.replace('-', '') + '2359'
    }
    return _request(f'{base}/{operation}?' + urllib.parse.urlencode(params, safe='%'), 'bid')


def upsert_bids(items, target_terms=BID_TARGETS, business_type_override='물품'):
    count = 0
    with connect() as conn:
        for raw in items:
            x = normalize_bid(raw, business_type_override=business_type_override)
            if not x['notice_no']:
                continue
            if target_terms and not _matches(x['notice_name'], target_terms):
                continue
            conn.execute('''
                INSERT INTO bids(notice_no,notice_order,notice_date,close_date,open_date,notice_name,notice_org,demand_org,region,
                    business_type,method_name,budget_amount,estimated_price,url,source_key,is_sample)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT DO UPDATE SET
                    notice_date=excluded.notice_date,close_date=excluded.close_date,open_date=excluded.open_date,
                    notice_name=excluded.notice_name,notice_org=excluded.notice_org,demand_org=excluded.demand_org,
                    region=excluded.region,business_type=excluded.business_type,method_name=excluded.method_name,
                    budget_amount=excluded.budget_amount,estimated_price=excluded.estimated_price,url=excluded.url,
                    is_sample=0,updated_at=CURRENT_TIMESTAMP
            ''', tuple(x[k] for k in [
                'notice_no','notice_order','notice_date','close_date','open_date','notice_name','notice_org','demand_org','region',
                'business_type','method_name','budget_amount','estimated_price','url','source_key'
            ]))
            count += 1
    return count


def _sync_bid_operation(start_date, end_date, operation, target_terms, business_type, log_type, max_pages=500):
    log_id = new_sync_log(log_type, start_date, end_date)
    processed = 0
    try:
        s = dt.date.fromisoformat(start_date)
        e = dt.date.fromisoformat(end_date)
        cur = s
        while cur <= e:
            chunk_end = min(cur + dt.timedelta(days=27), e)
            page, seen, total = 1, 0, None
            while page <= max_pages:
                items, total = fetch_bid_page(cur.isoformat(), chunk_end.isoformat(), page, operation=operation)
                if page == 1 and total and math.ceil(total / 999) > max_pages:
                    raise IncompleteSyncError(f'{log_type} 원본 {total:,}건으로 페이지 한도를 초과합니다. 기간을 줄이세요.')
                if not items:
                    break
                processed += upsert_bids(items, target_terms=target_terms, business_type_override=business_type)
                seen += len(items)
                if total is not None and seen >= total:
                    break
                page += 1
                time.sleep(0.15)
            if total and seen < total:
                raise IncompleteSyncError(f'{log_type} {cur}~{chunk_end}: 원본 {total:,}건 중 {seen:,}건만 조회했습니다.')
            cur = chunk_end + dt.timedelta(days=1)
        finish_sync_log(log_id, 'OK', processed, f'{start_date} ~ {end_date}: {processed:,}건 저장·갱신')
        return processed
    except ApiQuotaReached as e:
        finish_sync_log(log_id, 'PAUSED', processed, str(e))
        raise
    except Exception as e:
        finish_sync_log(log_id, 'ERROR', processed, str(e))
        raise


def sync_bids_period(start_date, end_date, max_pages=500):
    with BID_LOCK:
        n = _sync_bid_operation(start_date, end_date, 'getBidPblancListInfoThng', BID_TARGETS, '물품', 'BIDS', max_pages=max_pages)
        now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        set_setting('last_bid_sync', now)
        set_setting('last_bid_sync_result', f'{start_date} ~ {end_date}: 조명 물품 입찰공고 {n:,}건 저장·갱신')
        return n


def sync_services_period(start_date, end_date, max_pages=500):
    with BID_LOCK:
        n = _sync_bid_operation(start_date, end_date, 'getBidPblancListInfoServc', SERVICE_TARGETS, '용역', 'SERVICES', max_pages=max_pages)
        now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        set_setting('last_service_sync', now)
        set_setting('last_service_sync_result', f'{start_date} ~ {end_date}: 조명/전기 관련 용역공고 {n:,}건 저장·갱신')
        return n


def test_shopping_api():
    """연결 테스트. 0건이면 조회구분 후보를 자동으로 시도한다."""
    end = dt.date.today()
    start = end - dt.timedelta(days=7)
    items, total = fetch_shop_page(start.isoformat(), end.isoformat(), 1, 10)
    if not items:
        found, probe_items, probe_total = probe_shop_inqry_div(start.isoformat(), end.isoformat(), rows=10)
        if found:
            return len(probe_items), probe_total
    return len(items), total


def backfill_period(start_date, end_date, progress=None):
    """지정 구간을 월 단위로 구축한다. 호출한도 도달 시 커서를 남기고 중단한다."""
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    first_month = dt.date(start.year, start.month, 1)
    cursor = get_setting('backfill_cursor', '')
    try:
        cur = dt.date.fromisoformat(cursor) if cursor else first_month
    except Exception:
        cur = first_month
    if cur < first_month or cur > end:
        cur = first_month

    months = []
    m = first_month
    while m <= end:
        nm = dt.date(m.year + 1, 1, 1) if m.month == 12 else dt.date(m.year, m.month + 1, 1)
        months.append(m)
        m = nm
    month_index = {mm: i for i, mm in enumerate(months)}

    total_saved = int(float(get_setting('backfill_total_saved', '0') or 0))
    set_setting('backfill_status', '실행중')
    set_setting('backfill_range', f'{start_date} ~ {end_date}')
    try:
        while cur <= end:
            next_month = dt.date(cur.year + 1, 1, 1) if cur.month == 12 else dt.date(cur.year, cur.month + 1, 1)
            chunk_start = max(cur, start)
            chunk_end = min(next_month - dt.timedelta(days=1), end)
            set_setting('backfill_cursor', cur.isoformat())
            try:
                n = sync_shopping_period(chunk_start.isoformat(), chunk_end.isoformat())
            except ApiQuotaReached as e:
                set_setting('backfill_status', '호출한도 대기')
                set_setting('backfill_message', f'{cur:%Y-%m} 수집 중 일일 호출한도 도달 · {e}')
                return total_saved
            total_saved += n
            set_setting('backfill_total_saved', str(total_saved))
            idx = month_index.get(cur, 0) + 1
            pct = min(100, int(idx / max(1, len(months)) * 100))
            set_setting('backfill_progress', str(pct))
            set_setting('backfill_message', f'{chunk_start} ~ {chunk_end} 완료 / 누적 대상 {total_saved:,}건')
            if progress:
                progress(pct, total_saved)
            cur = next_month
            set_setting('backfill_cursor', cur.isoformat())

        set_setting('backfill_status', '완료')
        set_setting('backfill_progress', '100')
        set_setting('backfill_cursor', '')
        return total_saved
    except Exception as e:
        set_setting('backfill_status', '오류')
        set_setting('backfill_message', str(e))
        raise


def backfill_since(start_date='2025-01-01', progress=None):
    """지정일부터 오늘까지 구축. 기본 시작일은 2025-01-01."""
    return backfill_period(start_date, dt.date.today().isoformat(), progress=progress)


def backfill_three_years(progress=None):
    """기존 호출부 호환용. 2025-01-01 또는 설정값부터 오늘까지 구축한다."""
    default_start = get_setting('backfill_start_date', '2025-01-01') or '2025-01-01'
    try:
        start = dt.date.fromisoformat(default_start)
    except Exception:
        start = dt.date(2025, 1, 1)
    return backfill_period(start.isoformat(), dt.date.today().isoformat(), progress=progress)
