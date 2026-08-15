"""v2.5.7: manual 2025 history build by registration datetime + daily change sync.

Initial historical construction uses the ShoppingMall service registration
window (rgstDtBgnDt/rgstDtEndDt). Ongoing automatic collection uses the
change window (chgDtBgnDt/chgDtEndDt) once per day with a 2-day overlap.
Existing rows are updated through the existing source-key UPSERT behavior.
"""
import datetime as dt
import math
import time
import urllib.parse

from db import get_setting, set_setting

VERSION = "2.5.7-sinsung-manual-history-daily"
HISTORY_START = dt.date(2025, 1, 1)
DAILY_OVERLAP_DAYS = 2


def _next_month(day):
    return dt.date(day.year + 1, 1, 1) if day.month == 12 else dt.date(day.year, day.month + 1, 1)


def _request_shop(g, start_date, end_date, page=1, rows=999, mode="changed"):
    key = g.get_setting("api_key")
    base = g.get_setting("shop_api_base_url").rstrip("/")
    op = g.get_setting("shop_api_operation").strip("/")
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    if not op:
        raise RuntimeError("쇼핑몰 납품요구상세 오퍼레이션명이 비어 있습니다.")

    params = {
        "serviceKey": key,
        "numOfRows": int(rows),
        "pageNo": int(page),
        "type": "json",
        "inqryDiv": "1",
    }
    bgn = start_date.replace("-", "") + "0000"
    end = end_date.replace("-", "") + "2359"
    if mode == "registered":
        params["rgstDtBgnDt"] = bgn
        params["rgstDtEndDt"] = end
    else:
        params["chgDtBgnDt"] = bgn
        params["chgDtEndDt"] = end

    url = f"{base}/{op}?" + urllib.parse.urlencode(params, safe="%")
    return g._request(url, "shop")


def _sync_shop_window(start_date, end_date, mode="changed", max_pages=2000):
    import g2b_sync as g

    label = "등록일시" if mode == "registered" else "변경일시"
    with g.SHOP_LOCK:
        log_id = g.new_sync_log(f"SHOPPING-{mode.upper()}", start_date, end_date)
        processed = seen = matched = skipped = 0
        try:
            page = 1
            total = None
            rows_per_page = 999
            while page <= max_pages:
                items, total = _request_shop(g, start_date, end_date, page, rows_per_page, mode)
                if page == 1 and total and math.ceil(total / rows_per_page) > max_pages:
                    raise g.IncompleteSyncError(
                        f"{label} 원본 {total:,}건으로 페이지 한도 {max_pages:,}를 초과합니다. 기간을 줄여 주세요."
                    )
                if not items:
                    break
                saved_now, matched_now, skipped_now = g.upsert_shop(items, target_only=True)
                processed += saved_now
                matched += matched_now
                skipped += skipped_now
                if page == 1:
                    set_setting(
                        "last_shop_first_fields",
                        ", ".join(sorted(str(k) for k in items[0].keys())) if items else "",
                    )
                seen += len(items)
                set_setting("last_shop_raw_count", str(seen))
                set_setting("last_shop_matched_count", str(matched))
                set_setting("last_shop_saved_count", str(processed))
                set_setting("last_shop_skipped_count", str(skipped))
                if total is not None and seen >= total:
                    break
                page += 1
                time.sleep(0.15)

            if total and seen < total:
                raise g.IncompleteSyncError(f"{label} 원본 {total:,}건 중 {seen:,}건만 수집했습니다.")
            # A nationwide historical registration month should not silently be 0.
            # Daily change windows may legitimately have no changes, so 0 is allowed there.
            if seen == 0 and mode == "registered":
                raise RuntimeError(f"쇼핑몰 {label} 조회 원본이 0건입니다. API 활용승인/조회조건을 확인해 주세요.")

            now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result = (
                f"{start_date} ~ {end_date} · {label} 기준 · 원본 {seen:,}건 / "
                f"세부품명번호 대상 {matched:,}건 / 저장·갱신 {processed:,}건 / 필수값 누락 {skipped:,}건"
            )
            set_setting("last_sync", now)
            set_setting("last_shop_error", "")
            set_setting("last_sync_result", result)
            g.finish_sync_log(log_id, "OK", processed, result)
            return processed, seen
        except g.ApiQuotaReached as exc:
            g.finish_sync_log(log_id, "PAUSED", processed, str(exc))
            raise
        except Exception as exc:
            set_setting("last_shop_error", str(exc))
            g.finish_sync_log(log_id, "ERROR", processed, str(exc))
            raise


