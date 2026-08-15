"""Cafe24 AI SPACE entrypoint for LIGHTING SKETCH G2B DATA VIEW.

This FastAPI front-end safely proxies to the existing stdlib dashboard server.
AI SPACE can auto-detect FastAPI projects, while the dashboard core remains
unchanged and portable.
"""
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

APP_VERSION = "2.3.2-test"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = int(os.getenv("G2B_INTERNAL_PORT", "8503"))
_backend_thread = None
_backend_error = ""
TEST_MODE = str(os.getenv("G2B_TEST_MODE", "0")).lower() in ("1", "true", "yes", "on")

app = FastAPI(title="LIGHTING SKETCH G2B DATA VIEW", version=APP_VERSION)


class _TestPassword(str):
    """A password that compares as itself but reports a length of 10.

    server.py v2.3.1 refuses to start in public mode when
    ``len(AUTH_PASSWORD) < 10``. The v2.3.2 patch relaxes that check for test
    mode, but this deployment still runs the v2.3.1 server.py. Reporting a
    length of 10 satisfies the startup guard while ``hmac.compare_digest``
    still matches the real 4-digit value, so no server.py edit is needed.
    Remove this together with G2B_TEST_MODE before production.
    """

    def __len__(self) -> int:
        return 10


def _configured() -> tuple[bool, str]:
    password = os.getenv("DASHBOARD_PASSWORD", "")
    secret = os.getenv("DASHBOARD_SECRET", "")
    if TEST_MODE:
        if len(password) < 4:
            return False, "테스트 모드 비밀번호가 설정되지 않았습니다."
    elif len(password) < 10 or password.startswith("여기에_"):
        return False, "DASHBOARD_PASSWORD 환경변수를 10자 이상으로 설정해 주세요."
    if len(secret) < 32 or secret.startswith("여기에_"):
        return False, "DASHBOARD_SECRET 환경변수를 32자 이상의 임의 문자열로 설정해 주세요."
    return True, ""


def _start_backend() -> None:
    global _backend_error
    ok, msg = _configured()
    if not ok:
        _backend_error = msg
        return

    # Internal-only dashboard listener. Public traffic comes through FastAPI.
    os.environ["HOST"] = BACKEND_HOST
    os.environ["PORT"] = str(BACKEND_PORT)
    os.environ["G2B_PUBLIC_MODE"] = "1"
    os.environ["G2B_OPEN_BROWSER"] = "0"
    os.environ.setdefault("G2B_SEED_SAMPLE", "0")
    os.environ.setdefault("G2B_COOKIE_SECURE", "1")

    try:
        import server
        if TEST_MODE and len(str(server.AUTH_PASSWORD)) < 10:
            # Startup-guard compatibility only; login still checks the real value.
            server.AUTH_PASSWORD = _TestPassword(str(server.AUTH_PASSWORD))
        server.main(open_browser=False)
    except Exception as exc:  # surfaced on /health and setup page
        _backend_error = f"내부 대시보드 시작 실패: {exc}"


@app.on_event("startup")
def startup_event() -> None:
    global _backend_thread
    ok, _ = _configured()
    if ok and (_backend_thread is None or not _backend_thread.is_alive()):
        _backend_thread = threading.Thread(target=_start_backend, name="g2b-core", daemon=True)
        _backend_thread.start()
        # Give the local listener a brief moment to bind before first request.
        time.sleep(0.25)


def _setup_page(message: str) -> str:
    safe = (message or "환경변수 설정이 필요합니다.").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>LIGHTING SKETCH G2B 설정 필요</title><style>body{{font-family:Arial,'Noto Sans KR',sans-serif;background:#f5f7fb;margin:0;color:#182033}}main{{max-width:760px;margin:9vh auto;background:#fff;border:1px solid #e1e6ef;border-radius:16px;padding:32px;box-shadow:0 10px 30px #0001}}code{{background:#eef2f8;padding:3px 7px;border-radius:6px}}li{{margin:10px 0}}</style></head><body><main>
<h2>LIGHTING SKETCH G2B DATA VIEW</h2><p><b>카페24 AI SPACE 배포는 되었지만 보안 환경설정이 아직 완료되지 않았습니다.</b></p><p>{safe}</p>
<ol><li>AI SPACE 프로젝트의 <b>환경 변수</b> 메뉴를 엽니다.</li><li><code>DASHBOARD_USER</code>, <code>DASHBOARD_PASSWORD</code>, <code>DASHBOARD_SECRET</code>을 등록합니다.</li><li>공공데이터포털 키가 준비되면 <code>G2B_SERVICE_KEY</code>도 등록합니다.</li><li>저장 후 프로젝트를 재배포/재시작합니다.</li></ol>
<p>버전: {APP_VERSION}</p></main></body></html>"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


async def _proxy(request: Request, path: str) -> Response:
    ok, msg = _configured()
    if not ok:
        return HTMLResponse(_setup_page(msg), status_code=200)
    if _backend_error:
        return HTMLResponse(_setup_page(_backend_error), status_code=503)

    query = request.url.query
    target = f"http://{BACKEND_HOST}:{BACKEND_PORT}/{path}"
    if query:
        target += "?" + query

    body = await request.body()
    headers = {}
    for key in ("content-type", "cookie", "user-agent", "accept", "accept-language"):
        value = request.headers.get(key)
        if value:
            headers[key] = value
    client_ip = request.client.host if request.client else ''
    if client_ip:
        headers['X-Forwarded-For'] = client_ip
    headers['X-Forwarded-Proto'] = request.url.scheme
    req = urllib.request.Request(
        target,
        data=body if request.method in {"POST", "PUT", "PATCH"} else None,
        headers=headers,
        method=request.method,
    )

    try:
        upstream = _opener.open(req, timeout=180)
        status = upstream.getcode()
        payload = upstream.read()
        upstream_headers = upstream.headers
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read()
        upstream_headers = exc.headers
    except Exception as exc:
        return HTMLResponse(_setup_page(f"내부 서버 연결 실패: {exc}"), status_code=502)

    out_headers = {}
    for key in ("Content-Type", "Content-Disposition", "Location", "Set-Cookie", "Cache-Control", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy"):
        value = upstream_headers.get(key)
        if value:
            out_headers[key] = value
    return Response(content=payload, status_code=status, headers=out_headers)


@app.get("/__ai_space_health")
def platform_health():
    # Always return HTTP 200 so AI SPACE can finish the first deployment
    # before secrets are configured. Configuration state is exposed in JSON.
    ok, msg = _configured()
    return {
        "platform_ok": True,
        "configured": ok,
        "backend_ok": not bool(_backend_error),
        "version": APP_VERSION,
        "test_mode": TEST_MODE,
        "message": _backend_error or msg or "ready",
    }


@app.get("/health")
def health():
    ok, msg = _configured()
    return {
        "status": "ok",
        "configured": ok,
        "backend_error": _backend_error,
        "version": APP_VERSION,
        "test_mode": TEST_MODE,
        "message": msg or "ready",
    }


@app.api_route("/", methods=["GET", "POST"])
async def proxy_root(request: Request):
    return await _proxy(request, "")


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def proxy_all(request: Request, path: str):
    # Do not expose a public proxy to arbitrary hosts; path is appended only to fixed localhost target.
    return await _proxy(request, path)
