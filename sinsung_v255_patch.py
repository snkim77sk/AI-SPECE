"""v2.5.5: adaptive shopping API date-parameter probe with visible diagnostics.

The ShoppingMallPrdctInfoService page has changed over time and several G2B
services use different date parameter names.  Instead of silently accepting a
zero-row response, this patch probes supported date parameter families, checks
that returned delivery/contract dates actually fall inside the requested
period, remembers the working mode, and fails visibly when no mode can be
validated.
"""
import datetime as dt
import urllib.parse

from db import get_setting, set_setting

VERSION = "2.5.5-sinsung-shopping-probe"

# Candidate parameter families seen in current/legacy G2B OpenAPI contracts.
# The first validated family is persisted and reused for normal collection.
_DATE_MODES = (
    ("inqry_dt", "조회일시", "inqryBgnDt", "inqryEndDt", True),
    ("chg_dt", "변경일시", "chgDtBgnDt", "chgDtEndDt", True),
    ("rgst_dt", "등록일시", "rgstDtBgnDt", "rgstDtEndDt", True),
    ("legacy_date", "구형 조회일자", "inqryBgnDate", "inqryEndDate", False),
)

_DATE_FIELDS = (
    "dlvrReqRcptDate", "dlvrReqDt", "deliveryReqDt", "dlvrReqDate",
    "IntlCntrctDlvrReqDate", "intlCntrctDlvrReqDate", "intllCntrctDlvrReqDate",
    "cntrctDt", "contractDt", "contractDate", "baseDt",
)


