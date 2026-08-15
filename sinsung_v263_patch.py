"""v2.6.3: make getDlvrReqDtlInfoList self-recovering across query parameter variants.

The v2.6.2 source restore was correct, but g2b_sync.fetch_shop_page still used a
single legacy request shape (inqryBgnDate/inqryEndDate + persisted inqryDiv).
This patch probes only the known working delivery-detail operation, validates
returned row dates, persists the winning shape, and falls back to one full
snapshot with local date filtering when the API ignores/does not support a date
shape.
"""
import datetime as dt
import math
import time
import urllib.parse

from db import get_setting, set_setting

VERSION = "2.6.3-sinsung-delivery-adaptive"
ROWS_PER_PAGE = 999
API_RESERVE = 100
DATE_MODES = ("legacy_date", "inqry_dt", "rgst_dt", "chg_dt")
DIVS = ("1", "2", "3")


def _params(key, div, mode, start_date, end_date, page, rows):
    p = {
        "serviceKey": key,
        "pageNo": int(page),
        "numOfRows": int(rows),
        "type": "json",
        "inqryDiv": str(div),
    }
    s = str(start_date).replace("-", "")
    e = str(end_date).replace("-", "")
    if mode == "legacy_date":
        p["inqryBgnDate"] = s
        p["inqryEndDate"] = e
    elif mode == "inqry_dt":
        p["inqryBgnDt"] = s + "0000"
        p["inqryEndDt"] = e + "2359"
    elif mode == "rgst_dt":
        p["rgstDtBgnDt"] = s + "0000"
        p["rgstDtEndDt"] = e + "2359"
    elif mode == "chg_dt":
        p["chgDtBgnDt"] = s + "0000"
        p["chgDtEndDt"] = e + "2359"
    return p


def _direct_page(g, mode, div, start_date, end_date, page=1, rows=ROWS_PER_PAGE):
    key = g.get_setting("api_key")
    base = g.get_setting("shop_api_base_url").rstrip("/")
    op = g.get_setting("shop_api_operation").strip("/") or "getDlvrReqDtlInfoList"
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    p = _params(key, div, mode, start_date, end_date, page, rows)
    url = f"{base}/{op}?" + urllib.parse.urlencode(p, safe="%")
    return g._request(url, "shop")


def _base_date(g, raw):
    try:
        return str(g.normalize_shop_item(raw).get("base_date") or "")[:10]
    except Exception:
        return ""


def _has_in_range_date(g, items, start_date, end_date):
    for raw in items[:50]:
        d = _base_date(g, raw)
        if d and start_date <= d <= end_date:
            return True
    return False


def probe_delivery_shape(force=False):
    import g2b_sync as g

    saved_mode = get_setting("v263_delivery_mode", "")
    saved_div = get_setting("v263_delivery_div", "")
    if not force and saved_mode in DATE_MODES + ("no_date",) and saved_div in DIVS:
        return saved_mode, saved_div

    today = dt.date.today()
    start = (today - dt.timedelta(days=45)).isoformat()
    end = today.isoformat()
    report = []

    # First prefer a true server-side date filter. A candidate only wins when a
    # returned row itself has a business date inside the requested window.
    for mode in DATE_MODES:
        for div in DIVS:
            try:
                items, total = _direct_page(g, mode, div, start, end, 1, 20)
                report.append(f"{mode}/div{div}: total={int(total or 0)}, rows={len(items)}")
                if items and _has_in_range_date(g, items, start, end):
                    set_setting("v263_delivery_mode", mode)
                    set_setting("v263_delivery_div", div)
                    set_setting("shop_inqry_div", div)
                    set_setting("v263_delivery_probe", " | ".join(report))
                    set_setting("v263_delivery_probe_hit", f"{mode} / inqryDiv={div}")
                    return mode, div
            except g.ApiQuotaReached:
                raise
            except Exception as exc:
                report.append(f"{mode}/div{div}: 오류 {str(exc)[:120]}")

    # Final fallback: some gateway revisions expose delivery-detail rows without
    # a usable date query. If so, fetch the snapshot once and filter dates in-app.
    for div in DIVS:
        try:
            items, total = _direct_page(g, "no_date", div, start, end, 1, 20)
            report.append(f"no_date/div{div}: total={int(total or 0)}, rows={len(items)}")
            if items and int(total or 0) > 0:
                set_setting("v263_delivery_mode", "no_date")
                set_setting("v263_delivery_div", div)
                set_setting("shop_inqry_div", div)
                set_setting("v263_delivery_probe", " | ".join(report))
                set_setting("v263_delivery_probe_hit", f"no_date / inqryDiv={div} / 로컬 날짜필터")
                return "no_date", div
        except g.ApiQuotaReached:
            raise
        except Exception as exc:
            report.append(f"no_date/div{div}: 오류 {str(exc)[:120]}")

    set_setting("v263_delivery_mode", "")
    set_setting("v263_delivery_div", "")
    set_setting("v263_delivery_probe", " | ".join(report))
    set_setting("v263_delivery_probe_hit", "")
    raise RuntimeError(
        "getDlvrReqDtlInfoList의 조회 파라미터를 자동 검증했지만 유효한 원본 응답을 찾지 못했습니다. "
        "설정 화면의 '납품요구상세 파라미터 진단(v2.6.3)'을 확인해 주세요."
    )


