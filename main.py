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


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in ("1", "true", "yes", "on")


def bridge_cafe24_budget_database_url(environ=None):
    """Compose G2B_BUDGET_DATABASE_URL from Cafe24 auto-injected DB_* variables.

    Cafe24 AI SPACE injects the project's own PostgreSQL credentials as
    DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD and does not allow secrets
    to be stored as ordinary environment variables. The budget store only reads
    G2B_BUDGET_DATABASE_URL, so bridge it here once at process start.

    - An explicit G2B_BUDGET_DATABASE_URL always wins.
    - Generic DATABASE_URL is still never used.
    - Skipped in G2B_TEST_MODE.
    - Credentials are never printed.
    Returns True only when a URL was composed.
    """
    env = os.environ if environ is None else environ
    if str(env.get("G2B_BUDGET_DATABASE_URL", "") or "").strip():
        return False
    if str(env.get("G2B_TEST_MODE", "0") or "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    host = str(env.get("DB_HOST", "") or "").strip()
    name = str(env.get("DB_NAME", "") or "").strip()
    user = str(env.get("DB_USER", "") or "").strip()
    password = str(env.get("DB_PASSWORD", "") or "")
    port = str(env.get("DB_PORT", "") or "").strip() or "5432"
    if not (host and name and user):
        return False
    auth = quote(user, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    env["G2B_BUDGET_DATABASE_URL"] = (
        f"postgresql://{auth}@{host}:{port}/{quote(name, safe='')}"
    )
    print("G2B_BUDGET_DATABASE_URL_BRIDGED_FROM_CAFE24_DB_ENV", flush=True)
    return True


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
    bridge_cafe24_budget_database_url()
except Exception as _bridge_exc:  # never block process start
    print("G2B_BUDGET_DATABASE_URL_BRIDGE_FAILED", type(_bridge_exc).__name__, flush=True)

app, BOOTSTRAP_IMPORT_ERROR = build_runtime()

__all__ = [
    "app",
    "BOOTSTRAP_IMPORT_ERROR",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
]
