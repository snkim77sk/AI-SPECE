"""v2.6.0: use specific-item procurement API for reliable 2025 shopping history.

Primary source:
  ShoppingMallPrdctInfoService/getSpcifyPrdlstPrcureInfoList

The previous delivery-detail operation can return totalCount=0 when called as a
broad/all-record query. This patch queries our exact procurement classifications
directly, probes the supported classification parameter, filters dates locally,
and keeps 2-hour change refreshes on the same source.
"""
import datetime as dt
import math
import time
import urllib.parse

from db import get_setting, set_setting

VERSION = "2.6.0-sinsung-specific-procurement"
HISTORY_START = "2025-01-01"
SPECIFIC_OPERATION = "getSpcifyPrdlstPrcureInfoList"
ROWS_PER_PAGE = 999
SHOP_INTERVAL_HOURS = 2
SHOP_OVERLAP_HOURS = 6
API_RESERVE = 100


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _target_codes(g):
    return tuple(sorted({_digits(x) for x in g.SHOP_DETAIL_ITEM_NOS if _digits(x)}))


def _source_keys(g, mode):
    codes = _target_codes(g)
    if mode == "detail":
        return [(code, code) for code in codes]
    prefixes = sorted({code[:8] for code in codes})
    return [(prefix, prefix) for prefix in prefixes]


def _params(mode, value, page, rows, key, change_start=None, change_end=None):
    p = {
        "serviceKey": key,
        "pageNo": int(page),
        "numOfRows": int(rows),
        "type": "json",
        "inqryDiv": "1",
    }
    if mode == "detail":
        p["dtilPrdctClsfcNo"] = value
    else:
        p["prdctClsfcNo"] = value
    if change_start is not None and change_end is not None:
        p["chgDtBgnDt"] = change_start.strftime("%Y%m%d%H%M")
        p["chgDtEndDt"] = change_end.strftime("%Y%m%d%H%M")
    return p


def _request_specific(g, mode, value, page=1, rows=ROWS_PER_PAGE, change_start=None, change_end=None):
    key = g.get_setting("api_key")
    base = g.get_setting("shop_api_base_url").rstrip("/")
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    p = _params(mode, value, page, rows, key, change_start, change_end)
    url = f"{base}/{SPECIFIC_OPERATION}?" + urllib.parse.urlencode(p, safe="%")
    return g._request(url, "shop")


def _first_value(raw, *keys):
    for key in keys:
        value = raw.get(key) if isinstance(raw, dict) else None
        if value not in (None, ""):
            return value
    return ""


def _prepare_raw(raw, requested_code="", mode="detail"):
    if not isinstance(raw, dict):
        return raw
    d = dict(raw)
    if mode == "detail" and requested_code and not _digits(
        _first_value(d, "dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo")
    ):
        d["dtilPrdctClsfcNo"] = requested_code

    aliases = {
        "dminsttNm": ("dmndInsttNm", "demandInsttNm", "demandOrgNm", "orderInsttNm"),
        "dminsttRgnNm": ("dmndInsttRgnNm", "demandRegionNm", "rgnNm", "regionName"),
        "corpNm": ("cntrctCorpNm", "entrpsNm", "vendorNm", "supplierNm", "corpName"),
        "prdctIdntNo": ("goodsIdntNo", "itemId", "identificationNo", "prdctIdntfcNo"),
        "prdctIdntNoNm": ("prdctIdntNm", "goodsIdntNm", "itemName", "prdctNm", "goodsNm"),
        "dtilPrdctClsfcNoNm": ("dtilPrdctClsfcNm", "detailPrdctNm", "detailItemName", "prdctClsfcNoNm"),
        "dlvrReqRcptDate": ("dlvrReqDt", "deliveryReqDt", "dlvrReqDate", "reqDt", "dlvrReqRcptDt"),
        "prdctUprc": ("unitPric", "unitPrice", "cntrctUnitPric", "cntrctPrce", "prc"),
        "prdctQty": ("dlvrReqQty", "reqQty", "quantity", "qty"),
        "prdctAmt": ("supplyAmount", "amount", "dlvrAmt", "dlvrReqAmt", "reqAmt"),
        "dlvrReqNm": ("cntrctNm", "contractNm", "deliveryReqNm", "bizNm", "dlvrReqSj"),
        "prdctSno": ("dlvrReqDtlSeq", "dlvrReqDtlSn", "dlvrReqSeq", "detailSeq", "seq"),
        "cntrctCorpBizno": ("corpBizno", "bizno", "bizrno"),
        "fnlDlvrReqYn": ("lastDlvrReqYn", "finalDlvrReqYn", "finalYn", "lastYn", "fnlYn"),
        "cntrctCnclsStleNm": ("cntrctMthdNm", "contractMthdNm", "contractMethodNm", "cntrctMthd"),
        "dlvrTmlmtDate": ("dlvrTmlmtDt", "deliveryDeadline", "dlvrDueDt", "deliveryDueDate"),
    }
    for dst, srcs in aliases.items():
        if d.get(dst) in (None, ""):
            value = _first_value(d, *srcs)
            if value not in (None, ""):
                d[dst] = value
    return d


