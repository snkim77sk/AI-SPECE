"""Cafe24 AI SPACE entrypoint for LIGHTING SKETCH G2B DATA VIEW.

FastAPI fronts the existing stdlib dashboard server. Manual collection requests
are intercepted here so the browser returns immediately while the existing
collector continues in a background thread.
"""
import datetime as dt
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

APP_VERSION = "2.3.5-background-sync"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = int(os.getenv("G2B_INTERNAL_PORT", "8503"))
_backend_thread = None
_backend_error = ""
TEST_MODE = str(os.getenv("G2B_TEST_MODE", "0")).lower() in ("1", "true", "yes", "on")

_SYNC_PATHS = {
    "sync-shop": ("쇼핑몰 조달내역", "shop"),
    "sync-bids": ("물품 입찰공고", "bid"),
    "sync-services": ("용역공고", "bid"),
}
_sync_lock = threading.Lock()
_sync_thread = None
_sync_state = {
    "status": "대기", "job": "", "path": "", "start": "", "end": "",
    "started_at": "", "finished_at": "", "message": "수집 대기",
    "baseline_rows": 0, "baseline_calls": 0,
}

app = FastAPI(title="LIGHTING SKETCH G2B DATA VIEW", version=APP_VERSION)


def _backend_listening() -> bool:
    try:
        with socket.create_connection((BACKEND_HOST, BACKEND_PORT), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_for_backend(timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _backend_error:
            return False
        if _backend_listening():
            return True
        time.sleep(0.1)
    return _backend_listening()


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
    os.environ["HOST"] = BACKEND_HOST
    os.environ["PORT"] = str(BACKEND_PORT)
    os.environ["G2B_PUBLIC_MODE"] = "0" if TEST_MODE else "1"
    os.environ["G2B_OPEN_BROWSER"] = "0"
    os.environ["G2B_SEED_SAMPLE"] = "0"
    os.environ.setdefault("G2B_COOKIE_SECURE", "1")
    try:
        if str(os.getenv("G2B_PURGE_SAMPLE_DATA", "1")).lower() in ("1", "true", "yes", "on"):
            from db import init_db
            from seed import clear_samples
            init_db()
            clear_samples()
        import server
        server.main(open_browser=False)
    except Exception as exc:
        _backend_error = f"내부 대시보드 시작 실패: {exc}"


@app.on_event("startup")
def startup_event() -> None:
    global _backend_thread
    ok, _ = _configured()
    if ok and (_backend_thread is None or not _backend_thread.is_alive()):
        _backend_thread = threading.Thread(target=_start_backend, name="g2b-core", daemon=True)
        _backend_thread.start()
        _wait_for_backend(6.0)


def _setup_page(message: str) -> str:
    safe = (message or "환경변수 설정이 필요합니다.").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>LIGHTING SKETCH G2B 설정 필요</title><style>body{{font-family:Arial,'Noto Sans KR',sans-serif;background:#f5f7fb;margin:0;color:#182033}}main{{max-width:760px;margin:9vh auto;background:#fff;border:1px solid #e1e6ef;border-radius:16px;padding:32px;box-shadow:0 10px 30px #0001}}</style></head><body><main>
<h2>LIGHTING SKETCH G2B DATA VIEW</h2><p><b>카페24 AI SPACE 배포는 되었지만 보안 환경설정이 아직 완료되지 않았습니다.</b></p><p>{safe}</p><p>버전: {APP_VERSION}</p>
</main></body></html>"""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _persist_sync_state() -> None:
    try:
        from db import set_setting
        for key in ("status", "job", "path", "start", "end", "started_at", "finished_at", "message"):
            set_setting(f"bg_sync_{key}", str(_sync_state.get(key, "")))
    except Exception:
        pass


def _row_count(path: str) -> int:
    try:
        from db import connect
        with connect() as conn:
            if path == "sync-shop":
                row = conn.execute("SELECT COUNT(*) FROM shopping_contracts WHERE is_sample=0").fetchone()
            elif path == "sync-services":
                row = conn.execute("SELECT COUNT(*) FROM bids WHERE is_sample=0 AND business_type='용역'").fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM bids WHERE is_sample=0 AND business_type!='용역'").fetchone()
            return int(row[0] if row else 0)
    except Exception:
        return 0


def _api_calls(kind: str) -> tuple[int, int]:
    try:
        from g2b_sync import api_usage
        return api_usage(kind)
    except Exception:
        return 0, 0


def _sync_snapshot() -> dict:
    state = dict(_sync_state)
    path = state.get("path", "")
    kind = _SYNC_PATHS.get(path, ("", "shop"))[1]
    current_rows = _row_count(path) if path else 0
    current_calls, call_limit = _api_calls(kind)
    state["saved_rows"] = max(0, current_rows - int(state.get("baseline_rows", 0) or 0))
    state["api_calls"] = max(0, current_calls - int(state.get("baseline_calls", 0) or 0))
    state["api_calls_today"] = current_calls
    state["api_call_limit"] = call_limit
    state["running"] = bool(_sync_thread and _sync_thread.is_alive())
    return state


def _finish_message(path: str, fallback: str) -> str:
    try:
        from db import get_setting
        key = {
            "sync-shop": "last_sync_result",
            "sync-bids": "last_bid_sync_result",
            "sync-services": "last_service_sync_result",
        }.get(path, "")
        return get_setting(key, "") or fallback
    except Exception:
        return fallback


def _background_collect(path: str, body: bytes, headers: dict) -> None:
    label, _ = _SYNC_PATHS[path]
    target = f"http://{BACKEND_HOST}:{BACKEND_PORT}/{path}"
    try:
        req = urllib.request.Request(target, data=body, headers=headers, method="POST")
        try:
            upstream = _opener.open(req, timeout=1800)
            upstream.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location", "")
                params = parse_qs(urlsplit(location).query)
                if (params.get("error") or ["0"])[0] == "1":
                    error_message = (params.get("msg") or [f"{label} 수집 실패"])[0]
                    raise RuntimeError(error_message)
            else:
                detail = exc.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(f"HTTP {exc.code}: {detail}")
        _sync_state["status"] = "완료"
        _sync_state["message"] = _finish_message(path, f"{label} 수집 완료")
    except Exception as exc:
        _sync_state["status"] = "오류"
        _sync_state["message"] = str(exc)
    finally:
        _sync_state["finished_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _persist_sync_state()


def _start_background_collect(path: str, body: bytes, request_headers) -> tuple[bool, str]:
    global _sync_thread
    with _sync_lock:
        if _sync_thread is not None and _sync_thread.is_alive():
            return False, f"이미 {_sync_state.get('job') or '데이터'} 수집이 진행 중입니다."
        form = parse_qs(body.decode("utf-8", "replace"))
        start = (form.get("start") or [""])[0]
        end = (form.get("end") or [""])[0]
        label, kind = _SYNC_PATHS[path]
        current_calls, _ = _api_calls(kind)
        _sync_state.update({
            "status": "수집중", "job": label, "path": path,
            "start": start, "end": end,
            "started_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "", "message": "API 수집을 백그라운드에서 실행 중입니다.",
            "baseline_rows": _row_count(path), "baseline_calls": current_calls,
        })
        _persist_sync_state()
        headers = {"Content-Type": request_headers.get("content-type", "application/x-www-form-urlencoded")}
        cookie = request_headers.get("cookie")
        if cookie:
            headers["Cookie"] = cookie
        forwarded = request_headers.get("x-forwarded-for")
        if forwarded:
            headers["X-Forwarded-For"] = forwarded
        _sync_thread = threading.Thread(
            target=_background_collect, args=(path, body, headers),
            name=f"g2b-{path}", daemon=True,
        )
        _sync_thread.start()
        return True, f"{label} 수집을 시작했습니다. 화면을 계속 사용할 수 있습니다."


def _sync_panel_html() -> str:
    return r"""
<section id="bg-sync-panel" style="margin:14px auto;max-width:1180px;border:1px solid #cbd5e1;border-radius:12px;background:#fff;padding:16px 18px;box-sizing:border-box">
  <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap">
    <strong style="font-size:16px">실데이터 수집 상태</strong>
    <span id="bg-sync-badge" style="font-weight:700">대기</span>
  </div>
  <div id="bg-sync-detail" style="margin-top:8px;line-height:1.7;color:#334155">상태 확인 중...</div>
</section>
<script>
(function(){
  function esc(v){return String(v==null?'':v).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function setDisabled(running){
    document.querySelectorAll('form[action="/sync-shop"],form[action="/sync-bids"],form[action="/sync-services"]').forEach(function(f){
      f.querySelectorAll('button,input[type="submit"]').forEach(function(b){
        b.disabled=!!running; b.style.opacity=running?'.55':''; b.style.cursor=running?'not-allowed':'';
      });
    });
  }
  async function poll(){
    try{
      var r=await fetch('/__sync_status',{cache:'no-store'});
      var s=await r.json();
      document.getElementById('bg-sync-badge').textContent=s.status||'대기';
      var period=(s.start||s.end)?esc(s.start||'-')+' ~ '+esc(s.end||'-'):'-';
      var calls=(s.api_calls||0)+'회 (오늘 '+(s.api_calls_today||0)+' / '+(s.api_call_limit||0)+')';
      document.getElementById('bg-sync-detail').innerHTML=
        '<b>작업:</b> '+esc(s.job||'-')+
        ' &nbsp; <b>기간:</b> '+period+
        ' &nbsp; <b>저장건수:</b> '+Number(s.saved_rows||0).toLocaleString()+'건'+
        ' &nbsp; <b>API 호출:</b> '+esc(calls)+
        '<br><b>시작:</b> '+esc(s.started_at||'-')+
        ' &nbsp; <b>종료:</b> '+esc(s.finished_at||'-')+
        '<br><b>메시지:</b> '+esc(s.message||'-');
      setDisabled(!!s.running);
    }catch(e){
      document.getElementById('bg-sync-badge').textContent='상태확인 오류';
      document.getElementById('bg-sync-detail').textContent=String(e);
    }
  }
  poll(); setInterval(poll,2000);
})();
</script>
"""


def _inject_sync_panel(payload: bytes) -> bytes:
    try:
        text = payload.decode("utf-8")
        if 'id="bg-sync-panel"' in text:
            return payload
        marker = "<main>"
        if marker in text:
            text = text.replace(marker, marker + _sync_panel_html(), 1)
        elif "</body>" in text:
            text = text.replace("</body>", _sync_panel_html() + "</body>", 1)
        return text.encode("utf-8")
    except Exception:
        return payload


async def _proxy(request: Request, path: str) -> Response:
    ok, msg = _configured()
    if not ok:
        return HTMLResponse(_setup_page(msg), status_code=200)
    if _backend_error:
        return HTMLResponse(_setup_page(_backend_error), status_code=200 if path == "" else 503)

    if request.method == "POST" and path in _SYNC_PATHS:
        body = await request.body()
        _, message = _start_background_collect(path, body, request.headers)
        return Response(
            status_code=303,
            headers={"Location": "/settings?msg=" + quote(message), "Cache-Control": "no-store"},
        )

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
    client_ip = request.client.host if request.client else ""
    if client_ip:
        headers["X-Forwarded-For"] = client_ip
    headers["X-Forwarded-Proto"] = request.url.scheme
    req = urllib.request.Request(
        target, data=body if request.method in {"POST", "PUT", "PATCH"} else None,
        headers=headers, method=request.method,
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
        message = f"내부 서버 연결 실패: {exc}"
        return HTMLResponse(_setup_page(message), status_code=200 if path == "" else 502)

    content_type = upstream_headers.get("Content-Type", "")
    if request.method == "GET" and path == "settings" and "text/html" in content_type:
        payload = _inject_sync_panel(payload)

    out_headers = {}
    for key in ("Content-Type", "Content-Disposition", "Location", "Set-Cookie", "Cache-Control", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Content-Security-Policy"):
        value = upstream_headers.get(key)
        if value:
            out_headers[key] = value
    return Response(content=payload, status_code=status, headers=out_headers)


@app.get("/__sync_status")
def sync_status():
    return _sync_snapshot()


@app.get("/__ai_space_health")
def platform_health():
    ok, msg = _configured()
    return {
        "platform_ok": True, "configured": ok,
        "backend_ok": _backend_listening() and not bool(_backend_error),
        "version": APP_VERSION, "test_mode": TEST_MODE,
        "db_path": os.getenv("G2B_DB_PATH", ""), "sample_generation": False,
        "sync": _sync_snapshot(), "message": _backend_error or msg or "ready",
    }


@app.get("/health")
def health():
    ok, msg = _configured()
    return {
        "status": "ok", "configured": ok,
        "backend_ok": _backend_listening() and not bool(_backend_error),
        "backend_error": _backend_error, "db_path": os.getenv("G2B_DB_PATH", ""),
        "version": APP_VERSION, "test_mode": TEST_MODE, "sample_generation": False,
        "sync": _sync_snapshot(), "message": _backend_error or msg or "ready",
    }


@app.api_route("/", methods=["GET", "POST"])
async def proxy_root(request: Request):
    return await _proxy(request, "")


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def proxy_all(request: Request, path: str):
    return await _proxy(request, path)
