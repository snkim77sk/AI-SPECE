"""Cafe24-safe application wrapper for SINSUNG G2B DATA VIEW 2.2.

Adds manual/automatic collection coordination while preserving the proven 2.0
FastAPI healthcheck-first startup structure.
"""
import os

import app as legacy_app

APP_VERSION = "2.2"

_original_manual_start = legacy_app._start_background_collect
_original_background_collect = legacy_app._background_collect


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
        if get_setting("last_auto_sync_status", "") == "수집중":
            source = get_setting("last_auto_sync_current_source", "") or "자동수집"
            return False, f"{source} 자동수집이 진행 중입니다. 완료 후 수동수집을 실행해 주세요."
        if get_setting("manual_sync_active", "0") == "1":
            source = get_setting("manual_sync_source", "") or "수동수집"
            return False, f"이미 {source} 수동수집이 진행 중입니다."
    except Exception:
        pass

    # Mark the narrow start window before the background thread is created so
    # the scheduler cannot begin an automatic run at the same moment.
    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    ok, message = _original_manual_start(path, body, request_headers)
    if not ok:
        _set_manual_state(False, "")
    return ok, message


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
        # Existing one-time cleanup is idempotent and normally already complete.
        from sinsung_v200_reset import reset_data_once
        reset_data_once()

        # Preserve 2.1 initialization for users who jump directly from 2.0.
        from sinsung_v210_auto import initialize_auto_sync
        initialize_auto_sync()

        from sinsung_v220_stability import initialize_auto_stability
        initialize_auto_stability()

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


# Original manual-start resolves _background_collect from app.py globals when it
# runs, so installing both wrappers here safely coordinates the two directions.
legacy_app._background_collect = _background_collect_v220
legacy_app._start_background_collect = _start_background_collect_v220
legacy_app._start_backend = _start_backend_v220
legacy_app._wait_for_backend = _fast_backend_wait
legacy_app.APP_VERSION = APP_VERSION
legacy_app.app.version = APP_VERSION

app = legacy_app.app

__all__ = ["app", "APP_VERSION"]