def _normalized(g, raw, requested_code="", mode="detail"):
    prepared = _prepare_raw(raw, requested_code, mode)
    return prepared, g.normalize_shop_item(prepared)


def _is_permission_error(exc):
    text = str(exc).lower()
    return any(token in text for token in (
        "service_access_denied", "permission_denied", "service_key_is_not_registered",
        "deadline_has_expired", "api 오류 20", "api 오류 30", "api 오류 31",
        "이용 권한", "활용신청", "등록되지 않은 api",
    ))


def _probe_source(force=False):
    import g2b_sync as g
    saved = "" if force else get_setting("shop_specific_mode", "")
    if saved in ("detail", "class"):
        return saved

    diagnostics = []
    for mode in ("detail", "class"):
        for label, value in _source_keys(g, mode):
            try:
                items, total = _request_specific(g, mode, value, page=1, rows=20)
                total = int(total or 0)
                diagnostics.append(f"{mode}:{label}={total}")
                if items and total > 0:
                    first = _prepare_raw(items[0], label if mode == "detail" else "", mode)
                    fields = ",".join(sorted(str(k) for k in first.keys()))
                    set_setting("shop_specific_mode", mode)
                    set_setting("shop_specific_probe", " / ".join(diagnostics))
                    set_setting("shop_specific_probe_hit", f"{mode}:{label} total={total:,}")
                    set_setting("shop_specific_probe_total", str(total))
                    set_setting("shop_specific_probe_page_count", str(len(items)))
                    set_setting("last_shop_first_fields", fields)
                    return mode
            except g.ApiQuotaReached:
                raise
            except Exception as exc:
                diagnostics.append(f"{mode}:{label}=ERROR {str(exc)[:120]}")
                if _is_permission_error(exc):
                    set_setting("shop_specific_probe", " / ".join(diagnostics))
                    raise RuntimeError(
                        "특정품목조달내역 API 이용권한이 확인되지 않습니다. "
                        "공공데이터포털의 '조달청_나라장터쇼핑몰 품목정보 서비스' 최신 활용신청 상태를 확인해 주세요."
                    ) from exc

    set_setting("shop_specific_mode", "")
    set_setting("shop_specific_probe", " / ".join(diagnostics))
    raise RuntimeError(
        "특정품목조달내역 API에서 대상 세부품명/물품분류 조회가 모두 0건입니다. "
        "설정 화면의 '특정품목조달내역 진단'을 확인해 주세요."
    )


def _upsert_filtered(g, rows, requested_code, mode, start_date, end_date, targets):
    prepared_rows = []
    raw_in_range = raw_2025 = missing_date = missing_item_id = 0
    for raw in rows:
        prepared, x = _normalized(g, raw, requested_code, mode)
        code = _digits(x.get("detail_item_no"))
        base_date = str(x.get("base_date") or "")[:10]
        item_id = _digits(x.get("item_id"))
        if not base_date:
            missing_date += 1
            continue
        if base_date.startswith("2025-"):
            raw_2025 += 1
        if not (start_date <= base_date <= end_date):
            continue
        raw_in_range += 1
        if mode == "class" and code not in targets:
            continue
        if mode == "detail":
            if not code:
                prepared["dtilPrdctClsfcNo"] = requested_code
            elif code != requested_code:
                continue
        if not item_id:
            missing_item_id += 1
        prepared_rows.append(prepared)

    if not prepared_rows:
        return 0, 0, 0, raw_in_range, raw_2025, missing_date, missing_item_id
    saved, matched, skipped = g.upsert_shop(prepared_rows, target_only=True)
    return saved, matched, skipped, raw_in_range, raw_2025, missing_date, missing_item_id


