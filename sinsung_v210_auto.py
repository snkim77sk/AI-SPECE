"""2.1 automatic collection initialization.

Uses the verified 2.0 single collector engine and only changes runtime scheduling:
- procurement auto collection enabled
- 2 hour interval
- recent 14 day overlap for UPSERT refresh
- historical backfill remains disabled
- budget auto collection remains separately controlled
"""
from db import get_setting, set_setting

VERSION = "2.1"
INIT_MARKER = "v210_auto_sync_initialized"


def initialize_auto_sync():
    """Apply 2.1 automatic collection defaults once.

    Existing API credentials, users, company configuration and collected data are
    preserved. The first 2.1 start clears last_auto_sync so a verified refresh is
    performed immediately, then the scheduler continues every 2 hours.
    """
    if get_setting(INIT_MARKER, "") == "1":
        return False

    set_setting("auto_sync_enabled", "1")
    set_setting("auto_sync_hours", "2")
    set_setting("auto_sync_days", "14")
    set_setting("last_auto_sync", "")
    set_setting("last_auto_sync_started", "")
    set_setting("last_auto_sync_finished", "")
    set_setting("last_auto_sync_status", "대기")
    set_setting(
        "last_auto_sync_result",
        "2.1 자동수집 준비 완료 · 쇼핑몰/물품입찰/용역을 2시간마다 동일 수집엔진으로 갱신",
    )

    # Historical build stays disabled until a later verified release.
    set_setting("backfill_status", "비활성")
    set_setting("backfill_progress", "0")
    set_setting("backfill_message", "2.1에서는 과거자료 구축을 비활성화합니다.")

    set_setting(INIT_MARKER, "1")
    return True
