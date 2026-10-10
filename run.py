"""Cafe24 stdlib PORT guard and supervised G2B runtime.

The external Cafe24 PORT is owned by this standard-library-only parent process
before FastAPI, Uvicorn, PostgreSQL, SQLAlchemy, memory_guard, or any other
project runtime module is imported.  The full application runs on a loopback
child port and requests are proxied to it.

If the child cannot import, crashes, is OOM-killed, or is temporarily restarting,
the public PORT stays bound and returns a recovery response instead of allowing
openresty to collapse to 502.
"""
from __future__ import annotations

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


def _load_version():
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION.txt")
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
        return raw.split()[-1].strip() if raw else "unknown"
    except Exception:
        return "unknown"


VERSION = _load_version()
MAX_PROXY_REQUEST_BYTES = 16 * 1024 * 1024
PROXY_CONNECT_TIMEOUT_SECONDS = 3.0
PROXY_RESPONSE_TIMEOUT_SECONDS = 120.0
CHILD_START_TIMEOUT_SECONDS = 20.0
CHILD_RESTART_BACKOFF = (2, 4, 8, 15, 30)
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

_CHILD_LOCK = threading.Lock()
_CHILD_PROCESS = None
_CHILD_PORT = 0
_STOP_EVENT = threading.Event()


