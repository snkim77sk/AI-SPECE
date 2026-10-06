"""Cafe24 process supervisor for SINSUNG G2B vNext.

Production keeps a tiny stdlib HTTP process bound to the public Cafe24 PORT for
the lifetime of the container. The complete FastAPI/Uvicorn runtime runs in a
separate loopback child process and is proxied only after its platform-health
endpoint responds.

This separation is deliberate: an import crash, native crash, OOM kill, or other
forced termination of the full runtime must not also kill the public HTTP binder.
The supervisor automatically restarts the child with bounded backoff while the
public /live, /health, /ready, and root recovery responses stay available.

G2B_EMERGENCY_ONLY=1 or G2B_FULL_RUNTIME_DISABLE=1 disables the child entirely.
Source collection and destructive-reset policy remain owned by vnext_clean_app.
"""
from __future__ import annotations

import atexit
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runtime_identity import deployment_verdict_info, runtime_identity

VERSION = "4.1.162"
_TRUE = {"1", "true", "yes", "on"}
_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

_CHILD_LOCK = threading.Lock()
_CHILD_STOP = threading.Event()
_CHILD_PROCESS = None
_CHILD_STATE = {
    "enabled": False,
    "pid": 0,
    "port": 0,
    "ready": False,
    "starts": 0,
    "restarts": 0,
    "last_exit_code": None,
    "last_error": "",
    "started_at": 0.0,
    "ready_at": 0.0,
}


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


def _flag(name):
    return str(os.getenv(name, "0") or "0").strip().lower() in _TRUE


def emergency_only_enabled():
    return _flag("G2B_EMERGENCY_ONLY") or _flag("G2B_FULL_RUNTIME_DISABLE")


def full_runtime_enabled():
    # Legacy positive recovery gates are compatibility-only from 4.1.161 onward.
    if _flag("G2B_TEST_MODE"):
        return True
    return not emergency_only_enabled()


