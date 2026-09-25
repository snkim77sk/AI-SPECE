"""Cafe24 AI SPACE bootstrap entrypoint for SINSUNG G2B vNext.

The bootstrap itself is intentionally tiny. If the full runtime import fails for
any reason, an ASGI fallback still binds the HTTP port and exposes diagnostics
instead of letting the platform collapse to a generic 502.
"""
from __future__ import annotations

import importlib
import traceback

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


def build_runtime(importer=importlib.import_module):
    try:
        runtime = importer("vnext_clean_app")
        app = runtime.app
        return app, ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:500]}"
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
                "import_error": error,
            }

        @fallback.get("/ready")
        def ready():
            return JSONResponse(
                {
                    "status": "not_ready",
                    "backend_ok": False,
                    "runtime": "G2B_VNEXT_BOOTSTRAP",
                    "import_error": error,
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
                f"<pre>{error}</pre></body></html>",
                status_code=200,
            )

        return fallback, error


app, BOOTSTRAP_IMPORT_ERROR = build_runtime()

__all__ = ["app", "BOOTSTRAP_IMPORT_ERROR", "build_runtime"]