def manual_history_2025(progress=None):
    """User-triggered monthly historical build from 2025-01-01 using registration datetime."""
    import g2b_sync as g

    end = dt.date.today()
    status = get_setting("backfill_status", "대기")
    cursor_text = get_setting("backfill_cursor", "")
    resume = status in ("호출한도 대기", "중단됨", "오류", "실행중") and bool(cursor_text)
    if resume:
        try:
            cur = dt.date.fromisoformat(cursor_text)
        except Exception:
            cur = HISTORY_START
        if cur < HISTORY_START or cur > end:
            cur = HISTORY_START
        total_saved = int(float(get_setting("backfill_total_saved", "0") or 0))
    else:
        cur = HISTORY_START
        total_saved = 0
        set_setting("backfill_cursor", HISTORY_START.isoformat())
        set_setting("backfill_total_saved", "0")
        set_setting("backfill_progress", "0")

    months = []
    m = HISTORY_START
    while m <= end:
        months.append(m)
        m = _next_month(m)
    month_index = {m: i for i, m in enumerate(months)}

    # Keep server restart compatibility: server.main converts '실행중' to '중단됨'.
    set_setting("backfill_status", "실행중")
    set_setting("backfill_message", "2025-01-01부터 등록일시 기준으로 월별 수동 구축 중입니다.")
    try:
        while cur <= end:
            next_month = _next_month(cur)
            chunk_end = min(next_month - dt.timedelta(days=1), end)
            set_setting("backfill_cursor", cur.isoformat())
            try:
                n, raw = _sync_shop_window(cur.isoformat(), chunk_end.isoformat(), mode="registered")
            except g.ApiQuotaReached as exc:
                set_setting("backfill_status", "호출한도 대기")
                set_setting("backfill_message", f"{cur:%Y-%m} 수집 중 호출한도 도달 · 다음에 버튼을 누르면 이 달부터 재개 · {exc}")
                return total_saved

            total_saved += n
            set_setting("backfill_total_saved", str(total_saved))
            idx = month_index.get(cur, 0) + 1
            pct = min(100, int(idx / max(1, len(months)) * 100))
            set_setting("backfill_progress", str(pct))
            set_setting(
                "backfill_message",
                f"{cur.isoformat()} ~ {chunk_end.isoformat()} 완료 · 원본 {raw:,}건 · 누적 저장·갱신 {total_saved:,}건",
            )
            if progress:
                progress(pct, total_saved)
            cur = next_month
            set_setting("backfill_cursor", cur.isoformat())

        set_setting("backfill_status", "완료")
        set_setting("backfill_progress", "100")
        set_setting("backfill_cursor", "")
        set_setting(
            "backfill_message",
            f"2025-01-01 ~ {end.isoformat()} 수동 구축 완료 · 누적 저장·갱신 {total_saved:,}건 · 이후 매일 자동 변경분 수집",
        )
        return total_saved
    except Exception as exc:
        set_setting("backfill_status", "오류")
        set_setting("backfill_message", str(exc))
        raise