def _recovery_gate_snapshot():
    phase = (
        "PHASE0_EMERGENCY_HTTP"
        if emergency_only_enabled()
        else "PHASE0_SUPERVISOR_HTTP"
    )
    identity = runtime_identity()
    return {
        "phase": phase,
        "full_runtime_enable": full_runtime_enabled(),
        "emergency_only": emergency_only_enabled(),
        "legacy_full_runtime_enable": _flag("G2B_FULL_RUNTIME_ENABLE"),
        "backend_init_enable": not _flag("G2B_BACKEND_INIT_DISABLE"),
        "legacy_backend_init_enable": _flag("G2B_BACKEND_INIT_ENABLE"),
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
<title>SINSUNG G2B 기동중</title>
<style>
body{font-family:Arial,sans-serif;background:#f4f6f9;color:#172033;margin:0}
.wrap{max-width:720px;margin:48px auto;padding:20px}
.card{background:#fff;border:1px solid #dde2ea;border-radius:18px;padding:24px}
h1{font-size:24px;margin:0 0 12px}.ok{color:#0b7a53;font-weight:800}
code{background:#eef1f5;padding:2px 6px;border-radius:6px}
</style>
</head>
<body><div class="wrap"><div class="card">
<h1>SINSUNG G2B 자동 기동</h1>
<p class="ok">외부 HTTP 서비스는 정상 기동했습니다.</p>
<p>전체 프로그램을 별도 프로세스에서 자동으로 준비하고 있습니다.</p>
<p>전체 프로그램이 강제 종료되어도 이 화면과 상태 확인 경로는 유지됩니다.</p>
<p>PostgreSQL 데이터·예산자료·revision·checkpoint는 삭제하거나 초기화하지 않습니다.</p>
<p>버전 <code>4.1.162</code></p>
</div></div></body></html>""".encode("utf-8")


def _child_snapshot():
    with _CHILD_LOCK:
        state = dict(_CHILD_STATE)
    started_at = float(state.get("started_at") or 0.0)
    ready_at = float(state.get("ready_at") or 0.0)
    state["uptime_seconds"] = (
        max(0.0, round(time.monotonic() - started_at, 3))
        if started_at
        else 0.0
    )
    state["ready_seconds"] = (
        max(0.0, round(time.monotonic() - ready_at, 3))
        if ready_at
        else 0.0
    )
    return state


def _set_child_state(**values):
    with _CHILD_LOCK:
        _CHILD_STATE.update(values)


def _allocate_loopback_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _probe_child(port, path="/__ai_space_health", timeout=0.5):
    if not port:
        return False
    conn = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=timeout)
        conn.request("GET", path, headers={"Host": "127.0.0.1"})
        response = conn.getresponse()
        response.read(4096)
        return 200 <= int(response.status) < 300
    except (OSError, http.client.HTTPException, ValueError):
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _child_http_status(path, timeout=0.75):
    state = _child_snapshot()
    if not state.get("ready") or not state.get("port"):
        return None
    conn = None
    try:
        conn = http.client.HTTPConnection(
            "127.0.0.1",
            int(state["port"]),
            timeout=timeout,
        )
        conn.request("GET", path, headers={"Host": "127.0.0.1"})
        response = conn.getresponse()
        response.read(8192)
        return int(response.status)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _terminate_child():
    global _CHILD_PROCESS
    _CHILD_STOP.set()
    with _CHILD_LOCK:
        proc = _CHILD_PROCESS
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


atexit.register(_terminate_child)


def _child_supervisor_loop(external_port):
    global _CHILD_PROCESS

    backoff = 2.0
    first_start = True
    while not _CHILD_STOP.is_set() and full_runtime_enabled():
        child_port = _allocate_loopback_port()
        env = dict(os.environ)
        env["PORT"] = str(child_port)
        env["G2B_EXTERNAL_PORT"] = str(external_port)
        env["G2B_SUPERVISED_CHILD"] = "1"

        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "vnext_clean_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(child_port),
            "--proxy-headers",
            "--forwarded-allow-ips",
            "*",
            "--no-server-header",
        ]

        try:
            proc = subprocess.Popen(command, env=env)
        except Exception as exc:
            _set_child_state(
                enabled=True,
                pid=0,
                port=child_port,
                ready=False,
                last_error=f"SPAWN_{type(exc).__name__}",
            )
            print(
                "G2B_SUPERVISOR_CHILD_SPAWN_FAILED",
                type(exc).__name__,
                flush=True,
            )
            _CHILD_STOP.wait(backoff)
            backoff = min(backoff * 2.0, 30.0)
            continue

        with _CHILD_LOCK:
            _CHILD_PROCESS = proc
            _CHILD_STATE.update(
                enabled=True,
                pid=int(proc.pid or 0),
                port=child_port,
                ready=False,
                starts=int(_CHILD_STATE.get("starts") or 0) + 1,
                restarts=int(_CHILD_STATE.get("restarts") or 0)
                + (0 if first_start else 1),
                last_exit_code=None,
                last_error="",
                started_at=time.monotonic(),
                ready_at=0.0,
            )
        first_start = False

        print(
            f"G2B_SUPERVISOR_CHILD_STARTED pid={proc.pid} "
            f"127.0.0.1:{child_port} v{VERSION}",
            flush=True,
        )

        while not _CHILD_STOP.is_set():
            exit_code = proc.poll()
            if exit_code is not None:
                _set_child_state(
                    ready=False,
                    last_exit_code=int(exit_code),
                    last_error=f"CHILD_EXIT_{int(exit_code)}",
                )
                print(
                    f"G2B_SUPERVISOR_CHILD_EXIT pid={proc.pid} code={exit_code}",
                    flush=True,
                )
                break

            state = _child_snapshot()
            if not state.get("ready") and _probe_child(child_port):
                _set_child_state(
                    ready=True,
                    ready_at=time.monotonic(),
                    last_error="",
                )
                backoff = 2.0
                print(
                    f"G2B_SUPERVISOR_CHILD_READY pid={proc.pid} "
                    f"127.0.0.1:{child_port} v{VERSION}",
                    flush=True,
                )

            _CHILD_STOP.wait(0.5)

        if _CHILD_STOP.is_set():
            break
        _CHILD_STOP.wait(backoff)
        backoff = min(backoff * 2.0, 30.0)

    _set_child_state(ready=False)


class RecoveryHandler(BaseHTTPRequestHandler):
    server_version = "SINSUNG-G2B-Supervisor"
    sys_version = ""

    def _security_headers(self, phase=None):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-G2B-Version", VERSION)
        self.send_header(
            "X-G2B-Recovery-Phase",
            phase
            or (
                "PHASE0_EMERGENCY_HTTP"
                if emergency_only_enabled()
                else "PHASE0_SUPERVISOR_HTTP"
            ),
        )
        identity = runtime_identity()
        build_commit = identity["build_commit"]
        if build_commit:
            self.send_header("X-G2B-Build-Commit", build_commit)
        self.send_header("X-G2B-Build-Commit-Source", identity["build_commit_source"])
        self.send_header("X-G2B-Source-Fingerprint", identity["source_fingerprint"])
        self.send_header("X-G2B-Process-Instance", identity["process_instance_id"])
        self.send_header(
            "X-G2B-Process-Started-At",
            identity["process_started_at_utc"],
        )
        verdict = deployment_verdict_info(
            identity,
            phase=(
                "PHASE0_EMERGENCY_HTTP"
                if emergency_only_enabled()
                else "PHASE0_SUPERVISOR_HTTP"
            ),
            recovery_mode=True,
        )
        self.send_header("X-G2B-Deployment-Verdict", verdict["deployment_verdict"])
        self.send_header(
            "X-G2B-Freshness-Verified",
            "1" if verdict["deployment_freshness_verified"] else "0",
        )
        child = _child_snapshot()
        self.send_header(
            "X-G2B-Child-Ready",
            "1" if child.get("ready") else "0",
        )
        if child.get("pid"):
            self.send_header("X-G2B-Child-Pid", str(child["pid"]))

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

    def _supervisor_payload(self, *, ready=False):
        child = _child_snapshot()
        identity = runtime_identity()
        phase = (
            "PHASE0_EMERGENCY_HTTP"
            if emergency_only_enabled()
            else (
                "PHASE2_CHILD_RUNTIME_READY"
                if child.get("ready")
                else "PHASE0_SUPERVISOR_HTTP"
            )
        )
        operational_status = _child_http_status("/ready") if ready else None
        gates = _recovery_gate_snapshot()
        return {
            **gates,
            "status": (
                "ready"
                if operational_status == 200
                else (
                    "recovery_ready"
                    if not emergency_only_enabled()
                    else "emergency_ready"
                )
            ),
            "process_alive": True,
            "runtime": (
                "G2B_EMERGENCY_HTTP_RECOVERY"
                if emergency_only_enabled()
                else "G2B_PROCESS_SUPERVISOR"
            ),
            "version": VERSION,
            "phase": phase,
            "recovery_mode": not bool(child.get("ready")),
            "child_enabled": bool(child.get("enabled")),
            "child_ready": bool(child.get("ready")),
            "child_pid": int(child.get("pid") or 0),
            "child_port": int(child.get("port") or 0),
            "child_starts": int(child.get("starts") or 0),
            "child_restarts": int(child.get("restarts") or 0),
            "child_last_exit_code": child.get("last_exit_code"),
            "child_last_error": str(child.get("last_error") or ""),
            "child_uptime_seconds": child.get("uptime_seconds", 0.0),
            "child_ready_seconds": child.get("ready_seconds", 0.0),
            "operational_ready": operational_status == 200,
            "operational_ready_http_status": operational_status,
            **deployment_verdict_info(
                identity,
                phase=phase,
                recovery_mode=not bool(child.get("ready")),
            ),
        }

    def _proxy_to_child(self):
        state = _child_snapshot()
        if not state.get("ready") or not state.get("port"):
            return False

        conn = None
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1",
                int(state["port"]),
                timeout=30,
            )
            conn.putrequest(
                self.command,
                self.path,
                skip_host=True,
                skip_accept_encoding=True,
            )

            original_host = self.headers.get("Host") or "localhost"
            client_ip = self.client_address[0] if self.client_address else ""
            for key, value in self.headers.items():
                lower = key.lower()
                if lower in _HOP_HEADERS or lower in {
                    "host",
                    "x-forwarded-for",
                    "x-forwarded-host",
                    "x-forwarded-proto",
                }:
                    continue
                conn.putheader(key, value)
            conn.putheader("Host", original_host)
            conn.putheader("X-Forwarded-Host", original_host)
            conn.putheader(
                "X-Forwarded-Proto",
                self.headers.get("X-Forwarded-Proto") or "https",
            )
            if client_ip:
                incoming = self.headers.get("X-Forwarded-For")
                forwarded = f"{incoming}, {client_ip}" if incoming else client_ip
                conn.putheader("X-Forwarded-For", forwarded)
            conn.endheaders()

            raw_length = self.headers.get("Content-Length")
            try:
                remaining = max(0, int(raw_length or "0"))
            except ValueError:
                remaining = 0
            while remaining:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                conn.send(chunk)
                remaining -= len(chunk)

            response = conn.getresponse()
            self.send_response(int(response.status), str(response.reason or ""))
            has_length = False
            for key, value in response.getheaders():
                lower = key.lower()
                if lower in _HOP_HEADERS:
                    continue
                if lower == "content-length":
                    has_length = True
                self.send_header(key, value)
            self.send_header("X-G2B-Supervised-Child", "1")
            if not has_length:
                self.send_header("Connection", "close")
            self.end_headers()

            if self.command != "HEAD":
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return True
        except (
            BrokenPipeError,
            ConnectionError,
            OSError,
            http.client.HTTPException,
            ValueError,
        ) as exc:
            _set_child_state(
                ready=False,
                last_error=f"PROXY_{type(exc).__name__}",
            )
            print(
                "G2B_SUPERVISOR_PROXY_DEGRADED",
                type(exc).__name__,
                flush=True,
            )
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _route(self):
        path = self.path.split("?", 1)[0]

        # Platform-facing probes are always answered by the stable parent process.
        if path in {"/live", "/__ai_space_health"}:
            payload = self._supervisor_payload(ready=False)
            payload["status"] = "ok"
            self._send_json(200, payload)
            return

        if path == "/health":
            payload = self._supervisor_payload(ready=False)
            payload["status"] = "ok"
            child_status = _child_http_status("/health")
            payload["child_health_http_status"] = child_status
            self._send_json(200, payload)
            return

        if path == "/ready":
            # Platform readiness means the stable public binder is alive. The
            # payload separately reports whether the child+PostgreSQL are fully
            # operational so a DB outage cannot turn into a Cafe24 502 loop.
            self._send_json(200, self._supervisor_payload(ready=True))
            return

        if full_runtime_enabled() and self._proxy_to_child():
            return

        if path == "/":
            self._send_html(200, ROOT_HTML)
            return

        self._send_html(503, ROOT_HTML)

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_PUT(self):
        self._route()

    def do_PATCH(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def do_OPTIONS(self):
        self._route()

    def log_message(self, fmt, *args):
        sys.stdout.write(
            "G2B_SUPERVISOR_HTTP %s - %s\n"
            % (self.address_string(), fmt % args)
        )
        sys.stdout.flush()


class RecoveryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _run_full_runtime():
    """Test/local direct runtime path.

    Production does not use this function. Cafe24 production uses the stable
    parent supervisor below so full-runtime death cannot remove the public PORT.
    """
    try:
        import uvicorn
    except Exception as exc:
        print(
            "G2B_DIRECT_UVICORN_IMPORT_FAILED",
            type(exc).__name__,
            flush=True,
        )
        _run_emergency_http()
        return

    forwarded = str(os.getenv("FORWARDED_ALLOW_IPS", "*") or "*").strip()
    try:
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=resolve_port(),
            proxy_headers=True,
            forwarded_allow_ips=forwarded,
            access_log=True,
            server_header=False,
        )
    except Exception as exc:
        print(
            "G2B_DIRECT_UVICORN_START_FAILED",
            type(exc).__name__,
            flush=True,
        )
        _run_emergency_http()


def _run_emergency_http():
    port = resolve_port()
    server = RecoveryHTTPServer(("0.0.0.0", port), RecoveryHandler)
    print(
        f"G2B_EMERGENCY_HTTP_RECOVERY_LISTENING 0.0.0.0:{port} v{VERSION}",
        flush=True,
    )
    print(
        "G2B_RECOVERY_GATE_STATE "
        + json.dumps(
            _recovery_gate_snapshot(),
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


def _run_supervised_http():
    port = resolve_port()

    # Constructing ThreadingHTTPServer binds/listens before any full-runtime
    # imports or subprocess work begin.
    server = RecoveryHTTPServer(("0.0.0.0", port), RecoveryHandler)
    _set_child_state(enabled=True)

    def _shutdown(signum, _frame):
        print(f"G2B_SUPERVISOR_SIGNAL {signum}", flush=True)
        _terminate_child()
        raise SystemExit(0)

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(signum, _shutdown)
        except (AttributeError, ValueError):
            pass

    supervisor = threading.Thread(
        target=_child_supervisor_loop,
        args=(port,),
        name="g2b-cafe24-child-supervisor",
        daemon=True,
    )
    supervisor.start()

    print(
        f"G2B_SUPERVISOR_HTTP_LISTENING 0.0.0.0:{port} v{VERSION}",
        flush=True,
    )
    print(
        "G2B_SUPERVISOR_POLICY "
        + json.dumps(
            {
                "child_process_isolated": True,
                "automatic_child_restart": True,
                "legacy_full_runtime_enable_ignored": True,
                "legacy_backend_init_enable_ignored": True,
                "post_boot_maintenance_opt_in": True,
                "auto_sync_opt_in": True,
                "destructive_reset_two_flag_guard": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )

    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        _terminate_child()


def main():
    # Existing test-mode workflows keep the deterministic direct ASGI path.
    if _flag("G2B_TEST_MODE"):
        _run_full_runtime()
        return

    if full_runtime_enabled():
        _run_supervised_http()
        return

    _run_emergency_http()


if __name__ == "__main__":
    main()