def fetch_shop_page_adaptive(start_date, end_date, page=1, rows=ROWS_PER_PAGE, inqry_div=None):
    import g2b_sync as g
    mode, saved_div = probe_delivery_shape(False)
    div = str(inqry_div or saved_div)
    items, total = _direct_page(g, mode, div, start_date, end_date, page, rows)
    # If a previously valid shape suddenly goes empty, re-probe once on page 1.
    if page == 1 and not items and int(total or 0) <= 0:
        mode, div = probe_delivery_shape(True)
        items, total = _direct_page(g, mode, div, start_date, end_date, page, rows)
    return items, total


def _filter_rows(g, items, start_date, end_date):
    out = []
    missing_date = 0
    for raw in items:
        d = _base_date(g, raw)
        if not d:
            missing_date += 1
            continue
        if start_date <= d <= end_date:
            out.append(raw)
    return out, missing_date


def collect_delivery_range(start_date, end_date, log_type="SHOPPING", progress=None):
    import g2b_sync as g

    try:
        sdate = dt.date.fromisoformat(start_date)
        edate = dt.date.fromisoformat(end_date)
    except Exception as exc:
        raise ValueError("수집 시작일/종료일 형식이 올바르지 않습니다.") from exc
    if sdate > edate:
        sdate, edate = edate, sdate
        start_date, end_date = sdate.isoformat(), edate.isoformat()

    mode, div = probe_delivery_shape(False)
    log_id = g.new_sync_log(log_type, start_date, end_date)
    total_raw = total_saved = total_matched = total_skipped = total_missing_date = 0
    diagnostics = []

    try:
        with g.SHOP_LOCK:
            # no_date must be read exactly once; repeated monthly calls would
            # fetch the same snapshot over and over and violate the requested range.
            chunks = [(sdate, edate)] if mode == "no_date" else g.month_chunks(start_date, end_date)
            for idx, (csd, ced) in enumerate(chunks, start=1):
                cs, ce = csd.isoformat(), ced.isoformat()
                page = 1
                seen = 0
                raw_chunk = saved_chunk = 0
                set_setting("last_shop_chunk", f"{cs} ~ {ce} ({idx}/{len(chunks)})")
                while True:
                    used, limit = g.api_usage("shop")
                    if used >= max(1, limit - API_RESERVE):
                        raise g.ApiQuotaReached(
                            f"쇼핑몰 API 안전여유 {API_RESERVE}회를 남기기 위해 중단합니다. 오늘 {used:,}/{limit:,}회 사용."
                        )
                    items, total = _direct_page(g, mode, div, cs, ce, page, ROWS_PER_PAGE)
                    total = int(total or 0)
                    if not items:
                        break
                    raw_chunk += len(items)
                    total_raw += len(items)
                    seen += len(items)

                    filtered, missing_date = _filter_rows(g, items, start_date, end_date)
                    total_missing_date += missing_date
                    if filtered:
                        saved, matched, skipped = g.upsert_shop(filtered, target_only=True)
                        total_saved += saved
                        total_matched += matched
                        total_skipped += skipped
                        saved_chunk += saved
                    if total_raw == len(items):
                        set_setting("last_shop_first_fields", ", ".join(sorted(str(k) for k in items[0].keys())))
                    set_setting("last_shop_raw_count", str(total_raw))
                    set_setting("last_shop_matched_count", str(total_matched))
                    set_setting("last_shop_saved_count", str(total_saved))
                    set_setting("last_shop_skipped_count", str(total_skipped + total_missing_date))
                    if progress:
                        pct = min(99, int((idx - 1 + min(1.0, seen / max(1, total))) / max(1, len(chunks)) * 100))
                        progress(pct, total_saved)
                    if seen >= total:
                        break
                    page += 1
                    time.sleep(0.12)
                diagnostics.append(f"{cs}~{ce}:원본{raw_chunk:,}/저장{saved_chunk:,}")

        if total_raw <= 0:
            raise RuntimeError("납품요구상세 원본 응답이 0건입니다. 파라미터 자동검증 결과를 확인해 주세요.")
        result = (
            f"{start_date} ~ {end_date} · getDlvrReqDtlInfoList({mode}, div={div}) · "
            f"원본 {total_raw:,}건 / 대상 {total_matched:,}건 / 저장·갱신 {total_saved:,}건 / "
            f"날짜·필수값 제외 {total_missing_date + total_skipped:,}건"
        )
        set_setting("v263_collect_diag", " | ".join(diagnostics[-24:]))
        set_setting("last_sync", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        set_setting("last_sync_result", result)
        set_setting("last_shop_error", "")
        set_setting("last_shop_chunk", "")
        g.finish_sync_log(log_id, "OK", total_saved, result)
        return {"raw": total_raw, "saved": total_saved, "matched": total_matched,
                "skipped": total_skipped + total_missing_date, "mode": mode, "div": div}
    except g.ApiQuotaReached as exc:
        g.finish_sync_log(log_id, "PAUSED", total_saved, str(exc))
        raise
    except Exception as exc:
        set_setting("last_shop_error", str(exc))
        g.finish_sync_log(log_id, "ERROR", total_saved, str(exc))
        raise


def apply_v263_patch():
    import g2b_sync as g
    import server as s
    import scheduler as sch
    import sinsung_v262_patch as v262

    # Reset only the parameter-selection cache, never collected business data.
    marker = "v263_delivery_adaptive_initialized"
    if get_setting(marker, "") != "1":
        set_setting("v263_delivery_mode", "")
        set_setting("v263_delivery_div", "")
        set_setting("v263_delivery_probe", "아직 자동검증 전")
        set_setting("v263_delivery_probe_hit", "")
        set_setting(marker, "1")

    g.fetch_shop_page = fetch_shop_page_adaptive
    # v2.6.2 wrappers resolve this global at runtime, so replacing it fixes
    # manual sync, history build and 2-hour refresh together.
    v262._collect_delivery_range = collect_delivery_range

    def test_shopping_api():
        mode, div = probe_delivery_shape(True)
        today = dt.date.today()
        start = (today - dt.timedelta(days=14)).isoformat()
        items, total = _direct_page(g, mode, div, start, today.isoformat(), 1, 20)
        if not items and int(total or 0) <= 0:
            raise RuntimeError("납품요구상세 자동검증 후에도 원본 0건입니다.")
        set_setting("shop_specific_test_result",
                    f"연결 성공 · getDlvrReqDtlInfoList · {mode} · inqryDiv={div} · 원본 {int(total or 0):,}건")
        return len(items), int(total or 0)

    g.test_shopping_api = test_shopping_api
    s.test_shopping_api = test_shopping_api

    # Existing v2.6.2 history/manual wrappers now call the replaced collector.
    g.sync_shopping_period = v262.sync_shopping_period_delivery
    s.sync_shopping_period = v262.sync_shopping_period_delivery
    g.backfill_three_years = v262.build_history_delivery
    s.backfill_three_years = v262.build_history_delivery
    sch.backfill_three_years = v262.build_history_delivery

    original_settings_html = s.settings_html
    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        hit = get_setting("v263_delivery_probe_hit", "") or "-"
        diag = get_setting("v263_delivery_probe", "") or "-"
        collect = get_setting("v263_collect_diag", "") or "-"
        block = (
            '<div class="notice"><b>납품요구상세 파라미터 진단(v2.6.3)</b><br>'
            f'채택: {s.esc(hit)}<br>'
            f'진단: {s.esc(diag)}<br>'
            f'최근수집: {s.esc(collect)}<br>'
            '<small>getDlvrReqDtlInfoList만 사용하며 조회구분과 날짜 파라미터를 실제 응답 날짜로 검증합니다. '
            '서버 날짜검색이 불가하면 전체 스냅샷을 한 번만 받아 요청기간을 로컬에서 필터링합니다.</small></div>'
        )
        marker_html = '<hr><h3>수동 동기화</h3>'
        if marker_html in page and "납품요구상세 파라미터 진단(v2.6.3)" not in page:
            page = page.replace(marker_html, block + marker_html, 1)
        return page
    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
