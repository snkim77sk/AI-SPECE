"""Ultra-light Cafe24 bootstrap for SINSUNG G2B vNext.

Production invariant:
- importing main.py never imports SQLAlchemy, PostgreSQL helpers, or vnext_clean_app;
- Uvicorn can bind the HTTP port first;
- /live and /__ai_space_health are always served by this tiny outer ASGI app;
- the full runtime is imported later in a daemon thread and then receives normal
  application traffic.

This protects 256MB Cafe24 cold-starts from runtime-import or DB-init work that
would otherwise happen before the platform can observe a live HTTP process.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import time
import traceback

_TRUE = ("1", "true", "yes", "on")
_BOOT_LOCK = threading.Lock()

BOOTSTRAP_IMPORT_ERROR = ""
BUDGET_DB_BRIDGE_SOURCE = ""


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def _runtime_import_delay_seconds():
    if _flag_on("G2B_TEST_MODE"):
        return 0.0
    raw = str(os.getenv("G2B_RUNTIME_IMPORT_DELAY_SECONDS", "8") or "8").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 8.0
    return max(2.0, min(value, 120.0))


def _bootstrap_version():
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION.txt")
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        return value.rsplit(" ", 1)[-1] if value else ""
    except OSError:
        return ""


def bridge_cafe24_budget_database_url(environ=None):
    """Compatibility diagnostic; never called during bootstrap import."""
    import g2b_database

    try:
        value = g2b_database.resolve_database_url(environ)
    except Exception:
        return ""
    return g2b_database.database_source_label() if value else ""


def _public_error(error):
    if _flag_on("G2B_TEST_MODE"):
        return str(error or "")
    text = str(error or "")
    return text.split(":", 1)[0][:120]


def _fallback_app(error):
    # Imported only when an explicit caller requests build_runtime() and the
    # runtime import failed. main.py module import itself stays stdlib-only.
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    public_error = _public_error(error)
    fallback = FastAPI(title="SINSUNG G2B vNext bootstrap")

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
    """Synchronous helper retained for regression diagnostics only."""
    try:
        runtime = importer("vnext_clean_app")
        return runtime.app, ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        print("G2B_VNEXT_IMPORT_FAILURE", error, flush=True)
        traceback.print_exc()
        return _fallback_app(error), error


async def _send_response(send, status, body, *, content_type="application/json; charset=utf-8"):
    if isinstance(body, str):
        payload = body.encode("utf-8")
    else:
        payload = json.dumps(
            body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    headers = [
        (b"content-type", content_type.encode("ascii")),
        (b"content-length", str(len(payload)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    await send({
        "type": "http.response.start",
        "status": int(status),
        "headers": headers,
    })
    await send({
        "type": "http.response.body",
        "body": payload,
        "more_body": False,
    })


class LazyRuntimeApp:
    """ASGI proxy that becomes live before importing the full application."""

    def __init__(self):
        self.runtime_app = None
        self.runtime_module = None
        self.import_error = ""
        self.import_started = False
        self.import_finished = False
        self._thread = None
        self.started_at = time.monotonic()

    def _load_runtime(self):
        global BOOTSTRAP_IMPORT_ERROR, BUDGET_DB_BRIDGE_SOURCE
        delay = _runtime_import_delay_seconds()
        if delay:
            time.sleep(delay)
        try:
            runtime = importlib.import_module("vnext_clean_app")
            runtime_app = runtime.app

            # The nested FastAPI lifespan is not invoked by this outer ASGI proxy,
            # so explicitly start only its background initialization entrypoint.
            try:
                if bool(getattr(runtime, "TEST_MODE", False)):
                    runtime.schedule_backend_init()
                else:
                    runtime.schedule_cold_start()
            except Exception as exc:
                print(
                    "G2B_LAZY_RUNTIME_POST_IMPORT_DEGRADED",
                    type(exc).__name__,
                    flush=True,
                )

            source = ""
            try:
                source = bridge_cafe24_budget_database_url()
            except Exception:
                source = ""

            with _BOOT_LOCK:
                self.runtime_module = runtime
                self.runtime_app = runtime_app
                self.import_error = ""
                self.import_finished = True
                BOOTSTRAP_IMPORT_ERROR = ""
                BUDGET_DB_BRIDGE_SOURCE = source
            print("G2B_LAZY_RUNTIME_IMPORT_OK", _bootstrap_version(), flush=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
            with _BOOT_LOCK:
                self.import_error = error
                self.import_finished = True
                BOOTSTRAP_IMPORT_ERROR = error
            print("G2B_VNEXT_IMPORT_FAILURE", error, flush=True)
            traceback.print_exc()

    def start_runtime_import(self):
        with _BOOT_LOCK:
            if self.import_started:
                return False
            self.import_started = True
            thread = threading.Thread(
                target=self._load_runtime,
                name="g2b-lazy-runtime-import",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception as exc:
                self.import_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                self.import_finished = True
                print(
                    "G2B_LAZY_RUNTIME_THREAD_DEGRADED",
                    type(exc).__name__,
                    flush=True,
                )
                return False
        return True

    def _bootstrap_state(self):
        with _BOOT_LOCK:
            return {
                "runtime_loaded": self.runtime_app is not None,
                "import_started": bool(self.import_started),
                "import_finished": bool(self.import_finished),
                "import_error": _public_error(self.import_error),
            }

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            kind = message.get("type")
            if kind == "lifespan.startup":
                # Starting a sleeping daemon is the only cold-start action.
                # No full runtime import happens on the lifespan call stack.
                try:
                    self.start_runtime_import()
                except Exception as exc:
                    print(
                        "G2B_LAZY_RUNTIME_START_DEGRADED",
                        type(exc).__name__,
                        flush=True,
                    )
                await send({"type": "lifespan.startup.complete"})
            elif kind == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _bootstrap_http(self, scope, send):
        path = str(scope.get("path") or "/")
        state = self._bootstrap_state()
        version = _bootstrap_version()

        if path in {"/live", "/__ai_space_health"}:
            await _send_response(send, 200, {
                "status": "ok",
                "process_alive": True,
                "runtime": "G2B_VNEXT_BOOTSTRAP",
                "runtime_loaded": state["runtime_loaded"],
                "import_started": state["import_started"],
                "version": version,
            })
            return

        if path == "/health":
            await _send_response(send, 200, {
                "status": "ok",
                "process_alive": True,
                "backend_ok": False,
                "runtime": "G2B_VNEXT_BOOTSTRAP",
                "runtime_loaded": False,
                "import_ok": False,
                "import_started": state["import_started"],
                "import_finished": state["import_finished"],
                "import_error": state["import_error"],
                "version": version,
            })
            return

        if path == "/ready":
            await _send_response(send, 503, {
                "status": "not_ready",
                "backend_ok": False,
                "runtime": "G2B_VNEXT_BOOTSTRAP",
                "runtime_loaded": False,
                "import_error": state["import_error"],
                "version": version,
            })
            return

        if path == "/":
            error_html = (
                f"<pre>{state['import_error']}</pre>"
                if state["import_error"] else ""
            )
            await _send_response(
                send,
                200,
                "<!doctype html><html lang='ko'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>SINSUNG G2B vNext</title>"
                "<body style='font-family:sans-serif;padding:32px'>"
                "<h2>SINSUNG G2B vNext</h2>"
                "<p>웹 서버가 먼저 기동되었습니다. 애플리케이션을 준비 중입니다.</p>"
                f"{error_html}</body></html>",
                content_type="text/html; charset=utf-8",
            )
            return

        await _send_response(send, 503, {
            "status": "starting",
            "runtime": "G2B_VNEXT_BOOTSTRAP",
            "runtime_loaded": False,
            "import_error": state["import_error"],
        })

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            await self._lifespan(receive, send)
            return

        if scope.get("type") != "http":
            return

        path = str(scope.get("path") or "/")
        # These two endpoints are *always* outer-bootstrap endpoints so platform
        # liveness can never depend on the full runtime after deployment.
        if path in {"/live", "/__ai_space_health"}:
            await self._bootstrap_http(scope, send)
            return

        runtime = self.runtime_app
        if runtime is not None:
            await runtime(scope, receive, send)
            return

        await self._bootstrap_http(scope, send)


# Critical: do not call build_runtime() here.
# Importing main.py must remain cheap enough for Uvicorn to bind the port first.
app = LazyRuntimeApp()

__all__ = [
    "app",
    "BOOTSTRAP_IMPORT_ERROR",
    "BUDGET_DB_BRIDGE_SOURCE",
    "LazyRuntimeApp",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
]