def apply_v257_patch():
    import g2b_sync as g
    import scheduler as sch
    import server as s

    # Normal manual/API test uses change-datetime semantics. Historical build
    # bypasses this and explicitly uses registration datetime month by month.
    def fetch_shop_page(start_date, end_date, page=1, rows=999):
        return _request_shop(g, start_date, end_date, page, rows, mode="changed")

    def test_shopping_api():
        end = dt.date.today()
        start = end - dt.timedelta(days=30)
        items, total = _request_shop(g, start.isoformat(), end.isoformat(), 1, 50, mode="changed")
        if int(total or 0) <= 0 or not items:
            raise RuntimeError("쇼핑몰 변경일시 기준 최근 30일 응답이 0건입니다.")
        return len(items), total

    g.fetch_shop_page = fetch_shop_page
    g.test_shopping_api = test_shopping_api
    s.test_shopping_api = test_shopping_api
    g.backfill_three_years = manual_history_2025
    s.backfill_three_years = manual_history_2025
    sch.backfill_three_years = manual_history_2025

    # Daily shopping updates are separated from the bid/service interval loop.
    def worker():
        while True:
            try:
                enabled = str(get_setting("auto_sync_enabled", "0")).lower() in ("1", "true", "yes", "on")
                if enabled and get_setting("api_key"):
                    now = dt.datetime.now()
                    today = now.date()
                    today_text = today.isoformat()

                    # Shopping: once per calendar day, using change datetime with
                    # a 2-day overlap so delayed/late updates are safely re-upserted.
                    if get_setting("last_shop_daily_attempt", "") != today_text:
                        set_setting("last_shop_daily_attempt", today_text)
                        shop_start = today - dt.timedelta(days=DAILY_OVERLAP_DAYS - 1)
                        try:
                            n, raw = _sync_shop_window(shop_start.isoformat(), today_text, mode="changed")
                            set_setting("last_shop_daily_success", today_text)
                            set_setting("last_shop_daily_result", f"매일 자동수집 완료 · 원본 {raw:,}건 / 저장·갱신 {n:,}건")
                        except Exception as exc:
                            set_setting("last_shop_daily_result", f"매일 자동수집 실패: {exc}")

                    # Bid/service: preserve existing configurable interval behavior.
                    hours = max(1, int(float(get_setting("auto_sync_hours", "3") or 3)))
                    last = get_setting("last_bidservice_auto_sync", "")
                    due = True
                    if last:
                        try:
                            due = (now - dt.datetime.fromisoformat(last)).total_seconds() >= hours * 3600
                        except Exception:
                            due = True
                    if due:
                        days = max(1, min(90, int(float(get_setting("auto_sync_days", "14") or 14))))
                        end = today
                        bstart = max(end - dt.timedelta(days=days), end - dt.timedelta(days=27))
                        messages = []
                        try:
                            n = sch.sync_bids_period(bstart.isoformat(), end.isoformat())
                            messages.append(f"물품입찰 {n:,}건")
                        except Exception as exc:
                            messages.append(f"입찰 오류: {exc}")
                        try:
                            n = sch.sync_services_period(bstart.isoformat(), end.isoformat())
                            messages.append(f"용역 {n:,}건")
                        except Exception as exc:
                            messages.append(f"용역 오류: {exc}")
                        set_setting("last_bidservice_auto_sync", now.isoformat(timespec="seconds"))
                        set_setting("last_auto_bidservice_result", " / ".join(messages))

                # Preserve the existing once-daily budget snapshot.
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

    # Reset only obsolete progress/diagnostic state once; real rows stay intact.
    marker = "v257_manual_history_daily_initialized"
    if get_setting(marker, "") != "1":
        set_setting("backfill_status", "대기")
        set_setting("backfill_progress", "0")
        set_setting("backfill_cursor", "")
        set_setting("backfill_total_saved", "0")
        set_setting("backfill_message", "2025-01-01 수동 구축 대기 · 완료 후 쇼핑몰은 매일 자동 변경분을 수집합니다.")
        set_setting("shop_date_param_mode", "official-rgst/chg")
        set_setting("shop_date_param_label", "과거=등록일시 / 일상=변경일시")
        set_setting("last_shop_probe", "v2.5.7 고정 방식 · 과거 구축 rgstDtBgnDt/rgstDtEndDt · 일상 갱신 chgDtBgnDt/chgDtEndDt")
        set_setting(marker, "1")

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        # Shopping manual form starts at the historical baseline; bids/services
        # retain the prior 2026-01-01 default from v2.5.6.
        import re
        page = re.sub(
            r'(<form[^>]*action="/sync-shop"[^>]*>.*?<input[^>]*type="date"[^>]*name="start"[^>]*value=")[^"]*(")',
            r'\g<1>2025-01-01\2', page, count=1, flags=re.S,
        )
        page = page.replace("2025-01-01부터 구축 시작", "2025-01-01 수동 구축")
        page = page.replace("쇼핑몰 API 연결/날짜조건 테스트", "쇼핑몰 API 연결 테스트")
        page = page.replace("프로그램 실행 중 자동수집", "쇼핑몰 매일 자동수집")
        page = page.replace("수집주기(시간)", "입찰/용역 수집주기(시간)")

        daily = get_setting("last_shop_daily_result", "아직 매일 자동수집 기록이 없습니다.")
        success = get_setting("last_shop_daily_success", "-") or "-"
        note = (
            '<div class="notice"><b>쇼핑몰 수집 방식</b><br>'
            '① 최초 구축: <b>2025-01-01 수동 구축</b> 버튼 → 등록일시 기준 월별 수집<br>'
            '② 이후 운영: <b>매일 자동수집</b> → 변경일시 기준 최근 2일을 재조회하여 중복 없이 갱신<br>'
            f'최근 자동 성공일: {s.esc(success)}<br>{s.esc(daily)}</div>'
        )
        marker_html = '<hr><h3>수동 동기화</h3>'
        if marker_html in page and "쇼핑몰 수집 방식" not in page:
            page = page.replace(marker_html, note + marker_html, 1)
        page = page.replace("쇼핑몰 API 날짜조건 진단", "쇼핑몰 API 수집 진단")
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
