"""Cafe24-safe FastAPI wrapper for SINSUNG G2B DATA VIEW 2.0.

The public FastAPI process must become healthy before potentially slow cleanup of
an existing persistent SQLite database. The one-time 2.0 reset therefore runs
inside the existing backend thread, not while importing main.py.
"""
import os

import app as legacy_app

APP_VERSION = "2.0"


def _start_backend_v200() -> None:
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
        # Potentially slow work is deliberately done here, after the FastAPI
        # application object already exists. reset_data_once() is idempotent.
        from sinsung_v200_reset import reset_data_once
        reset_data_once()

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
    """Never let the internal dashboard delay platform ASGI readiness."""
    return legacy_app._backend_listening()


# startup_event() resolves these globals when the event actually runs, so
# replacing them here keeps the proven FastAPI/proxy code and changes only the
# backend start behavior. Cafe24 can receive /health immediately while the
# persistent DB reset continues in the daemon backend thread.
legacy_app._start_backend = _start_backend_v200
legacy_app._wait_for_backend = _fast_backend_wait
legacy_app.APP_VERSION = APP_VERSION
legacy_app.app.version = APP_VERSION

app = legacy_app.app

__all__ = ["app", "APP_VERSION"]
