"""Cafe24 AI SPACE bootstrap entrypoint for SINSUNG G2B vNext.

Production recovery defaults to a dependency-free ASGI application so Cafe24 can
bind and probe the HTTP process even when it launches `main:app` directly instead
of honoring the Procfile. The default path imports only the Python standard
library and never opens PostgreSQL.

Production binds a lightweight ASGI shell first, then loads the complete G2B
runtime in a daemon thread and starts PostgreSQL initialization in the background.
Emergency-only mode remains available through G2B_EMERGENCY_ONLY=1.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
import traceback

from runtime_identity import deployment_verdict_info, runtime_identity

VERSION = "4.1.160"
_TRUE = ("1", "true", "yes", "on")


def _flag_on(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def emergency_only_enabled():
    return _flag_on("G2B_EMERGENCY_ONLY") or _flag_on("G2B_FULL_RUNTIME_DISABLE")


def full_runtime_enabled():
    # 4.1.161 normal operation no longer requires a positive enable flag.
    # Legacy G2B_FULL_RUNTIME_ENABLE=0 values from recovery instructions are
    # intentionally ignored so stale Cafe24 environment state cannot trap the
    # service in Phase 0 forever.
    if _flag_on("G2B_TEST_MODE"):
        return True
    return not emergency_only_enabled()


def _recovery_gate_snapshot():
    phase = "PHASE0_EMERGENCY_ASGI"
    identity = runtime_identity()
    return {
        "phase": phase,
        "full_runtime_enable": full_runtime_enabled(),
        "emergency_only": emergency_only_enabled(),
        "legacy_full_runtime_enable": _flag_on("G2B_FULL_RUNTIME_ENABLE"),
        "backend_init_enable": not _flag_on("G2B_BACKEND_INIT_DISABLE"),
        "legacy_backend_init_enable": _flag_on("G2B_BACKEND_INIT_ENABLE"),
        "post_boot_maintenance_enable": _flag_on("G2B_POST_BOOT_MAINTENANCE_ENABLE"),
        "auto_sync_requested": _flag_on("G2B_AUTO_SYNC"),
        "auto_sync_disabled": _flag_on("G2B_AUTO_SYNC_DISABLE"),
        "fresh_start_requested": _flag_on("G2B_V41_FRESH_START"),
        "destructive_reset_confirmed": _flag_on("G2B_DESTRUCTIVE_RESET_CONFIRM"),
        "database_touched": False,
        **identity,
        **deployment_verdict_info(
            identity,
            phase=phase,
            recovery_mode=True,
        ),
    }


def _resolve_port(value=None):
    raw = str(
        value if value is not None else os.getenv("PORT", "8000") or "8000"
    ).strip()
    try:
        port = int(raw)
    except ValueError:
        port = 8000
    if not 1 <= port <= 65535:
        port = 8000
    return port


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
<p>버전 <code>4.1.160</code></p>
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
                **_recovery_gate_snapshot(),
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
                **_recovery_gate_snapshot(),
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
                **_recovery_gate_snapshot(),
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
            (b"x-g2b-version", VERSION.encode("ascii")),
            (b"x-g2b-recovery-phase", b"PHASE0_EMERGENCY_ASGI"),
        ]
        identity = runtime_identity()
        build_commit = identity["build_commit"]
        if build_commit:
            headers.append((b"x-g2b-build-commit", build_commit.encode("ascii")))
        headers.append((
            b"x-g2b-build-commit-source",
            identity["build_commit_source"].encode("ascii"),
        ))
        headers.append((
            b"x-g2b-source-fingerprint",
            identity["source_fingerprint"].encode("ascii"),
        ))
        headers.append((
            b"x-g2b-process-instance",
            identity["process_instance_id"].encode("ascii"),
        ))
        headers.append((
            b"x-g2b-process-started-at",
            identity["process_started_at_utc"].encode("ascii"),
        ))
        verdict = deployment_verdict_info(
            identity,
            phase="PHASE0_EMERGENCY_ASGI",
            recovery_mode=True,
        )
        headers.append((
            b"x-g2b-deployment-verdict",
            verdict["deployment_verdict"].encode("ascii"),
        ))
        headers.append((
            b"x-g2b-freshness-verified",
            b"1" if verdict["deployment_freshness_verified"] else b"0",
        ))
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        })
        await send({
            "type": "http.response.body",
            "body": b"" if method == "HEAD" else body,
        })


class ProgressiveASGIApp:
    """Bind HTTP first, then attach the full FastAPI runtime in the background."""

    def __init__(self):
        self._recovery = RecoveryASGIApp()
        self._runtime_app = None
        self._runtime_module = None
        self._runtime_error = ""
        self._loader_started = False
        self._loader_lock = threading.Lock()

    @property
    def runtime_loaded(self):
        return self._runtime_app is not None

    @property
    def runtime_error(self):
        return str(self._runtime_error or "")

    def start_runtime_load(self):
        if emergency_only_enabled():
            return False
        with self._loader_lock:
            if self._loader_started:
                return False
            self._loader_started = True
            thread = threading.Thread(
                target=self._load_runtime,
                name="g2b-progressive-runtime-loader",
                daemon=True,
            )
            thread.start()
        return True

    def _load_runtime(self):
        global BUDGET_DB_BRIDGE_SOURCE, BOOTSTRAP_IMPORT_ERROR
        try:
            try:
                BUDGET_DB_BRIDGE_SOURCE = bridge_cafe24_budget_database_url()
                if BUDGET_DB_BRIDGE_SOURCE:
                    print(
                        "G2B_DATABASE_SOURCE",
                        BUDGET_DB_BRIDGE_SOURCE,
                        flush=True,
                    )
                else:
                    print("G2B_DATABASE_SOURCE_MISSING", flush=True)
            except Exception as bridge_exc:
                BUDGET_DB_BRIDGE_SOURCE = ""
                print(
                    "G2B_DATABASE_SOURCE_CHECK_FAILED",
                    type(bridge_exc).__name__,
                    flush=True,
                )

            runtime = importlib.import_module("vnext_clean_app")
            runtime_app = runtime.app
            self._runtime_module = runtime
            self._runtime_app = runtime_app
            BOOTSTRAP_IMPORT_ERROR = ""
            print(f"G2B_FULL_RUNTIME_ATTACHED v{VERSION}", flush=True)

            try:
                runtime.schedule_backend_init()
            except Exception as backend_exc:
                print(
                    "G2B_BACKEND_AUTO_INIT_SCHEDULE_FAILED",
                    type(backend_exc).__name__,
                    flush=True,
                )
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
            self._runtime_error = error
            BOOTSTRAP_IMPORT_ERROR = error
            print("G2B_PROGRESSIVE_RUNTIME_IMPORT_FAILURE", error, flush=True)
            traceback.print_exc()

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")

        if scope_type == "lifespan":
            while True:
                message = await receive()
                message_type = message.get("type")
                if message_type == "lifespan.startup":
                    self.start_runtime_load()
                    await send({"type": "lifespan.startup.complete"})
                elif message_type == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        if scope_type == "http":
            # Some ASGI hosts disable lifespan. The first request still starts
            # the loader while the recovery shell remains responsive.
            self.start_runtime_load()
            runtime_app = self._runtime_app
            if runtime_app is not None:
                await runtime_app(scope, receive, send)
                return

        await self._recovery(scope, receive, send)


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
                "version": VERSION,
                "import_ok": False,
                **runtime_identity(),
            }

        @fallback.get("/health")
        @fallback.get("/__ai_space_health")
        def health():
            return {
                "status": "ok",
                "process_alive": True,
                "backend_ok": False,
                "runtime": "G2B_VNEXT_BOOTSTRAP",
                "version": VERSION,
                "import_ok": False,
                "import_error": public_error,
                **runtime_identity(),
            }

        @fallback.get("/ready")
        def ready():
            return JSONResponse(
                {
                    "status": "not_ready",
                    "backend_ok": False,
                    "runtime": "G2B_VNEXT_BOOTSTRAP",
                    "version": VERSION,
                    "import_error": public_error,
                    **runtime_identity(),
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

if _flag_on("G2B_TEST_MODE"):
    # Unit/integration tests keep the deterministic direct import path.
    app, BOOTSTRAP_IMPORT_ERROR = build_runtime()
elif full_runtime_enabled():
    # Production binds this tiny wrapper first. Full runtime import and backend
    # initialization happen only after the ASGI server has started accepting
    # lifespan/HTTP events.
    app = ProgressiveASGIApp()
    print(f"G2B_PROGRESSIVE_BOOTSTRAP_ACTIVE v{VERSION}", flush=True)
else:
    app = RecoveryASGIApp()
    print(f"G2B_EMERGENCY_ASGI_RECOVERY_ACTIVE v{VERSION}", flush=True)
    print(
        "G2B_RECOVERY_GATE_STATE "
        + json.dumps(
            _recovery_gate_snapshot(),
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def _run_as_script():
    """Support platforms that execute `python main.py` directly."""
    port = _resolve_port()
    if full_runtime_enabled():
        import uvicorn

        forwarded = str(os.getenv("FORWARDED_ALLOW_IPS", "*") or "*").strip()
        print(
            f"G2B_DIRECT_MAIN_PROGRESSIVE_LISTENING 0.0.0.0:{port} v{VERSION}",
            flush=True,
        )
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            proxy_headers=True,
            forwarded_allow_ips=forwarded,
            access_log=True,
            server_header=False,
        )
        return

    # Reuse the dependency-free emergency HTTP launcher so direct script
    # execution remains database-free and does not require FastAPI/Uvicorn.
    from run import _run_emergency_http

    print(
        f"G2B_DIRECT_MAIN_EMERGENCY_LAUNCH v{VERSION}",
        flush=True,
    )
    _run_emergency_http()


if __name__ == "__main__":
    _run_as_script()


__all__ = [
    "app",
    "VERSION",
    "RecoveryASGIApp",
    "ProgressiveASGIApp",
    "emergency_only_enabled",
    "BOOTSTRAP_IMPORT_ERROR",
    "BUDGET_DB_BRIDGE_SOURCE",
    "build_runtime",
    "bridge_cafe24_budget_database_url",
    "full_runtime_enabled",
]