def _apply_process_tuning_env():
    defaults = {
        "MALLOC_ARENA_MAX": "2",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    for name, value in defaults.items():
        if not str(os.environ.get(name) or "").strip():
            os.environ[name] = value
    return {name: str(os.environ.get(name) or "") for name in defaults}


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


def _pick_internal_port(external_port):
    configured = str(os.getenv("G2B_INTERNAL_PORT", "") or "").strip()
    if configured:
        try:
            value = int(configured)
        except ValueError:
            value = 0
        if 1 <= value <= 65535 and value != int(external_port):
            return value

    for _ in range(5):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            value = int(sock.getsockname()[1])
        finally:
            sock.close()
        if value != int(external_port):
            return value
    raise RuntimeError("G2B_INTERNAL_PORT_UNAVAILABLE")


def _json_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _child_state():
    with _CHILD_LOCK:
        proc = _CHILD_PROCESS
        port = int(_CHILD_PORT or 0)
        pid = int(proc.pid) if proc is not None and proc.poll() is None else 0
        exit_code = None if proc is None or proc.poll() is None else proc.returncode
    return {
        "child_running": bool(pid),
        "child_pid": pid,
        "child_port": port,
        "child_exit_code": exit_code,
    }


def _recovery_snapshot():
    return {
        "status": "ok",
        "process_alive": True,
        "runtime": "G2B_PORT_GUARD_RECOVERY",
        "version": VERSION,
        "recovery_mode": True,
        "port_guard": True,
        "database_touched": False,
        "source_io_performed": False,
        "operational_ready": False,
        **_child_state(),
    }


_RECOVERY_ROOT = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="3">
<title>SINSUNG G2B 시작/복구 중</title>
<style>
body{font-family:Arial,sans-serif;background:#f4f6f9;color:#172033;margin:0}
.wrap{max-width:720px;margin:48px auto;padding:20px}
.card{background:#fff;border:1px solid #dde2ea;border-radius:18px;padding:24px}
h1{font-size:24px;margin:0 0 12px}.ok{color:#0b7a53;font-weight:800}
.muted{color:#697386;line-height:1.6}
</style>
</head>
<body><div class="wrap"><div class="card">
<h1>SINSUNG G2B 시작/복구 중</h1>
<p class="ok">Cafe24 외부 HTTP PORT는 정상 연결되어 있습니다.</p>
<p class="muted">내부 G2B 런타임을 준비하거나 자동 복구 중입니다. PostgreSQL 자료와 체크포인트는 삭제하지 않습니다.</p>
</div></div></body></html>""".encode("utf-8")


def _probe_child(port, path="/live", timeout=0.75):
    if not port:
        return False
    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=float(timeout))
    try:
        conn.request("GET", path, headers={"Connection": "close"})
        response = conn.getresponse()
        response.read(256 * 1024)
        return 200 <= int(response.status) < 500
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _spawn_runtime(port):
    env = os.environ.copy()
    env["G2B_SUPERVISED_CHILD"] = "1"
    env["G2B_INTERNAL_PORT"] = str(int(port))
    env["PORT"] = str(int(port))
    _apply_process_tuning_env()
    for name in (
        "MALLOC_ARENA_MAX",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
    ):
        env[name] = str(os.environ.get(name) or "")
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(int(port)),
        "--proxy-headers",
        "--forwarded-allow-ips",
        "*",
        "--no-server-header",
    ]
    print(
        f"G2B_CHILD_START port={int(port)} v{VERSION}",
        flush=True,
    )
    return subprocess.Popen(command, env=env)


def _stop_child():
    global _CHILD_PROCESS
    with _CHILD_LOCK:
        proc = _CHILD_PROCESS
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _supervisor_loop(port):
    global _CHILD_PROCESS, _CHILD_PORT
    failure_index = 0
    while not _STOP_EVENT.is_set():
        proc = None
        try:
            proc = _spawn_runtime(port)
            with _CHILD_LOCK:
                _CHILD_PROCESS = proc
                _CHILD_PORT = int(port)

            deadline = time.monotonic() + CHILD_START_TIMEOUT_SECONDS
            healthy = False
            while not _STOP_EVENT.is_set() and time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                if _probe_child(port):
                    healthy = True
                    break
                _STOP_EVENT.wait(0.2)

            if healthy:
                failure_index = 0
                print(
                    f"G2B_CHILD_HTTP_READY pid={proc.pid} port={int(port)}",
                    flush=True,
                )
                while not _STOP_EVENT.is_set() and proc.poll() is None:
                    _STOP_EVENT.wait(1.0)
            elif proc.poll() is None:
                print(
                    f"G2B_CHILD_START_TIMEOUT pid={proc.pid} port={int(port)}",
                    flush=True,
                )
                try:
                    proc.terminate()
                except Exception:
                    pass

            if _STOP_EVENT.is_set():
                break

            try:
                exit_code = proc.wait(timeout=5) if proc.poll() is None else proc.returncode
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                exit_code = proc.wait() if proc is not None else -1

            print(
                f"G2B_CHILD_EXIT code={exit_code} restart=1",
                flush=True,
            )
        except Exception as exc:
            print(
                "G2B_CHILD_SUPERVISOR_ERROR",
                type(exc).__name__,
                flush=True,
            )
        finally:
            with _CHILD_LOCK:
                if _CHILD_PROCESS is proc:
                    _CHILD_PROCESS = None

        wait_seconds = CHILD_RESTART_BACKOFF[
            min(failure_index, len(CHILD_RESTART_BACKOFF) - 1)
        ]
        failure_index = min(failure_index + 1, len(CHILD_RESTART_BACKOFF) - 1)
        _STOP_EVENT.wait(wait_seconds)


class PortGuardHandler(BaseHTTPRequestHandler):
    server_version = "SINSUNG-G2B-PortGuard"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def _headers(self, content_type, content_length=None):
        self.send_header("Content-Type", content_type)
        if content_length is not None:
            self.send_header("Content-Length", str(int(content_length)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-G2B-Version", VERSION)
        self.send_header("X-G2B-Port-Guard", "1")
        self.send_header("Connection", "close")

    def _send_json(self, status, payload):
        body = _json_bytes(payload)
        self.send_response(int(status))
        self._headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_html(self, status=200):
        self.send_response(int(status))
        self._headers("text/html; charset=utf-8", len(_RECOVERY_ROOT))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(_RECOVERY_ROOT)

    def _request_body(self):
        raw = str(self.headers.get("Content-Length", "") or "").strip()
        if not raw:
            return b""
        try:
            length = int(raw)
        except ValueError:
            raise ValueError("INVALID_CONTENT_LENGTH")
        if length < 0 or length > MAX_PROXY_REQUEST_BYTES:
            raise ValueError("REQUEST_BODY_TOO_LARGE")
        return self.rfile.read(length) if length else b""

    def _proxy_headers(self):
        headers = {}
        for name, value in self.headers.items():
            if name.lower() in _HOP_BY_HOP:
                continue
            headers[name] = value
        forwarded_for = str(self.headers.get("X-Forwarded-For", "") or "").strip()
        client_ip = str(self.client_address[0] if self.client_address else "")
        if client_ip:
            headers["X-Forwarded-For"] = (
                forwarded_for + ", " + client_ip if forwarded_for else client_ip
            )
        if not str(headers.get("X-Forwarded-Proto", "") or "").strip():
            headers["X-Forwarded-Proto"] = "https"
        headers["Connection"] = "close"
        return headers

    def _child_port(self):
        return int(_child_state().get("child_port") or 0)

    def _proxy(self, *, force_status=None):
        port = self._child_port()
        if not port:
            raise ConnectionError("CHILD_PORT_UNAVAILABLE")
        body = self._request_body()
        conn = http.client.HTTPConnection(
            "127.0.0.1",
            port,
            timeout=PROXY_CONNECT_TIMEOUT_SECONDS,
        )
        try:
            conn.connect()
            if conn.sock is not None:
                conn.sock.settimeout(PROXY_RESPONSE_TIMEOUT_SECONDS)
            conn.request(
                self.command,
                self.path,
                body=body if body else None,
                headers=self._proxy_headers(),
            )
            response = conn.getresponse()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            raise

        status = int(force_status) if force_status is not None else int(response.status)
        self.send_response(
            status,
            "OK" if force_status is not None else response.reason,
        )
        for name, value in response.getheaders():
            lower = name.lower()
            if lower in _HOP_BY_HOP or lower in {
                "server",
                "date",
                "connection",
            }:
                continue
            self.send_header(name, value)
        self.send_header("X-G2B-Port-Guard", "1")
        if force_status is not None:
            self.send_header(
                "X-G2B-Child-Status",
                str(int(response.status)),
            )
        self.send_header("Connection", "close")
        self.end_headers()

        if self.command != "HEAD":
            try:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
        conn.close()
        return True

    def _recovery(self):
        if self.command in {"GET", "HEAD"}:
            self._send_html(200)
        else:
            self._send_json(
                503,
                {
                    **_recovery_snapshot(),
                    "status": "not_ready",
                    "error": "G2B_CHILD_RUNTIME_STARTING",
                },
            )

    def _route(self):
        path = self.path.split("?", 1)[0]

        if path == "/ready":
            # Cafe24 may use /ready as a process-health probe. Keep the external
            # upstream alive with HTTP 200 while preserving strict operational
            # readiness in the JSON body and X-G2B-Child-Status.
            try:
                self._proxy(force_status=200)
                return
            except Exception:
                self._send_json(
                    200,
                    {
                        **_recovery_snapshot(),
                        "status": "recovery_ready",
                    },
                )
                return

        if path in {"/live", "/health", "/__ai_space_health"}:
            try:
                self._proxy()
                return
            except Exception:
                self._send_json(200, _recovery_snapshot())
                return

        try:
            self._proxy()
            return
        except Exception:
            self._recovery()

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
            "G2B_PORT_GUARD_HTTP %s - %s\n"
            % (self.address_string(), fmt % args)
        )
        sys.stdout.flush()


class PortGuardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128


def _bind_server(port):
    last = None
    for _ in range(40):
        try:
            return PortGuardHTTPServer(("0.0.0.0", int(port)), PortGuardHandler)
        except OSError as exc:
            last = exc
            time.sleep(0.25)
    raise last or RuntimeError("G2B_EXTERNAL_PORT_BIND_FAILED")


def _signal_exit(_signum, _frame):
    raise KeyboardInterrupt()


def main():
    external_port = resolve_port()
    internal_port = _pick_internal_port(external_port)
    _apply_process_tuning_env()

    # Critical order: bind the Cafe24-facing PORT before starting/importing the
    # full runtime child. This parent file imports only the Python stdlib.
    server = _bind_server(external_port)
    print(
        f"G2B_PORT_GUARD_LISTENING 0.0.0.0:{external_port} "
        f"child=127.0.0.1:{internal_port} v{VERSION}",
        flush=True,
    )

    signal.signal(signal.SIGTERM, _signal_exit)
    signal.signal(signal.SIGINT, _signal_exit)
    supervisor = threading.Thread(
        target=_supervisor_loop,
        args=(internal_port,),
        name="g2b-child-supervisor",
        daemon=True,
    )
    supervisor.start()

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        _STOP_EVENT.set()
        _stop_child()
        server.server_close()
        supervisor.join(timeout=2)


if __name__ == "__main__":
    main()
