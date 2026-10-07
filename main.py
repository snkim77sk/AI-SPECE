"""Cafe24 AI SPACE bootstrap entrypoint for SINSUNG G2B vNext.

Production must bind HTTP before importing the full application stack.  A tiny
ASGI bootstrap starts immediately, imports vnext_clean_app in a daemon thread,
and delegates every request to the real FastAPI app as soon as that import
finishes.  Browser GETs receive a small warmup page instead of a connection gap.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import traceback

from app_version import APP_VERSION

_TRUE = ("1", "true", "yes", "on")


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def bridge_cafe24_budget_database_url(environ=None):
    """Deprecated 4.0 compatibility hook kept for diagnostics/tests."""
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


def build_runtime(importer=importlib.import_module):
    """Synchronous compatibility loader used by tests and explicit test mode."""
    try:
        runtime = importer("vnext_clean_app")
        app = runtime.app
        return app, ""
    except Exception as exc:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse

        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        public_error = _public_error(error)
        print("G2B_VNEXT_IMPORT_FAILURE", error, flush=True)
        traceback.print_exc()

        fallback = FastAPI(title="SINSUNG G2B vNext bootstrap")

        @fallback.get("/live")
        def live():
            return {
                "status": "ok",
                "process_alive": True,
                "runtime": "G2B_VNEXT_BOOTSTRAP",
                "import_ok": False,
                "version": APP_VERSION,
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
                "version": APP_VERSION,
            }

        @fallback.get("/ready")
        def ready():
            return JSONResponse(
                {
                    "status": "not_ready",
                    "backend_ok": False,
                    "runtime": "G2B_VNEXT_BOOTSTRAP",
                    "import_error": public_error,
                    "version": APP_VERSION,
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

        return fallback, error


def _warmup_html(error=""):
    if error:
        title = "G2B vNext 시작 실패"
        message = "웹 서버는 연결되었지만 애플리케이션 준비에 실패했습니다."
        detail = _public_error(error)
        refresh = ""
    else:
        title = "G2B vNext 시작 중"
        message = (
            "웹 서버는 연결되었습니다. 애플리케이션과 PostgreSQL 저장소를 "
            "백그라운드에서 준비 중이며 완료되면 자동으로 전환됩니다."
        )
        detail = "STARTING"
        refresh = "<meta http-equiv='refresh' content='2'>"
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        + refresh
        + f"<title>{title}</title><style>"
        "body{margin:0;background:#f4f6f9;color:#172033;font-family:Arial,sans-serif}"
        ".wrap{max-width:620px;margin:12vh auto;padding:20px}"
        ".card{background:#fff;border:1px solid #dde2ea;border-radius:18px;padding:28px}"
        ".bar{height:8px;background:#eef1f5;border-radius:999px;overflow:hidden;margin:18px 0}"
        ".bar span{display:block;width:42%;height:100%;background:#177d68;animation:p 1.1s ease-in-out infinite alternate}"
        "@keyframes p{from{transform:translateX(-35%)}to{transform:translateX(170%)}}"
        ".muted{color:#697386;line-height:1.6}</style></head><body>"
        "<main class='wrap'><section class='card'>"
        f"<h2>{title}</h2><div class='bar'><span></span></div>"
        f"<p class='muted'>{message}</p><p class='muted'>{detail}</p>"
        "</section></main></body></html>"
    ).encode("utf-8")


async def _send_response(send, status, body, content_type):
    headers = [
        (b"content-type", content_type.encode("ascii")),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    await send({"type": "http.response.start", "status": int(status), "headers": headers})
    await send({"type": "http.response.body", "body": body})


class LazyRuntimeApp:
    """Bind Uvicorn immediately and import the heavy runtime off the startup path."""

    def __init__(self, importer=importlib.import_module):
        self._importer = importer
        self._lock = threading.Lock()
        self._thread = None
        self._runtime_app = None
        self._runtime_error = ""

    @property
    def runtime_ready(self):
        return self._runtime_app is not None

    @property
    def runtime_error(self):
        return str(self._runtime_error or "")

    def _load_runtime(self):
        try:
            source = bridge_cafe24_budget_database_url()
            if source:
                print("G2B_DATABASE_SOURCE", source, flush=True)
            else:
                print("G2B_DATABASE_SOURCE_MISSING", flush=True)

            runtime = self._importer("vnext_clean_app")
            # The delegated FastAPI lifespan is not entered by Uvicorn because this
            # bootstrap owns the outer lifespan. Reproduce its only startup action
            # explicitly and keep it non-blocking.
            try:
                runtime.schedule_backend_init()
            except Exception as exc:
                print(
                    "G2B_BOOTSTRAP_BACKEND_INIT_DEGRADED",
                    type(exc).__name__,
                    flush=True,
                )
            with self._lock:
                self._runtime_app = runtime.app
                self._runtime_error = ""
            print("G2B_LAZY_RUNTIME_READY", APP_VERSION, flush=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
            with self._lock:
                self._runtime_error = error
            print("G2B_VNEXT_IMPORT_FAILURE", error, flush=True)
            traceback.print_exc()

    def start_loader(self):
        with self._lock:
            if self._runtime_app is not None:
                return False
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._runtime_error:
                return False
            thread = threading.Thread(
                target=self._load_runtime,
                name="g2b-lazy-runtime-loader",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return True

    async def _lifespan(self, receive, send):
        self.start_loader()
        while True:
            message = await receive()
            message_type = str(message.get("type") or "")
            if message_type == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message_type == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _bootstrap_http(self, scope, receive, send):
        path = str(scope.get("path") or "/")
        method = str(scope.get("method") or "GET").upper()
        error = self.runtime_error

        if path in {"/live", "/health", "/__ai_space_health"}:
            payload = {
                "status": "ok",
                "process_alive": True,
                "backend_ok": False,
                "runtime": (
                    "G2B_VNEXT_BOOTSTRAP_FAILED"
                    if error
                    else "G2B_VNEXT_BOOTSTRAP_LOADING"
                ),
                "runtime_loading": not bool(error),
                "import_ok": False,
                "import_error": _public_error(error),
                "version": APP_VERSION,
            }
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            await _send_response(send, 200, body, "application/json; charset=utf-8")
            return

        if path == "/ready":
            payload = {
                "status": "not_ready",
                "backend_ok": False,
                "runtime": (
                    "G2B_VNEXT_BOOTSTRAP_FAILED"
                    if error
                    else "G2B_VNEXT_BOOTSTRAP_LOADING"
                ),
                "runtime_loading": not bool(error),
                "import_error": _public_error(error),
                "version": APP_VERSION,
            }
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            await _send_response(send, 503, body, "application/json; charset=utf-8")
            return

        if method == "GET":
            await _send_response(
                send,
                503 if error else 200,
                _warmup_html(error),
                "text/html; charset=utf-8",
            )
            return

        body = json.dumps(
            {
                "status": "not_ready",
                "runtime_loading": not bool(error),
                "error": _public_error(error) or "RUNTIME_STARTING",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await _send_response(send, 503, body, "application/json; charset=utf-8")

    async def __call__(self, scope, receive, send):
        scope_type = str(scope.get("type") or "")
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
            return

        app = self._runtime_app
        if app is not None:
            await app(scope, receive, send)
            return

        self.start_loader()
        app = self._runtime_app
        if app is not None:
            await app(scope, receive, send)
            return

        if scope_type == "http":
            await self._bootstrap_http(scope, receive, send)
            return

        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return


if _flag_on("G2B_TEST_MODE"):
    try:
        BUDGET_DB_BRIDGE_SOURCE = bridge_cafe24_budget_database_url()
    except Exception:
        BUDGET_DB_BRIDGE_SOURCE = ""
    app, BOOTSTRAP_IMPORT_ERROR = build_runtime()
else:
    # Production leaves database/source imports to the loader thread so Uvicorn can
    # bind Cafe24's external PORT before the heavy application import begins.
    BUDGET_DB_BRIDGE_SOURCE = ""
    BOOTSTRAP_IMPORT_ERROR = ""
    app = LazyRuntimeApp()


__all__ = [
    "app",
    "BOOTSTRAP_IMPORT_ERROR",
    "BUDGET_DB_BRIDGE_SOURCE",
    "LazyRuntimeApp",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
]
