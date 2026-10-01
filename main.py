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
_PG_SCHEMES = ("postgres://", "postgresql://", "postgresql+psycopg://")


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def _get(env, *names):
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _compose(host, port, name, user, password):
    auth = quote(user, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    return f"postgresql://{auth}@{host}:{port or '5432'}/{quote(name, safe='')}"


def bridge_cafe24_budget_database_url(environ=None):
    """Bridge the hosting platform's own PostgreSQL credentials to the budget store.

    Cafe24 AI SPACE injects the project's PostgreSQL credentials automatically and
    does not allow DB secrets to be stored as ordinary environment variables. The
    budget store only reads G2B_BUDGET_DATABASE_URL, so compose it once here.

    Order: explicit G2B_BUDGET_DATABASE_URL (always wins) ->
    DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD ->
    PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD ->
    POSTGRES_URL / POSTGRESQL_URL / DATABASE_URL (PostgreSQL schemes only).
    Skipped in G2B_TEST_MODE. Credentials are never printed.
    Returns the source label when a URL was composed, else "".
    """
    env = os.environ if environ is None else environ
    if str(env.get("G2B_BUDGET_DATABASE_URL", "") or "").strip():
        return ""
    if str(env.get("G2B_TEST_MODE", "0") or "").strip().lower() in _TRUE:
        return ""

    url = ""
    source = ""
    host = _get(env, "DB_HOST")
    name = _get(env, "DB_NAME", "DB_DATABASE")
    user = _get(env, "DB_USER", "DB_USERNAME")
    if host and name and user:
        url = _compose(host, _get(env, "DB_PORT"), name, user,
                       str(env.get("DB_PASSWORD", "") or ""))
        source = "DB_*"
    if not url:
        host = _get(env, "PGHOST", "POSTGRES_HOST")
        name = _get(env, "PGDATABASE", "POSTGRES_DB")
        user = _get(env, "PGUSER", "POSTGRES_USER")
        if host and name and user:
            url = _compose(host, _get(env, "PGPORT", "POSTGRES_PORT"), name, user,
                           str(env.get("PGPASSWORD", "") or env.get("POSTGRES_PASSWORD", "") or ""))
            source = "PG*"
    if not url:
        for key in ("POSTGRES_URL", "POSTGRESQL_URL", "DATABASE_URL"):
            value = _get(env, key)
            if value.lower().startswith(_PG_SCHEMES):
                url = value
                source = key
                break
    if not url:
        print("G2B_BUDGET_DATABASE_URL_BRIDGE_NO_SOURCE", flush=True)
        return ""
    env["G2B_BUDGET_DATABASE_URL"] = url
    print("G2B_BUDGET_DATABASE_URL_BRIDGED_FROM", source, flush=True)
    return source


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
except Exception as _bridge_exc:  # never block process start
    BUDGET_DB_BRIDGE_SOURCE = ""
    print("G2B_BUDGET_DATABASE_URL_BRIDGE_FAILED", type(_bridge_exc).__name__, flush=True)

app, BOOTSTRAP_IMPORT_ERROR = build_runtime()

__all__ = [
    "app",
    "BOOTSTRAP_IMPORT_ERROR",
    "BUDGET_DB_BRIDGE_SOURCE",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
]
