"""Cafe24-safe application wrapper for SINSUNG G2B DATA VIEW 2.3.

2.3 deliberately keeps the proven 2.2 module/entrypoint layout. Historical
collection is implemented inside the existing scheduler module so Cafe24 does
not need any new Python module during application startup.
"""
import datetime as dt
import os

import app as legacy_app

APP_VERSION = "2.3"

_original_manual_start = legacy_app._start_background_collect
_original_background_collect = legacy_app._background_collect

_HISTORY_BUSY = {
    "준비", "실행중", "재개대기", "자동수집 대기", "수동수집 대기",
    "호출한도 대기", "오류대기",
}


def _set_manual_state(active, source=""):
    try:
        from db import set_setting
        set_setting("manual_sync_active", "1" if active else "0")
        set_setting("manual_sync_source", source if active else "")
    except Exception:
        pass


def _background_collect_v220(path, body, headers):
    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    try:
        return _original_background_collect(path, body, headers)
    finally:
        _set_manual_state(False, "")


def _start_background_collect_v220(path, body, request_headers):
    try:
        from db import get_setting
        if get_setting("backfill_auto_resume", "0") == "1" and get_setting("backfill_status", "") in _HISTORY_BUSY:
            source = get_setting("backfill_current_source", "") or get_setting("backfill_status", "") or "과거자료"
            return False, f"2025 과거자료 구축({source})이 진행 중입니다. 과거 구축 단계가 끝난 뒤 수동수집을 실행해 주세요."
        if get_setting("last_auto_sync_status", "") == "수집중":
            source = get_setting("last_auto_sync_current_source", "") or "자동수집"
            return False, f"{source} 자동수집이 진행 중입니다. 완료 후 수동수집을 실행해 주세요."
        if get_setting("manual_sync_active", "0") == "1":
            source = get_setting("manual_sync_source", "") or "수동수집"
            return False, f"이미 {source} 수동수집이 진행 중입니다."
    except Exception:
        pass

    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    ok, message = _original_manual_start(path, body, request_headers)
    if not ok:
        _set_manual_state(False, "")
    return ok, message


def _recover_runtime_state():
    """Process-local collection threads disappear on every Cafe24 restart."""
    try:
        from db import get_setting, set_setting

        if get_setting("manual_sync_active", "0") == "1":
            set_setting("manual_sync_active", "0")
            set_setting("manual_sync_source", "")

        if get_setting("last_auto_sync_status", "") == "수집중":
            started = None
            try:
                started = dt.datetime.fromisoformat(get_setting("last_auto_sync_started", ""))
            except Exception:
                pass
            age = ""
            if started:
                minutes = max(0, int((dt.datetime.now() - started).total_seconds() // 60))
                age = f" · 이전 시작 후 {minutes}분"
            set_setting("last_auto_sync_status", "재시작복구")
            set_setting("last_auto_sync_current_source", "")
            set_setting("last_auto_sync_result", "2.3 기동 시 이전 자동수집 실행상태를 복구했습니다" + age)

        if get_setting("backfill_auto_resume", "0") == "1" and get_setting("backfill_status", "") in _HISTORY_BUSY:
            set_setting("backfill_status", "재개대기")
            set_setting("backfill_current_source", "")
            set_setting("backfill_message", "Cafe24 재시작 후 저장된 2025 과거자료 체크포인트에서 재개합니다.")
    except Exception:
        pass


def _start_backend_v220() -> None:
    ok, msg = legacy_app._configured()
    if not ok:
        legacy_app._backend_error = msg
        return

    os.environ["HOST"] = legacy_app.BACKEND_HOST
    os.environ["PORT"] = str(legacy_app.BACKEND_PORT)
    os.environ["G2B_PUBLIC_MODE"] = "0" if legacy_app.TEST_MODE else "1"
    os.environ["G2B_OPEN_BROWSER"] = "0"
    os.environ["G2B_SEED_SAMPLE"] = "0"
    os.environ.setdefault("G2B_COOKIE_SECURE", "1")

    try:
        from sinsung_v200_reset import reset_data_once
        reset_data_once()

        from sinsung_v210_auto import initialize_auto_sync
        initialize_auto_sync()

        from sinsung_v220_stability import initialize_auto_stability
        initialize_auto_stability()

        # 2.3 history defaults live in the existing scheduler.py file.
        from scheduler import initialize_history_backfill
        initialize_history_backfill()
        _recover_runtime_state()

        if str(os.getenv("G2B_PURGE_SAMPLE_DATA", "1")).lower() in ("1", "true", "yes", "on"):
            from db import init_db
            from seed import clear_samples
            init_db()
            clear_samples()

        import server
        server.main(open_browser=False)
    except Exception as exc:
        legacy_app._backend_error = f"내부 대시보드 시작 실패: {exc}"


def _fast_backend_wait(timeout: float = 0.5) -> bool:
    return legacy_app._backend_listening()


legacy_app._background_collect = _background_collect_v220
legacy_app._start_background_collect = _start_background_collect_v220
legacy_app._start_backend = _start_backend_v220
legacy_app._wait_for_backend = _fast_backend_wait
legacy_app.APP_VERSION = APP_VERSION
legacy_app.app.version = APP_VERSION

app = legacy_app.app

__all__ = ["app", "APP_VERSION"]