def _compact_date(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _in_range_rows(items, start_date, end_date):
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    matched = 0
    dated = 0
    samples = []
    for row in items or []:
        found = ""
        for key in _DATE_FIELDS:
            found = _compact_date(row.get(key) if isinstance(row, dict) else "")
            if found:
                break
        if found:
            dated += 1
            if len(samples) < 4:
                samples.append(found)
            if start <= found <= end:
                matched += 1
    return matched, dated, samples


def _mode_by_name(name):
    for mode in _DATE_MODES:
        if mode[0] == name:
            return mode
    return None


def _params_for_mode(mode, start_date, end_date, page, rows, key):
    name, _label, begin_key, end_key, with_time = mode
    start_digits = start_date.replace("-", "")
    end_digits = end_date.replace("-", "")
    params = {
        "serviceKey": key,
        "numOfRows": int(rows),
        "pageNo": int(page),
        "type": "json",
        "inqryDiv": "1",
    }
    if with_time:
        params[begin_key] = start_digits + "0000"
        params[end_key] = end_digits + "2359"
    else:
        params[begin_key] = start_digits
        params[end_key] = end_digits
    return params


def apply_v255_patch():
    import g2b_sync as g
    import server as s
    import scheduler as scheduler_module
    import sinsung_v253_patch as hist

    def request_mode(mode, start_date, end_date, page=1, rows=999):
        key = g.get_setting("api_key")
        base = g.get_setting("shop_api_base_url").rstrip("/")
        op = g.get_setting("shop_api_operation").strip("/")
        if not key:
            raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
        if not op:
            raise RuntimeError("쇼핑몰 납품요구상세 오퍼레이션명이 비어 있습니다.")
        params = _params_for_mode(mode, start_date, end_date, page, rows, key)
        url = f"{base}/{op}?" + urllib.parse.urlencode(params, safe="%")
        return g._request(url, "shop")

    def probe_mode(start_date, end_date, rows=100):
        diagnostics = []
        for mode in _DATE_MODES:
            name, label, *_ = mode
            try:
                items, total = request_mode(mode, start_date, end_date, 1, rows)
                in_range, dated, samples = _in_range_rows(items, start_date, end_date)
                diagnostics.append(
                    f"{name}: total={int(total or 0)}, page={len(items)}, "
                    f"dated={dated}, in_range={in_range}, samples={','.join(samples) or '-'}"
                )
                # A real nationwide shopping response for a normal multi-day/monthly
                # period must contain rows and at least one returned business date in
                # the requested interval.  This rejects parameter names that are simply
                # ignored by the upstream API.
                if int(total or 0) > 0 and items and in_range > 0:
                    set_setting("shop_date_param_mode", name)
                    set_setting("shop_date_param_label", label)
                    set_setting("last_shop_probe", " / ".join(diagnostics))
                    return mode, items, total
            except g.ApiQuotaReached:
                raise
            except Exception as exc:
                text = str(exc)
                diagnostics.append(f"{name}: ERROR {text[:180]}")
                # Authentication/authorization failures are independent of the date
                # parameter name.  Do not burn more calls repeating the same failure.
                lowered = text.lower()
                if any(token in lowered for token in (
                    "service_access_denied", "service_key_is_not_registered",
                    "service_key_is_null", "permission", "인증키", "권한",
                    "code 20", "code 30", "code 31", "api 오류 20",
                    "api 오류 30", "api 오류 31",
                )):
                    set_setting("last_shop_probe", " / ".join(diagnostics))
                    raise
        summary = " / ".join(diagnostics) or "응답 없음"
        set_setting("shop_date_param_mode", "")
        set_setting("shop_date_param_label", "미확정")
        set_setting("last_shop_probe", summary)
        raise RuntimeError(
            "쇼핑몰 API 날짜조건을 검증하지 못했습니다. 원본 0건을 정상완료로 처리하지 않습니다. "
            "설정 화면의 '쇼핑몰 API 날짜조건 진단' 내용을 확인해 주세요."
        )

    def fetch_shop_page(start_date, end_date, page=1, rows=999, force_probe=False):
        saved_name = "" if force_probe else get_setting("shop_date_param_mode", "")
        mode = _mode_by_name(saved_name)
        if mode is None:
            mode, first_items, first_total = probe_mode(start_date, end_date, min(int(rows), 100))
            # Return the probed first page only if the requested page/size exactly
            # matches. Otherwise issue the normal request with the now-validated mode.
            if int(page) == 1 and int(rows) <= 100:
                return first_items[: int(rows)], first_total

        items, total = request_mode(mode, start_date, end_date, page, rows)
        if int(page) == 1:
            in_range, dated, samples = _in_range_rows(items, start_date, end_date)
            set_setting(
                "last_shop_probe",
                f"사용모드={mode[0]}({mode[1]}) · total={int(total or 0)} · "
                f"page={len(items)} · dated={dated} · in_range={in_range} · "
                f"samples={','.join(samples) or '-'}",
            )
            # For normal 7+ day or historical month collection, a zero response is
            # suspicious enough to re-probe all known parameter families once.
            try:
                days = (dt.date.fromisoformat(end_date) - dt.date.fromisoformat(start_date)).days + 1
            except Exception:
                days = 30
            if (not items or int(total or 0) == 0 or (dated and in_range == 0)) and days >= 7:
                mode2, first_items, first_total = probe_mode(start_date, end_date, min(int(rows), 100))
                if int(rows) <= 100:
                    return first_items[: int(rows)], first_total
                return request_mode(mode2, start_date, end_date, page, rows)
        return items, total

    def test_shopping_api():
        # Use a broad recent period and force a fresh probe so the test verifies
        # both authentication and the date-filter contract, not just HTTP reachability.
        end = dt.date.today()
        start = end - dt.timedelta(days=30)
        items, total = fetch_shop_page(start.isoformat(), end.isoformat(), 1, 50, force_probe=True)
        return len(items), total

    g.fetch_shop_page = fetch_shop_page
    g.test_shopping_api = test_shopping_api
    s.test_shopping_api = test_shopping_api

    # Keep every historical-build reference on the 2025 builder.
    g.backfill_three_years = hist.backfill_from_2025
    s.backfill_three_years = hist.backfill_from_2025
    scheduler_module.backfill_three_years = hist.backfill_from_2025

    # Reset only the false/unknown historical progress state once. Existing real
    # procurement rows remain untouched and UPSERT will update them safely.
    marker = "v255_shop_probe_reset"
    if get_setting(marker, "") != "1":
        set_setting("backfill_scope_start", hist.SCOPE_KEY)
        set_setting("backfill_status", "대기")
        set_setting("backfill_progress", "0")
        set_setting("backfill_cursor", "")
        set_setting("backfill_total_saved", "0")
        set_setting("backfill_message", "쇼핑몰 API 날짜조건 자동진단 적용 · API 연결 테스트 후 2025-01-01부터 구축해 주세요.")
        set_setting("shop_date_param_mode", "")
        set_setting("shop_date_param_label", "미확정")
        set_setting("last_shop_probe", "아직 날짜조건 진단을 실행하지 않았습니다.")
        set_setting(marker, "1")

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        mode = get_setting("shop_date_param_mode", "") or "미확정"
        label = get_setting("shop_date_param_label", "") or "미확정"
        probe = get_setting("last_shop_probe", "") or "아직 진단하지 않았습니다."
        diagnostic = (
            '<div class="notice"><b>쇼핑몰 API 날짜조건 진단</b><br>'
            f'현재 모드: {s.esc(mode)} ({s.esc(label)})<br>'
            f'{s.esc(probe)}<br>'
            '<small>원본 0건 또는 요청기간과 다른 날짜의 응답은 정상 수집으로 인정하지 않습니다.</small></div>'
        )
        marker_html = '<hr><h3>수동 동기화</h3>'
        if marker_html in page and "쇼핑몰 API 날짜조건 진단" not in page:
            page = page.replace(marker_html, diagnostic + marker_html, 1)
        page = page.replace("쇼핑몰 API 연결 테스트", "쇼핑몰 API 연결/날짜조건 테스트")
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
