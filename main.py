"""Cafe24 AI SPACE bootstrap entrypoint for SINSUNG G2B vNext.

The bootstrap itself is intentionally tiny. If the full runtime import fails for
any reason, an ASGI fallback still binds the HTTP port and exposes diagnostics
instead of letting the platform collapse to a generic 502.
"""
from __future__ import annotations

import importlib
import os
import traceback
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

_TRUE = ("1", "true", "yes", "on")


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def bridge_cafe24_budget_database_url(environ=None):
    """Deprecated 4.0 compatibility hook.

    G2B 4.1 resolves the canonical PostgreSQL connection inside g2b_database and
    does not mutate environment variables at bootstrap.  The function remains for
    one release so old diagnostics/tests can call it safely; it returns the source
    label only and never exposes credentials.
    """
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
    try:
        runtime = importer("vnext_clean_app")
        app = runtime.app
        return app, ""
    except Exception as exc:
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


try:
    BUDGET_DB_BRIDGE_SOURCE = bridge_cafe24_budget_database_url()
    if BUDGET_DB_BRIDGE_SOURCE:
        print("G2B_DATABASE_SOURCE", BUDGET_DB_BRIDGE_SOURCE, flush=True)
    elif not _flag_on("G2B_TEST_MODE"):
        print("G2B_DATABASE_SOURCE_MISSING", flush=True)
except Exception as _bridge_exc:  # never block process start
    BUDGET_DB_BRIDGE_SOURCE = ""
    print("G2B_DATABASE_SOURCE_CHECK_FAILED", type(_bridge_exc).__name__, flush=True)

app, BOOTSTRAP_IMPORT_ERROR = build_runtime()

__all__ = [
    "app",
    "BOOTSTRAP_IMPORT_ERROR",
    "BUDGET_DB_BRIDGE_SOURCE",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
]
