"""v2.5.3 patch: build procurement history from 2025-01-01 with resumable monthly sync."""
import datetime as dt

from db import get_setting, set_setting

VERSION = "2.5.3-sinsung-history-2025"
HISTORY_START = dt.date(2025, 1, 1)
SCOPE_KEY = HISTORY_START.isoformat()


def _next_month(day):
    return dt.date(day.year + 1, 1, 1) if day.month == 12 else dt.date(day.year, day.month + 1, 1)


def backfill_from_2025(progress=None):
    """Collect shopping procurement data from 2025-01-01 through today.

    The collector runs month-by-month, resumes after the daily API safety limit,
    and relies on the existing source-key UPSERT behavior so reruns update rows
    instead of creating duplicates.
    """
    import g2b_sync as g

    end = dt.date.today()
    first_month = HISTORY_START
    previous_scope = get_setting("backfill_scope_start", "")
    previous_status = get_setting("backfill_status", "대기")
    cursor_text = get_setting("backfill_cursor", "")

    resume = previous_scope == SCOPE_KEY and previous_status in ("호출한도 대기", "중단됨", "실행중") and bool(cursor_text)
    if resume:
        try:
            cur = dt.date.fromisoformat(cursor_text)
        except Exception:
            cur = first_month
        if cur < first_month or cur > end:
            cur = first_month
        total_saved = int(float(get_setting("backfill_total_saved", "0") or 0))
    else:
        cur = first_month
        total_saved = 0
        set_setting("backfill_scope_start", SCOPE_KEY)
        set_setting("backfill_cursor", first_month.isoformat())
        set_setting("backfill_total_saved", "0")
        set_setting("backfill_progress", "0")
        set_setting("backfill_message", f"{SCOPE_KEY}부터 과거 조달자료 구축을 시작합니다.")

    months = []
    m = first_month
    while m <= end:
        months.append(m)
        m = _next_month(m)
    month_index = {m: i for i, m in enumerate(months)}

    set_setting("backfill_status", "실행중")
    try:
        while cur <= end:
            next_month = _next_month(cur)
            chunk_start = max(cur, HISTORY_START)
            chunk_end = min(next_month - dt.timedelta(days=1), end)
            set_setting("backfill_cursor", cur.isoformat())
            try:
                n = g.sync_shopping_period(chunk_start.isoformat(), chunk_end.isoformat())
            except g.ApiQuotaReached as exc:
                set_setting("backfill_status", "호출한도 대기")
                set_setting(
                    "backfill_message",
                    f"{chunk_start} ~ {chunk_end} 수집 중 일일 호출한도 도달 · 다음 날 자동 재개 · {exc}",
                )
                return total_saved

            total_saved += n
            set_setting("backfill_total_saved", str(total_saved))
            idx = month_index.get(cur, 0) + 1
            pct = min(100, int(idx / max(1, len(months)) * 100))
            set_setting("backfill_progress", str(pct))
            set_setting(
                "backfill_message",
                f"{chunk_start} ~ {chunk_end} 완료 / 2025-01-01 이후 누적 처리 {total_saved:,}건",
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
            f"2025-01-01 ~ {end.isoformat()} 구축 완료 / 누적 처리 {total_saved:,}건",
        )
        return total_saved
    except Exception as exc:
        set_setting("backfill_status", "오류")
        set_setting("backfill_message", str(exc))
        raise


def apply_v253_patch():
    import g2b_sync as g
    import scheduler as scheduler_module
    import server as s

    # Replace every live reference used by manual and automatic resume paths.
    g.backfill_three_years = backfill_from_2025
    s.backfill_three_years = backfill_from_2025
    scheduler_module.backfill_three_years = backfill_from_2025

    # Keep the familiar settings layout but make the actual historical scope clear.
    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        replacements = {
            "최근 3년 구축 시작": "2025-01-01부터 구축 시작",
            "최근 3년을 월 단위로 순차 수집합니다. API 호출량이 많을 수 있습니다. 시작할까요?":
                "2025-01-01부터 오늘까지 월 단위로 순차 수집합니다. 기존 자료는 중복 추가하지 않고 갱신합니다. 시작할까요?",
            "3년 구축 상태:": "2025-01-01 이후 구축 상태:",
        }
        for old, new in replacements.items():
            page = page.replace(old, new)
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
