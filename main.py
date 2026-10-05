"""Ultra-light Cafe24 bootstrap for SINSUNG G2B vNext.

Production must bind the HTTP port before importing the full runtime.  The previous
entrypoint imported vnext_clean_app while uvicorn was still importing main:app; if
that import was slow or the 256MB process was killed, even the fallback app never
had a chance to bind and Cafe24 returned 502.

This module therefore exposes a tiny FastAPI app immediately.  After the bootstrap
ASGI lifespan starts, a daemon thread imports vnext_clean_app and switches all
non-liveness traffic to the real runtime.  /live and /__ai_space_health remain
bootstrap-owned and storage-free for the lifetime of the process.
"""
from __future__ import annotations

import importlib
import os
import threading
import time
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app_version import APP_VERSION

_TRUE = {"1", "true", "yes", "on"}
_RUNTIME_LOCK = threading.RLock()
_RUNTIME_APP = None
_RUNTIME_MODULE = None
_RUNTIME_ERROR = ""
_RUNTIME_LOADING = False
_RUNTIME_ATTEMPTS = 0
_RUNTIME_THREAD = None

# Compatibility diagnostics retained for older callers/tests.
BOOTSTRAP_IMPORT_ERROR = ""
BUDGET_DB_BRIDGE_SOURCE = ""


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def _runtime_import_delay_seconds():
    if _flag_on("G2B_TEST_MODE"):
        return 0.0
    raw = str(
        os.getenv("G2B_RUNTIME_IMPORT_DELAY_SECONDS", "5") or "5"
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        value = 5.0
    return max(2.0, min(value, 60.0))


def _runtime_role_label():
    value = str(
        os.getenv("G2B_RUNTIME_ROLE", "UNIFIED") or "UNIFIED"
    ).strip().upper()
    return (
        value
        if value in {"UNIFIED", "RESULT_SERVER", "LOCAL_COLLECTOR"}
        else "UNIFIED"
    )


def _public_error(error):
    if _flag_on("G2B_TEST_MODE"):
        return str(error or "")
    text = str(error or "")
    return text.split(":", 1)[0][:120]


def bridge_cafe24_budget_database_url(environ=None):
    """Return only the selected PostgreSQL source label.

    This helper is intentionally never called during module import.  It may import
    SQLAlchemy-backed configuration only after HTTP has already bound.
    """
    import g2b_database

    try:
        value = g2b_database.resolve_database_url(environ)
    except Exception:
        return ""
    return g2b_database.database_source_label() if value else ""


def _fallback_app(error):
    public_error = _public_error(error)
    fallback = FastAPI(title="SINSUNG G2B vNext bootstrap fallback")

    @fallback.get("/live")
    def live():
        return {
            "status": "ok",
            "process_alive": True,
            "runtime": "G2B_VNEXT_BOOTSTRAP",
            "import_ok": False,
        }

    @fallback.get("/health")
    @fallback.get("/__ai_space_health")
    def health():
        return {
            "status": "ok",
            "process_alive": True,
            "backend_ok": False,
            "runtime": "G2B_VNEXT_BOOTSTRAP",
            "import_ok": False,
            "import_error": public_error,
        }

    @fallback.get("/ready")
    def ready():
        return JSONResponse(
            {
                "status": "not_ready",
                "backend_ok": False,
                "runtime": "G2B_VNEXT_BOOTSTRAP",
                "import_error": public_error,
            },
            status_code=503,
        )

    @fallback.get("/")
    def root():
        return HTMLResponse(
            "<!doctype html><html lang='ko'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SINSUNG G2B vNext</title>"
            "<body style='font-family:sans-serif;padding:32px'>"
            "<h2>SINSUNG G2B vNext bootstrap</h2>"
            "<p>웹 프로세스는 기동했지만 애플리케이션 모듈을 불러오지 못했습니다.</p>"
            f"<pre>{public_error}</pre></body></html>",
            status_code=200,
        )
    return fallback


def build_runtime(importer=importlib.import_module):
    """Compatibility helper for deterministic tests/manual diagnostics.

    Unlike old releases, this is not executed at module import.
    """
    try:
        runtime = importer("vnext_clean_app")
        return runtime.app, ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        print("G2B_VNEXT_IMPORT_FAILURE", error, flush=True)
        traceback.print_exc()
        return _fallback_app(error), error


def _runtime_snapshot():
    with _RUNTIME_LOCK:
        return {
            "app": _RUNTIME_APP,
            "module": _RUNTIME_MODULE,
            "error": _RUNTIME_ERROR,
            "loading": bool(_RUNTIME_LOADING),
            "attempts": int(_RUNTIME_ATTEMPTS),
        }


def runtime_loaded():
    return _runtime_snapshot()["app"] is not None


def runtime_app():
    return _runtime_snapshot()["app"]


def load_runtime_now(importer=importlib.import_module):
    """Import the full runtime outside the uvicorn import/bind path."""
    global _RUNTIME_APP, _RUNTIME_MODULE, _RUNTIME_ERROR
    global _RUNTIME_LOADING, _RUNTIME_ATTEMPTS
    global BOOTSTRAP_IMPORT_ERROR, BUDGET_DB_BRIDGE_SOURCE

    with _RUNTIME_LOCK:
        if _RUNTIME_APP is not None:
            return _RUNTIME_APP, ""
        if _RUNTIME_LOADING:
            return None, "RUNTIME_IMPORT_IN_PROGRESS"
        _RUNTIME_LOADING = True
        _RUNTIME_ATTEMPTS += 1

    try:
        runtime = importer("vnext_clean_app")
        runtime_app_value = runtime.app

        # The real FastAPI lifespan is not entered because the bootstrap remains
        # the top-level ASGI app. Reproduce only its tiny scheduling side effect.
        try:
            if bool(getattr(runtime, "TEST_MODE", False)):
                runtime.schedule_backend_init()
            else:
                runtime.schedule_cold_start()
        except Exception as exc:
            print(
                "G2B_RUNTIME_POST_IMPORT_SCHEDULE_DEGRADED",
                type(exc).__name__,
                flush=True,
            )

        source = ""
        try:
            source = bridge_cafe24_budget_database_url()
        except Exception:
            source = ""

        with _RUNTIME_LOCK:
            _RUNTIME_MODULE = runtime
            _RUNTIME_APP = runtime_app_value
            _RUNTIME_ERROR = ""
            _RUNTIME_LOADING = False
            BOOTSTRAP_IMPORT_ERROR = ""
            BUDGET_DB_BRIDGE_SOURCE = source
        print("G2B_VNEXT_RUNTIME_LOADED", APP_VERSION, flush=True)
        if source:
            print("G2B_DATABASE_SOURCE", source, flush=True)
        return runtime_app_value, ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        public_error = _public_error(error)
        with _RUNTIME_LOCK:
            _RUNTIME_ERROR = error
            _RUNTIME_LOADING = False
            BOOTSTRAP_IMPORT_ERROR = error
        print("G2B_VNEXT_IMPORT_FAILURE", public_error, flush=True)
        traceback.print_exc()
        return None, error


def _runtime_loader_worker():
    global _RUNTIME_THREAD
    current = threading.current_thread()
    try:
        delay = _runtime_import_delay_seconds()
        if delay:
            time.sleep(delay)
        load_runtime_now()
    finally:
        with _RUNTIME_LOCK:
            if _RUNTIME_THREAD is current:
                _RUNTIME_THREAD = None


def schedule_runtime_load():
    global _RUNTIME_THREAD
    with _RUNTIME_LOCK:
        if _RUNTIME_APP is not None or _RUNTIME_LOADING:
            return False
        existing = _RUNTIME_THREAD
        if existing is not None and (
            existing.is_alive()
            or getattr(existing, "ident", None) is None
        ):
            return False
        thread = threading.Thread(
            target=_runtime_loader_worker,
            name="g2b-v4-runtime-import",
            daemon=True,
        )
        _RUNTIME_THREAD = thread
        try:
            thread.start()
        except Exception as exc:
            if _RUNTIME_THREAD is thread:
                _RUNTIME_THREAD = None
            print(
                "G2B_RUNTIME_IMPORT_THREAD_DEGRADED",
                type(exc).__name__,
                flush=True,
            )
            return False
    return True


@asynccontextmanager
async def _bootstrap_lifespan(_app):
    # This is the only startup action before uvicorn begins serving requests.
    # Thread creation is cheap and the worker sleeps in production before imports.
    try:
        schedule_runtime_load()
    except Exception as exc:
        print(
            "G2B_RUNTIME_IMPORT_SCHEDULE_DEGRADED",
            type(exc).__name__,
            flush=True,
        )
    yield


bootstrap = FastAPI(
    title="SINSUNG G2B vNext bootstrap",
    version=APP_VERSION,
    lifespan=_bootstrap_lifespan,
)


@bootstrap.get("/live")
def bootstrap_live():
    state = _runtime_snapshot()
    return {
        "status": "ok",
        "process_alive": True,
        "runtime": (
            "G2B_VNEXT_CLEAN"
            if state["app"] is not None
            else "G2B_VNEXT_BOOTSTRAP"
        ),
        "runtime_loaded": state["app"] is not None,
        "runtime_loading": state["loading"],
        "runtime_role": _runtime_role_label(),
        "runtime_import_attempts": state["attempts"],
        "version": APP_VERSION,
    }


@bootstrap.get("/__ai_space_health")
def bootstrap_ai_space_health():
    state = _runtime_snapshot()
    return {
        "status": "ok",
        "process_alive": True,
        "runtime": "G2B_VNEXT_BOOTSTRAP",
        "runtime_loaded": state["app"] is not None,
        "runtime_loading": state["loading"],
        "runtime_role": _runtime_role_label(),
        "version": APP_VERSION,
    }


@bootstrap.get("/health")
def bootstrap_health():
    state = _runtime_snapshot()
    runtime = state["module"]
    if runtime is not None:
        try:
            return runtime.health()
        except Exception as exc:
            return {
                "status": "ok",
                "process_alive": True,
                "backend_ok": False,
                "runtime": "G2B_VNEXT_BOOTSTRAP",
                "runtime_loaded": True,
                "runtime_error": _public_error(type(exc).__name__),
                "version": APP_VERSION,
            }
    if state["error"]:
        return {
            "status": "ok",
            "process_alive": True,
            "backend_ok": False,
            "runtime": "G2B_VNEXT_BOOTSTRAP",
            "runtime_loaded": False,
            "import_ok": False,
            "import_error": _public_error(state["error"]),
            "version": APP_VERSION,
        }
    schedule_runtime_load()
    return {
        "status": "ok",
        "process_alive": True,
        "backend_ok": False,
        "runtime": "G2B_VNEXT_BOOTSTRAP",
        "runtime_loaded": False,
        "runtime_loading": bool(_runtime_snapshot()["loading"]),
        "version": APP_VERSION,
    }


@bootstrap.get("/ready")
def bootstrap_ready():
    state = _runtime_snapshot()
    runtime = state["module"]
    if runtime is not None:
        try:
            return runtime.ready()
        except Exception as exc:
            return JSONResponse(
                {
                    "status": "not_ready",
                    "backend_ok": False,
                    "runtime": "G2B_VNEXT_BOOTSTRAP",
                    "runtime_error": _public_error(type(exc).__name__),
                },
                status_code=503,
            )
    schedule_runtime_load()
    return JSONResponse(
        {
            "status": "not_ready",
            "backend_ok": False,
            "runtime": "G2B_VNEXT_BOOTSTRAP",
            "runtime_loaded": False,
            "runtime_loading": bool(_runtime_snapshot()["loading"]),
            "import_error": _public_error(state["error"]),
        },
        status_code=503,
    )


@bootstrap.get("/")
def bootstrap_root():
    if runtime_loaded():
        return RedirectResponse("/login", 302)
    state = _runtime_snapshot()
    detail = (
        "전체 애플리케이션을 준비 중입니다."
        if not state["error"]
        else "전체 애플리케이션 모듈을 불러오지 못했습니다."
    )
    error_html = (
        f"<pre>{_public_error(state['error'])}</pre>"
        if state["error"] else ""
    )
    return HTMLResponse(
        "<!doctype html><html lang='ko'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>SINSUNG G2B vNext</title>"
        "<body style='font-family:sans-serif;padding:32px'>"
        "<h2>SINSUNG G2B vNext</h2>"
        f"<p>{detail}</p>{error_html}"
        "<p><a href='/live'>기동 상태</a> · "
        "<a href='/health'>상태 확인</a></p></body></html>",
        status_code=200,
    )


class _RuntimeProxy:
    async def __call__(self, scope, receive, send):
        state = _runtime_snapshot()
        target = state["app"]
        if target is not None:
            await target(scope, receive, send)
            return

        schedule_runtime_load()
        if scope.get("type") == "http":
            response = HTMLResponse(
                "<!doctype html><html lang='ko'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<body style='font-family:sans-serif;padding:32px'>"
                "<h3>G2B 애플리케이션 준비 중</h3>"
                "<p>HTTP 서버는 정상 기동했습니다. 잠시 후 다시 접속해 주세요.</p>"
                "</body></html>",
                status_code=503,
            )
            await response(scope, receive, send)
            return

        # No websocket/runtime protocol is expected before the full app exists.
        await JSONResponse(
            {"status": "not_ready"},
            status_code=503,
        )(scope, receive, send)


# The mount exists from process import time; it dynamically forwards once loaded.
# Bootstrap liveness routes above take precedence over this catch-all mount.
bootstrap.mount("/", _RuntimeProxy())

# Uvicorn imports this lightweight app only.  The full runtime is never imported
# as a side effect of importing main.py.
app = bootstrap

__all__ = [
    "app",
    "bootstrap",
    "BOOTSTRAP_IMPORT_ERROR",
    "BUDGET_DB_BRIDGE_SOURCE",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
    "load_runtime_now",
    "runtime_app",
    "runtime_loaded",
    "schedule_runtime_load",
]
