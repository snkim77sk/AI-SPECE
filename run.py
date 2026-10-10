"""Portable Cafe24 launcher with last-resort HTTP failover.

Normal operation remains the existing direct Uvicorn -> main:app path. If Uvicorn
itself or main:app startup fails, keep the Cafe24 PORT alive with a tiny stdlib
HTTP server instead of letting the platform collapse to a generic 502. The
recovery server never opens PostgreSQL, runs maintenance, or calls source APIs.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import memory_guard

# Apply conservative native allocator/thread defaults before importing Uvicorn or
# the application stack. Explicit non-empty operator values remain untouched.
memory_guard.apply_default_process_tuning()

from app_version import APP_VERSION
from runtime_identity import deployment_verdict_info, runtime_identity

VERSION = APP_VERSION


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


def _json_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _recovery_snapshot():
    identity = runtime_identity()
    return {
        "status": "ok",
        "process_alive": True,
        "runtime": "G2B_LAUNCHER_RECOVERY",
        "version": VERSION,
        "recovery_mode": True,
        "database_touched": False,
        "source_io_performed": False,
        **identity,
        **deployment_verdict_info(
            identity,
            phase="LAUNCHER_RECOVERY",
            recovery_mode=True,
        ),
    }


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
</style>
</head>
<body><div class="wrap"><div class="card">
<h1>SINSUNG G2B 복구모드</h1>
<p class="ok">웹 프로세스가 복구 HTTP로 살아 있습니다.</p>
<p>정상 애플리케이션 기동에 실패해 DB/API 작업 없이 진단 경로만 제공합니다.</p>
</div></div></body></html>""".encode("utf-8")


class RecoveryHandler(BaseHTTPRequestHandler):
    server_version = "SINSUNG-G2B-Recovery"
    sys_version = ""

    def _headers(self, content_type, content_length):
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-G2B-Version", VERSION)

    def _send_json(self, status, payload):
        body = _json_bytes(payload)
        self.send_response(status)
        self._headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, status):
        self.send_response(status)
        self._headers("text/html; charset=utf-8", len(_RECOVERY_ROOT))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(_RECOVERY_ROOT)

    def _route(self):
        path = self.path.split("?", 1)[0]
        snapshot = _recovery_snapshot()
        if path in {"/live", "/__ai_space_health"}:
            self._send_json(200, snapshot)
            return
        if path == "/health":
            self._send_json(200, {
                **snapshot,
                "backend_ok": False,
                "operational_ready": False,
            })
            return
        if path == "/ready":
            self._send_json(503, {
                **snapshot,
                "status": "not_ready",
                "backend_ok": False,
                "operational_ready": False,
            })
            return
        if path == "/":
            self._send_html(200)
            return
        self._send_html(503)

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


def _run_full_runtime():
    # Import Uvicorn only here. If dependency installation is incomplete, the
    # outer launcher can still fall back to the stdlib recovery HTTP server.
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
    print(
        f"G2B_LAUNCHER_RECOVERY_LISTENING 0.0.0.0:{port} v{VERSION}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


def _run_full_runtime_with_fallback():
    """Keep the web process alive when launcher/import startup fails."""
    try:
        _run_full_runtime()
        return
    except KeyboardInterrupt:
        raise
    except SystemExit as exc:
        if exc.code in (None, 0):
            return
        print(
            "G2B_FULL_RUNTIME_LAUNCH_FAILED",
            f"SystemExit:{exc.code}",
            flush=True,
        )
    except Exception as exc:
        print(
            "G2B_FULL_RUNTIME_LAUNCH_FAILED",
            type(exc).__name__,
            flush=True,
        )

    _run_emergency_http()


def main():
    print(
        f"G2B_LAUNCHER_START port={resolve_port()} v{VERSION}",
        flush=True,
    )
    _run_full_runtime_with_fallback()


if __name__ == "__main__":
    main()
