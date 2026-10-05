"""Emergency HTTP-only recovery launcher for Cafe24 AI SPACE.

This file intentionally imports only the Python standard library.
It does not import FastAPI, Uvicorn, PostgreSQL, SQLAlchemy, the G2B runtime,
collectors, or any project module.  The sole purpose of this recovery release is
to bind the Cafe24 PORT reliably so platform probes stop returning 502.

PostgreSQL data is untouched.  Full G2B runtime reattachment happens only after
this recovery release proves the platform process/PORT path is healthy.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runtime_identity import deployment_verdict_info, runtime_identity

VERSION = "4.1.159"


def resolve_port(value=None):
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


def _recovery_gate_snapshot():
    phase = "PHASE0_EMERGENCY_HTTP"
    identity = runtime_identity()
    return {
        "phase": phase,
        "full_runtime_enable": _flag("G2B_FULL_RUNTIME_ENABLE"),
        "backend_init_enable": _flag("G2B_BACKEND_INIT_ENABLE"),
        "post_boot_maintenance_enable": _flag("G2B_POST_BOOT_MAINTENANCE_ENABLE"),
        "auto_sync_requested": _flag("G2B_AUTO_SYNC"),
        "auto_sync_disabled": _flag("G2B_AUTO_SYNC_DISABLE"),
        "fresh_start_requested": _flag("G2B_V41_FRESH_START"),
        "destructive_reset_confirmed": _flag("G2B_DESTRUCTIVE_RESET_CONFIRM"),
        "database_touched": False,
        **identity,
        **deployment_verdict_info(
            identity,
            phase=phase,
            recovery_mode=True,
        ),
    }


def _json_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


ROOT_HTML = """<!doctype html>
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
<p>현재는 사이트 복구를 우선해 최소 HTTP 서버만 실행 중입니다.</p>
<p>PostgreSQL 데이터·예산자료·revision·checkpoint는 삭제하거나 초기화하지 않았습니다.</p>
<p>버전 <code>4.1.159</code></p>
</div></div></body></html>""".encode("utf-8")


class RecoveryHandler(BaseHTTPRequestHandler):
    server_version = "SINSUNG-G2B-Recovery"
    sys_version = ""

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-G2B-Version", VERSION)
        self.send_header("X-G2B-Recovery-Phase", "PHASE0_EMERGENCY_HTTP")
        identity = runtime_identity()
        build_commit = identity["build_commit"]
        if build_commit:
            self.send_header("X-G2B-Build-Commit", build_commit)
        self.send_header("X-G2B-Build-Commit-Source", identity["build_commit_source"])
        self.send_header("X-G2B-Source-Fingerprint", identity["source_fingerprint"])
        self.send_header("X-G2B-Process-Instance", identity["process_instance_id"])
        self.send_header("X-G2B-Process-Started-At", identity["process_started_at_utc"])
        verdict = deployment_verdict_info(
            identity,
            phase="PHASE0_EMERGENCY_HTTP",
            recovery_mode=True,
        )
        self.send_header("X-G2B-Deployment-Verdict", verdict["deployment_verdict"])
        self.send_header(
            "X-G2B-Freshness-Verified",
            "1" if verdict["deployment_freshness_verified"] else "0",
        )

    def _send_json(self, status, payload):
        body = _json_bytes(payload)
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, status, body):
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _route(self):
        path = self.path.split("?", 1)[0]
        if path in {"/live", "/__ai_space_health"}:
            self._send_json(200, {
                "status": "ok",
                "process_alive": True,
                "runtime": "G2B_EMERGENCY_HTTP_RECOVERY",
                "version": VERSION,
                "recovery_mode": True,
                **_recovery_gate_snapshot(),
            })
            return
        if path == "/health":
            self._send_json(200, {
                "status": "ok",
                "process_alive": True,
                "backend_ok": False,
                "runtime": "G2B_EMERGENCY_HTTP_RECOVERY",
                "version": VERSION,
                "recovery_mode": True,
                **_recovery_gate_snapshot(),
                "database_touched": False,
            })
            return
        if path == "/ready":
            # Recovery readiness means only that the HTTP platform path is healthy.
            # Full application readiness is intentionally not claimed.
            self._send_json(200, {
                "status": "recovery_ready",
                "process_alive": True,
                "backend_ok": False,
                "runtime": "G2B_EMERGENCY_HTTP_RECOVERY",
                "version": VERSION,
                "recovery_mode": True,
                **_recovery_gate_snapshot(),
            })
            return
        if path == "/":
            self._send_html(200, ROOT_HTML)
            return
        self._send_html(503, ROOT_HTML)

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def log_message(self, fmt, *args):
        sys.stdout.write(
            "G2B_RECOVERY_HTTP %s - %s\n"
            % (self.address_string(), fmt % args)
        )
        sys.stdout.flush()


class RecoveryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _flag(name):
    return str(os.getenv(name, "0") or "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


def full_runtime_enabled():
    # Tests/CI continue to exercise the complete runtime. Production recovery is
    # intentionally HTTP-only unless the owner explicitly enables full runtime.
    return _flag("G2B_TEST_MODE") or _flag("G2B_FULL_RUNTIME_ENABLE")


def _run_full_runtime():
    import uvicorn

    forwarded = str(os.getenv("FORWARDED_ALLOW_IPS", "*") or "*").strip()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=resolve_port(),
        proxy_headers=True,
        forwarded_allow_ips=forwarded,
        access_log=True,
        server_header=False,
    )


def _run_emergency_http():
    port = resolve_port()
    server = RecoveryHTTPServer(("0.0.0.0", port), RecoveryHandler)
    gates = _recovery_gate_snapshot()
    print(
        f"G2B_EMERGENCY_HTTP_RECOVERY_LISTENING 0.0.0.0:{port} v{VERSION}",
        flush=True,
    )
    print(
        "G2B_RECOVERY_GATE_STATE "
        + json.dumps(gates, sort_keys=True, separators=(",", ":")),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


def main():
    if full_runtime_enabled():
        _run_full_runtime()
        return
    _run_emergency_http()


if __name__ == "__main__":
    main()
