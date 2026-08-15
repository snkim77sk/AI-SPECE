"""SINSUNG G2B DATA VIEW 2.2 automatic collection stabilization.

2.2 does not change the verified 2.0 collector engine. It only hardens runtime
coordination and persistent scheduler state for long-running Cafe24 operation.
"""
import datetime as dt

from db import get_setting, set_setting

VERSION = "2.2"
INIT_MARKER = "v220_auto_stability_initialized"


def _parse_dt(value):
    try:
        return dt.datetime.fromisoformat(str(value or ""))
    except Exception:
        return None


def initialize_auto_stability():
    """Apply 2.2 scheduler safety defaults once without deleting any data."""
    if get_setting(INIT_MARKER, "") == "1":
        return False

    # The operating policy remains exactly the verified 2.1 schedule.
    set_setting("auto_sync_enabled", "1")
    set_setting("auto_sync_hours", "2")
    set_setting("auto_sync_days", "14")
    set_setting("auto_sync_api_reserve", "100")

    # A manual collection thread cannot survive a process restart. Clear any
    # stale flag so the new scheduler is not blocked forever.
    set_setting("manual_sync_active", "0")
    set_setting("manual_sync_source", "")

    # If Cafe24 restarted while an automatic run was in progress, the old
    # daemon thread is gone. Mark it as recovered instead of leaving a false
    # permanent '수집중' status in the UI.
    if get_setting("last_auto_sync_status", "") == "수집중":
        started = _parse_dt(get_setting("last_auto_sync_started", ""))
        age_text = ""
        if started:
            minutes = max(0, int((dt.datetime.now() - started).total_seconds() // 60))
            age_text = f" · 이전 시작 후 {minutes}분"
        set_setting("last_auto_sync_status", "재시작복구")
        set_setting("last_auto_sync_current_source", "")
        set_setting(
            "last_auto_sync_result",
            "2.2 시작 시 이전 자동수집 실행상태를 안전하게 복구했습니다" + age_text,
        )

    # Historical build remains isolated until its own verified release.
    set_setting("backfill_status", "비활성")
    set_setting("backfill_progress", "0")
    set_setting("backfill_message", "2.2에서는 과거자료 구축을 비활성화합니다.")

    set_setting("scheduler_heartbeat", "")
    set_setting("last_auto_sync_current_source", "")
    set_setting("last_auto_sync_consecutive_failures", get_setting("last_auto_sync_consecutive_failures", "0") or "0")
    set_setting("v220_stability_summary", "2시간 자동수집 안정화 적용 · 충돌방지/한도여유/재시작복구/heartbeat")
    set_setting(INIT_MARKER, "1")
    return True