def _collect_specific_range(start_date, end_date, *, history=False, auto=False, progress=None):
    import g2b_sync as g
    try:
        dt.date.fromisoformat(start_date)
        dt.date.fromisoformat(end_date)
    except Exception as exc:
        raise ValueError("수집 시작일/종료일 형식이 올바르지 않습니다.") from exc
    if start_date > end_date:
        raise ValueError("수집 시작일이 종료일보다 늦습니다.")

    mode = _probe_source()
    targets = set(_target_codes(g))
    source_keys = _source_keys(g, mode)

    resume_key = get_setting("v260_hist_key", "") if history else ""
    resume_page = int(float(get_setting("v260_hist_page", "1") or 1)) if history else 1
    resume_active = bool(history and get_setting("backfill_status", "") in ("호출한도 대기", "중단됨", "오류") and resume_key)

    if resume_active:
        total_raw = int(float(get_setting("v260_hist_raw", "0") or 0))
        total_in_range = int(float(get_setting("v260_hist_eligible", "0") or 0))
        total_2025 = int(float(get_setting("v260_hist_2025", "0") or 0))
        total_saved = int(float(get_setting("v260_hist_saved", "0") or 0))
        total_matched = int(float(get_setting("v260_hist_matched", "0") or 0))
        total_skipped = int(float(get_setting("v260_hist_skipped", "0") or 0))
        total_missing_date = int(float(get_setting("v260_hist_missing_date", "0") or 0))
        total_missing_item = int(float(get_setting("v260_hist_missing_item", "0") or 0))
        previous_diag = get_setting("v260_hist_diag", "")
        per_key = [x for x in previous_diag.split(" | ") if x]
    else:
        total_raw = total_in_range = total_2025 = 0
        total_saved = total_matched = total_skipped = 0
        total_missing_date = total_missing_item = 0
        per_key = []
    first_fields_saved = False

    log_type = "SHOPPING-SPECIFIC-HISTORY" if history else ("SHOPPING-SPECIFIC-AUTO" if auto else "SHOPPING-SPECIFIC")
    log_id = g.new_sync_log(log_type, start_date, end_date)

    try:
        with g.SHOP_LOCK:
            for key_index, (label, value) in enumerate(source_keys):
                if resume_active:
                    if label != resume_key:
                        continue
                    resume_active = False
                    page = max(1, resume_page)
                else:
                    page = 1

                key_raw = key_in_range = key_2025 = key_saved = 0
                total = None
                pages_needed = None
                while True:
                    if history:
                        set_setting("v260_hist_key", label)
                        set_setting("v260_hist_page", str(page))
                    used, limit = g.api_usage("shop")
                    if used >= max(1, limit - API_RESERVE):
                        raise g.ApiQuotaReached(
                            f"쇼핑몰 API 안전여유 {API_RESERVE}회를 남기기 위해 일시중단합니다. 오늘 {used:,}/{limit:,}회 사용."
                        )
                    items, total = _request_specific(g, mode, value, page=page, rows=ROWS_PER_PAGE)
                    total = int(total or 0)
                    if page == 1:
                        pages_needed = max(1, math.ceil(total / ROWS_PER_PAGE)) if total else 0
                    if not items:
                        break
                    if not first_fields_saved:
                        set_setting("last_shop_first_fields", ",".join(sorted(str(k) for k in items[0].keys())))
                        first_fields_saved = True

                    saved, matched, skipped, in_range, y2025, miss_date, miss_item = _upsert_filtered(
                        g, items, label if mode == "detail" else "", mode, start_date, end_date, targets
                    )
                    nraw = len(items)
                    key_raw += nraw
                    key_in_range += in_range
                    key_2025 += y2025
                    key_saved += saved
                    total_raw += nraw
                    total_in_range += in_range
                    total_2025 += y2025
                    total_saved += saved
                    total_matched += matched
                    total_skipped += skipped
                    total_missing_date += miss_date
                    total_missing_item += miss_item

                    set_setting("last_shop_raw_count", str(total_raw))
                    set_setting("last_shop_matched_count", str(total_matched))
                    set_setting("last_shop_saved_count", str(total_saved))
                    set_setting("last_shop_skipped_count", str(total_skipped))
                    set_setting("shop_specific_2025_raw", str(total_2025))
                    set_setting("shop_specific_in_range", str(total_in_range))
                    if history:
                        set_setting("v260_hist_raw", str(total_raw))
                        set_setting("v260_hist_eligible", str(total_in_range))
                        set_setting("v260_hist_2025", str(total_2025))
                        set_setting("v260_hist_saved", str(total_saved))
                        set_setting("v260_hist_matched", str(total_matched))
                        set_setting("v260_hist_skipped", str(total_skipped))
                        set_setting("v260_hist_missing_date", str(total_missing_date))
                        set_setting("v260_hist_missing_item", str(total_missing_item))

                    if progress:
                        pct = min(99, int((key_index + min(0.99, page / max(1, pages_needed or page))) / max(1, len(source_keys)) * 100))
                        progress(pct, total_saved)
                    if total <= key_raw:
                        break
                    page += 1
                    time.sleep(0.12)

                per_key.append(f"{label}:원본{key_raw:,}/기간{key_in_range:,}/2025년{key_2025:,}/저장{key_saved:,}")
                if history:
                    set_setting("v260_hist_diag", " | ".join(per_key))
                    set_setting("v260_hist_key", "")
                    set_setting("v260_hist_page", "1")

        diag = " | ".join(per_key)
        set_setting("shop_specific_collect_diag", diag)
        if total_raw <= 0:
            raise RuntimeError("특정품목조달내역 API에서 대상 분류 전체가 0건입니다. 공공데이터포털 활용신청 또는 요청 파라미터를 확인해 주세요.")
        if total_in_range <= 0:
            raise RuntimeError(f"특정품목조달내역 원본 {total_raw:,}건은 조회됐지만 {start_date}~{end_date} 범위의 실제 납품/계약일 데이터가 0건입니다.")
        if history and start_date <= "2025-12-31" and total_2025 <= 0:
            raise RuntimeError(f"특정품목조달내역 원본 {total_raw:,}건은 조회됐지만 2025년 원본이 0건입니다. 2025년 자료 제공범위를 확인해야 하므로 구축 완료로 처리하지 않습니다.")
        if total_saved <= 0 and total_in_range > 0:
            raise RuntimeError(
                f"기간대상 {total_in_range:,}건을 받았지만 저장 0건입니다. 필수값 누락(날짜 {total_missing_date:,}, 식별번호 {total_missing_item:,}) 또는 응답 필드 매핑을 확인해 주세요."
            )

        result = (
            f"{start_date} ~ {end_date} · 특정품목조달내역({mode}) · 원본 {total_raw:,}건 / "
            f"기간대상 {total_in_range:,}건 / 2025년 원본 {total_2025:,}건 / 저장·갱신 {total_saved:,}건"
        )
        set_setting("last_sync", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        set_setting("last_sync_result", result)
        set_setting("last_shop_error", "")
        g.finish_sync_log(log_id, "OK", total_saved, result)
        return {"raw": total_raw, "eligible": total_in_range, "raw_2025": total_2025, "saved": total_saved,
                "matched": total_matched, "skipped": total_skipped, "mode": mode, "diag": diag}
    except g.ApiQuotaReached as exc:
        g.finish_sync_log(log_id, "PAUSED", total_saved, str(exc))
        raise
    except Exception as exc:
        set_setting("last_shop_error", str(exc))
        g.finish_sync_log(log_id, "ERROR", total_saved, str(exc))
        raise


def sync_shopping_period_specific(start_date, end_date, max_pages=2000):
    return int(_collect_specific_range(start_date, end_date, history=False, auto=False)["saved"])


def build_history_2025(progress=None):
    today = dt.date.today().isoformat()
    previous = get_setting("backfill_status", "")
    resume = previous in ("호출한도 대기", "중단됨", "오류") and bool(get_setting("v260_hist_key", ""))
    if not resume:
        set_setting("v260_hist_key", "")
        set_setting("v260_hist_page", "1")
        set_setting("v260_hist_raw", "0")
        set_setting("v260_hist_eligible", "0")
        set_setting("v260_hist_2025", "0")
        set_setting("v260_hist_saved", "0")
        set_setting("v260_hist_matched", "0")
        set_setting("v260_hist_skipped", "0")
        set_setting("v260_hist_missing_date", "0")
        set_setting("v260_hist_missing_item", "0")
        set_setting("v260_hist_diag", "")
        set_setting("backfill_total_saved", "0")

    set_setting("backfill_status", "실행중")
    set_setting("backfill_progress", "0")
    set_setting("backfill_message", "특정품목조달내역 API에서 12개 세부품명번호를 직접 조회하여 2025-01-01 이후 자료를 구축 중입니다.")
    try:
        def report(pct, saved):
            set_setting("backfill_progress", str(pct))
            set_setting("backfill_total_saved", str(saved))
            set_setting("backfill_message", f"특정품목조달내역 구축 {pct}% · 저장·갱신 {saved:,}건 · 2025년 원본 {int(float(get_setting('shop_specific_2025_raw','0') or 0)):,}건")
            if progress:
                progress(pct, saved)

        info = _collect_specific_range(HISTORY_START, today, history=True, auto=False, progress=report)
        set_setting("backfill_status", "완료")
        set_setting("backfill_progress", "100")
        set_setting("backfill_total_saved", str(info["saved"]))
        set_setting("v260_hist_key", "")
        set_setting("v260_hist_page", "1")
        set_setting("shop_history_build_completed", "1")
        set_setting("shop_history_build_completed_at", dt.datetime.now().isoformat(timespec="seconds"))
        set_setting("backfill_message",
            f"2025-01-01 ~ {today} 구축 완료 · 특정품목 원본 {info['raw']:,}건 / 2025년 원본 {info['raw_2025']:,}건 / "
            f"기간대상 {info['eligible']:,}건 / 저장·갱신 {info['saved']:,}건 · 이후 2시간마다 변경분 자동수집")
        return int(info["saved"])
    except Exception as exc:
        import g2b_sync as g
        if isinstance(exc, g.ApiQuotaReached):
            set_setting("backfill_status", "호출한도 대기")
            set_setting("backfill_message", f"{exc} 다음 실행 시 중단 위치부터 재개합니다.")
            return int(float(get_setting("backfill_total_saved", "0") or 0))
        set_setting("backfill_status", "오류")
        set_setting("backfill_message", str(exc))
        raise


def test_specific_api():
    mode = _probe_source(force=True)
    hit = get_setting("shop_specific_probe_hit", "")
    if not hit:
        raise RuntimeError("특정품목조달내역 API에서 유효한 대상 분류 응답을 확인하지 못했습니다.")
    total = int(float(get_setting("shop_specific_probe_total", "0") or 0))
    page_count = int(float(get_setting("shop_specific_probe_page_count", "0") or 0))
    set_setting("shop_specific_test_result", f"연결 성공 · {SPECIFIC_OPERATION} · 조회모드 {mode} · {hit}")
    return page_count, total


def _collect_recent_changes():
    import g2b_sync as g
    mode = _probe_source()
    targets = set(_target_codes(g))
    keys = _source_keys(g, mode)
    now = dt.datetime.now()
    start = now - dt.timedelta(hours=SHOP_OVERLAP_HOURS)
    total_raw = total_saved = 0
    log_id = g.new_sync_log("SHOPPING-SPECIFIC-2H", start.isoformat(timespec="minutes"), now.isoformat(timespec="minutes"))
    try:
        with g.SHOP_LOCK:
            for label, value in keys:
                page = 1
                seen_for_key = 0
                while True:
                    used, limit = g.api_usage("shop")
                    if used >= max(1, limit - API_RESERVE):
                        raise g.ApiQuotaReached(f"2시간 자동수집 보류: 쇼핑몰 API {used:,}/{limit:,}회 사용, 안전여유 {API_RESERVE}회 유지")
                    items, total = _request_specific(g, mode, value, page=page, rows=ROWS_PER_PAGE, change_start=start, change_end=now)
                    total = int(total or 0)
                    if not items:
                        break
                    prepared_rows = []
                    for raw in items:
                        prepared, x = _normalized(g, raw, label if mode == "detail" else "", mode)
                        code = _digits(x.get("detail_item_no"))
                        if mode == "class" and code not in targets:
                            continue
                        if mode == "detail" and not code:
                            prepared["dtilPrdctClsfcNo"] = label
                        prepared_rows.append(prepared)
                    if prepared_rows:
                        saved, _, _ = g.upsert_shop(prepared_rows, target_only=True)
                        total_saved += saved
                    n = len(items)
                    total_raw += n
                    seen_for_key += n
                    if total <= seen_for_key:
                        break
                    page += 1
                    time.sleep(0.12)

        result = (f"2시간 자동수집 · 특정품목조달내역({mode}) · {start:%Y-%m-%d %H:%M} ~ {now:%Y-%m-%d %H:%M} · 원본 {total_raw:,}건 / 저장·갱신 {total_saved:,}건")
        set_setting("last_shop_2h_success", now.isoformat(timespec="seconds"))
        set_setting("last_shop_2h_result", result)
        set_setting("last_sync_result", result)
        set_setting("last_shop_error", "")
        g.finish_sync_log(log_id, "OK", total_saved, result)
        return total_saved
    except Exception as exc:
        set_setting("last_shop_2h_result", f"2시간 자동수집 실패: {exc}")
        set_setting("last_shop_error", str(exc))
        g.finish_sync_log(log_id, "ERROR", total_saved, str(exc))
        raise


def apply_v260_patch():
    import g2b_sync as g
    import scheduler as sch
    import server as s

    g.sync_shopping_period = sync_shopping_period_specific
    s.sync_shopping_period = sync_shopping_period_specific
    g.backfill_three_years = build_history_2025
    s.backfill_three_years = build_history_2025
    sch.backfill_three_years = build_history_2025
    g.test_shopping_api = test_specific_api
    s.test_shopping_api = test_specific_api

    marker = "v260_specific_procurement_initialized"
    if get_setting(marker, "") != "1":
        set_setting("shop_specific_mode", "")
        set_setting("shop_specific_probe", "아직 특정품목조달내역 연결 테스트를 실행하지 않았습니다.")
        set_setting("shop_specific_probe_hit", "")
        set_setting("shop_specific_probe_total", "0")
        set_setting("shop_specific_probe_page_count", "0")
        set_setting("shop_specific_collect_diag", "")
        set_setting("shop_history_build_completed", "0")
        set_setting("backfill_status", "대기")
        set_setting("backfill_progress", "0")
        set_setting("backfill_message", "특정품목조달내역 방식으로 2025-01-01 수동 구축을 다시 실행해 주세요.")
        set_setting("v260_hist_key", "")
        set_setting("v260_hist_page", "1")
        set_setting("v260_hist_raw", "0")
        set_setting("v260_hist_eligible", "0")
        set_setting("v260_hist_2025", "0")
        set_setting("v260_hist_saved", "0")
        set_setting("v260_hist_matched", "0")
        set_setting("v260_hist_skipped", "0")
        set_setting("v260_hist_missing_date", "0")
        set_setting("v260_hist_missing_item", "0")
        set_setting("v260_hist_diag", "")
        set_setting("last_shop_2h_attempt", "")
        set_setting("last_shop_2h_result", "2025-01-01 수동 구축 완료 후 2시간 자동수집이 시작됩니다.")
        set_setting(marker, "1")

    def worker():
        while True:
            try:
                enabled = str(get_setting("auto_sync_enabled", "0")).lower() in ("1", "true", "yes", "on")
                if enabled and get_setting("api_key"):
                    now = dt.datetime.now()
                    if get_setting("backfill_status", "") == "완료":
                        last_text = get_setting("last_shop_2h_attempt", "")
                        due = True
                        if last_text:
                            try:
                                due = (now - dt.datetime.fromisoformat(last_text)).total_seconds() >= SHOP_INTERVAL_HOURS * 3600
                            except Exception:
                                due = True
                        if due:
                            set_setting("last_shop_2h_attempt", now.isoformat(timespec="seconds"))
                            try:
                                _collect_recent_changes()
                            except Exception:
                                pass

                    hours = max(1, int(float(get_setting("auto_sync_hours", "3") or 3)))
                    last_bs = get_setting("last_bidservice_auto_sync", "")
                    due_bs = True
                    if last_bs:
                        try:
                            due_bs = (now - dt.datetime.fromisoformat(last_bs)).total_seconds() >= hours * 3600
                        except Exception:
                            due_bs = True
                    if due_bs:
                        today = now.date()
                        days = max(1, min(90, int(float(get_setting("auto_sync_days", "14") or 14))))
                        bstart = max(today - dt.timedelta(days=days), today - dt.timedelta(days=27))
                        messages = []
                        try:
                            n = sch.sync_bids_period(bstart.isoformat(), today.isoformat())
                            messages.append(f"물품입찰 {n:,}건")
                        except Exception as exc:
                            messages.append(f"입찰 오류: {exc}")
                        try:
                            n = sch.sync_services_period(bstart.isoformat(), today.isoformat())
                            messages.append(f"용역 {n:,}건")
                        except Exception as exc:
                            messages.append(f"용역 오류: {exc}")
                        set_setting("last_bidservice_auto_sync", now.isoformat(timespec="seconds"))
                        set_setting("last_auto_bidservice_result", " / ".join(messages))

                today = dt.date.today()
                today_text = today.isoformat()
                if str(get_setting("budget_auto_sync_enabled", "1")).lower() in ("1", "true", "yes", "on") and sch.get_lofin_key():
                    if get_setting("last_budget_auto_attempt_date", "") != today_text:
                        set_setting("last_budget_auto_attempt_date", today_text)
                        set_setting("budget_sync_status", "수집중")
                        try:
                            sch.sync_budget_snapshot(today.year, today_text)
                            set_setting("budget_sync_status", "완료")
                        except Exception as exc:
                            set_setting("budget_sync_status", "오류")
                            set_setting("last_budget_sync_result", f"예산 자동수집 실패: {exc}")
            except Exception:
                pass
            time.sleep(60)

    sch._worker = worker
    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        page = page.replace("쇼핑몰 API 연결 테스트", "특정품목조달내역 API 연결 테스트")
        page = page.replace("쇼핑몰 API 연결/날짜조건 테스트", "특정품목조달내역 API 연결 테스트")
        page = page.replace("2025-01-01부터 구축 시작", "2025-01-01 수동 구축")
        page = page.replace("전체스냅샷", "특정품목조달내역")
        page = page.replace("납품요구상세 전체조회", "특정품목조달내역 직접조회")
        mode = get_setting("shop_specific_mode", "") or "미확정"
        hit = get_setting("shop_specific_probe_hit", "") or "-"
        probe = get_setting("shop_specific_probe", "") or "-"
        collect = get_setting("shop_specific_collect_diag", "") or "-"
        notice = (
            '<div class="notice"><b>쇼핑몰 수집원천</b><br>'
            f'오퍼레이션: {s.esc(SPECIFIC_OPERATION)} · 조회모드: {s.esc(mode)}<br>'
            f'연결확인: {s.esc(hit)}<br>'
            f'<b>특정품목조달내역 진단:</b> {s.esc(probe)}<br>'
            f'<b>최근 품목별 수집:</b> {s.esc(collect)}<br>'
            '<small>12개 세부품명번호를 직접 조회하고, 실제 납품/계약일을 프로그램 내부에서 기간 필터링합니다.</small></div>'
        )
        marker_html = '<hr><h3>수동 동기화</h3>'
        if marker_html in page and "쇼핑몰 수집원천</b>" not in page:
            page = page.replace(marker_html, notice + marker_html, 1)
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
