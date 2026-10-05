"""Cafe24 AI SPACE bootstrap entrypoint for SINSUNG G2B vNext.

Production recovery defaults to a dependency-free ASGI application so Cafe24 can
bind and probe the HTTP process even when it launches `main:app` directly instead
of honoring the Procfile. The default path imports only the Python standard
library and never opens PostgreSQL.

The complete G2B runtime is loaded only when G2B_TEST_MODE=1 or
G2B_FULL_RUNTIME_ENABLE=1.
"""
from __future__ import annotations

import importlib
import json
import os
import traceback

VERSION = "4.1.150"
_TRUE = ("1", "true", "yes", "on")


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def full_runtime_enabled():
    return _flag_on("G2B_TEST_MODE") or _flag_on("G2B_FULL_RUNTIME_ENABLE")


def _json_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


_RECOVERY_ROOT = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SINSUNG G2B 복구모드</title>
<style>
body{font-family:Arial,sans-serif;background:#f4f6f9;color:#172033;margin:0}
.wrap{max-width:720px;margin:48px auto;padding:20px}
.card{background:#fff;border:1px solid #dde2ea;border-radius:18px;padding:24px}
h1{font-size:24px;margin:0 0 12px}.ok{color:#0b7a53;font-weight:800}
code{background:#eef1f5;padding:2px 6px;border-radius:6px}
</style>
</head>
<body><div class="wrap"><div class="card">
<h1>SINSUNG G2B 응급 복구모드</h1>
<p class="ok">HTTP 서비스가 정상 기동했습니다.</p>
<p>Cafe24가 <code>main:app</code>을 직접 실행하는 경로도 응급 복구모드로 보호합니다.</p>
<p>PostgreSQL 데이터·예산자료·revision·checkpoint는 삭제하거나 초기화하지 않았습니다.</p>
<p>버전 <code>4.1.150</code></p>
</div></div></body></html>""".encode("utf-8")


class RecoveryASGIApp:
    """Minimal dependency-free ASGI app for platform/process recovery."""

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            while True:
                message = await receive()
                message_type = message.get("type")
                if message_type == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message_type == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope_type != "http":
            return

        path = str(scope.get("path") or "/")
        method = str(scope.get("method") or "GET").upper()

        if path in {"/live", "/__ai_space_health"}:
            status = 200
            body = _json_bytes({
                "status": "ok",
                "process_alive": True,
                "runtime": "G2B_EMERGENCY_ASGI_RECOVERY",
                "version": VERSION,
                "recovery_mode": True,
            })
            content_type = b"application/json; charset=utf-8"
        elif path == "/health":
            status = 200
            body = _json_bytes({
                "status": "ok",
                "process_alive": True,
                "backend_ok": False,
                "runtime": "G2B_EMERGENCY_ASGI_RECOVERY",
                "version": VERSION,
                "recovery_mode": True,
                "database_touched": False,
            })
            content_type = b"application/json; charset=utf-8"
        elif path == "/ready":
            status = 200
            body = _json_bytes({
                "status": "recovery_ready",
                "process_alive": True,
                "backend_ok": False,
                "runtime": "G2B_EMERGENCY_ASGI_RECOVERY",
                "version": VERSION,
                "recovery_mode": True,
            })
            content_type = b"application/json; charset=utf-8"
        elif path == "/":
            status = 200
            body = _RECOVERY_ROOT
            content_type = b"text/html; charset=utf-8"
        else:
            status = 503
            body = _RECOVERY_ROOT
            content_type = b"text/html; charset=utf-8"

        headers = [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"cache-control", b"no-store"),
        ]
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        })
        await send({
            "type": "http.response.body",
            "body": b"" if method == "HEAD" else body,
        })


def bridge_cafe24_budget_database_url(environ=None):
    """Deprecated 4.0 compatibility hook used only by the full runtime path."""
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
    """Load the complete app, retaining the previous fail-soft FastAPI fallback."""
    try:
        runtime = importer("vnext_clean_app")
        runtime_app = runtime.app
        return runtime_app, ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
        public_error = _public_error(error)
        print("G2B_VNEXT_IMPORT_FAILURE", error, flush=True)
        traceback.print_exc()

        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse

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

        return fallback, error


BUDGET_DB_BRIDGE_SOURCE = ""
BOOTSTRAP_IMPORT_ERROR = ""

if full_runtime_enabled():
    try:
        BUDGET_DB_BRIDGE_SOURCE = bridge_cafe24_budget_database_url()
        if BUDGET_DB_BRIDGE_SOURCE:
            print("G2B_DATABASE_SOURCE", BUDGET_DB_BRIDGE_SOURCE, flush=True)
        elif not _flag_on("G2B_TEST_MODE"):
            print("G2B_DATABASE_SOURCE_MISSING", flush=True)
    except Exception as _bridge_exc:
        BUDGET_DB_BRIDGE_SOURCE = ""
        print(
            "G2B_DATABASE_SOURCE_CHECK_FAILED",
            type(_bridge_exc).__name__,
            flush=True,
        )

    app, BOOTSTRAP_IMPORT_ERROR = build_runtime()
else:
    app = RecoveryASGIApp()
    print(f"G2B_EMERGENCY_ASGI_RECOVERY_ACTIVE v{VERSION}", flush=True)


__all__ = [
    "app",
    "VERSION",
    "RecoveryASGIApp",
    "BOOTSTRAP_IMPORT_ERROR",
    "BUDGET_DB_BRIDGE_SOURCE",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
    "full_runtime_enabled",
]
