"""Per-process restart recovery for SINSUNG 2.3.

Unlike one-time version initialization, these checks must run on every Cafe24
process start because daemon collection threads cannot survive a container restart.
"""
import datetime as dt

from db import get_setting, set_setting

VERSION = "2.3"


def _parse_dt(value):
    try:
        return dt.datetime.fromisoformat(str(value or ""))
    except Exception:
        return None


def recover_runtime_state_v230():
    recovered = []

    # A manual collection thread is process-local. Any persisted active flag is
    # stale after a new process starts.
    if get_setting("manual_sync_active", "0") == "1":
        set_setting("manual_sync_active", "0")
        set_setting("manual_sync_source", "")
        recovered.append("수동수집")

    # Automatic scheduler threads are also process-local. Recover this state on
    # every restart, not only the first 2.2 upgrade.
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
            "2.3 기동 시 이전 자동수집 실행상태를 안전하게 복구했습니다" + age_text,
        )
        recovered.append("자동수집")

    # A 2.3 history thread can be recreated from its persistent cursor. Mark it
    # ready for the delayed resume timer before server.main() starts.
    backfill_status = get_setting("backfill_status", "")
    auto_resume = get_setting("backfill_auto_resume", "0") == "1"
    if auto_resume and backfill_status in {
        "준비", "실행중", "자동수집 대기", "수동수집 대기", "호출한도 대기", "중단됨"
    }:
        set_setting("backfill_status", "재개대기")
        set_setting("backfill_current_source", "")
        set_setting(
            "backfill_message",
            "Cafe24 재시작을 감지했습니다. 저장된 2025 과거자료 체크포인트에서 자동 재개합니다.",
        )
        recovered.append("과거구축")
    elif backfill_status == "중지요청":
        set_setting("backfill_status", "중지됨")
        set_setting("backfill_auto_resume", "0")
        set_setting("backfill_current_source", "")
        set_setting("backfill_message", "재시작 전에 요청한 과거자료 구축 중지를 확정했습니다.")
        recovered.append("과거구축중지")

    set_setting("v230_last_runtime_recovery", dt.datetime.now().isoformat(timespec="seconds"))
    set_setting("v230_runtime_recovery_result", ", ".join(recovered) if recovered else "정상")
    return recovered
