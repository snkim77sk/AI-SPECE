"""Production web runtime for SINSUNG G2B vNext.

The application serves only vNext RAW/projection data. Legacy 2.x serving tables
and collectors are not imported. External source traffic remains safety-gated by
vNext source contexts and is never triggered by read-only pages.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app_version import APP_VERSION
from db import connect, current_db_path, db_is_persistent, get_service_key
from vnext_clean_db import (
    authenticate,
    create_admin,
    create_session,
    delete_session,
    ensure_clean_schema,
    session_user,
    setup_token,
    users_empty,
    validate_setup_token,
)

SESSION_COOKIE = "g2b_vnext_session"
TEST_MODE = str(os.getenv("G2B_TEST_MODE", "0")).lower() in ("1", "true", "yes", "on")
TARGET_CATEGORIES = ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")
CATEGORY_LABELS = {
    "LIGHTING": "조명",
    "POLE": "가로등주",
    "ELECTRICAL": "전기",
    "SOLAR": "태양광",
}
LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_FAILURES = 8

_BACKEND_LOCK = threading.Lock()
_BACKEND_STATE = {
    "initialized": False,
    "initializing": False,
    "backend_ok": False,
    "backend_error": "",
    "attempts": 0,
}
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES = {}


def esc(value):
    return html.escape(str(value or ""))


def money(value):
    try:
        return f"{int(value or 0):,}원"
    except Exception:
        return "0원"


def _secure(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers[
        "Content-Security-Policy"
    ] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
    if not TEST_MODE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def initialize_backend(*, force=False):
    """Initialize storage synchronously.

    Production startup never calls this on the event-loop thread; it is retained as
    a deterministic helper for background work and regression tests.
    """
    with _BACKEND_LOCK:
        if _BACKEND_STATE["backend_ok"] and not force:
            return True
        if _BACKEND_STATE["initializing"] and not force:
            return False
        _BACKEND_STATE["initializing"] = True
        _BACKEND_STATE["attempts"] += 1

    try:
        ensure_clean_schema()
        # Keep heavier projection imports out of ASGI module import/startup.
        import budget_projection_vnext
        budget_projection_vnext.ensure_schema()
    except Exception as exc:
        with _BACKEND_LOCK:
            _BACKEND_STATE.update(
                initialized=True,
                initializing=False,
                backend_ok=False,
                backend_error=f"{type(exc).__name__}: {str(exc)[:400]}",
            )
        print("G2B_VNEXT_BOOT_DEGRADED", type(exc).__name__, flush=True)
        return False

    with _BACKEND_LOCK:
        _BACKEND_STATE.update(
            initialized=True,
            initializing=False,
            backend_ok=True,
            backend_error="",
        )
    try:
        if users_empty():
            token = setup_token()
            if token:
                print(f"G2B_VNEXT_SETUP_TOKEN={token}", flush=True)
    except Exception as exc:
        # Authentication setup diagnostics must never revoke an otherwise usable DB.
        print("G2B_VNEXT_SETUP_DIAGNOSTIC_ERROR", type(exc).__name__, flush=True)
    print("G2B_VNEXT_BOOT_OK", APP_VERSION, flush=True)
    return True


def _backend_worker():
    initialize_backend(force=True)


def schedule_backend_init(*, force=False):
    """Start DB/schema initialization in a daemon thread and return immediately."""
    with _BACKEND_LOCK:
        if _BACKEND_STATE["backend_ok"] and not force:
            return False
        if _BACKEND_STATE["initializing"]:
            return False
        _BACKEND_STATE["initializing"] = True
    # initialize_backend owns the attempt lifecycle. Clear our reservation first so
    # the worker can enter it; no request thread waits for the database.
    with _BACKEND_LOCK:
        _BACKEND_STATE["initializing"] = False
    thread = threading.Thread(
        target=_backend_worker,
        name="g2b-vnext-backend-init",
        daemon=True,
    )
    thread.start()
    return True


def backend_status():
    with _BACKEND_LOCK:
        return dict(_BACKEND_STATE)


@asynccontextmanager
async def lifespan(_app):
    # Critical deployment invariant: HTTP startup does not wait for SQLite.
    schedule_backend_init()
    yield


app = FastAPI(title="SINSUNG G2B vNext", version=APP_VERSION, lifespan=lifespan)


STYLE = """
:root{font-family:Inter,Pretendard,Arial,sans-serif;color:#172033;background:#f4f6f9}
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#172033}
a{color:inherit;text-decoration:none}.top{background:#111b31;color:white;padding:18px 22px}
.brand{font-size:22px;font-weight:900}.sub{opacity:.75;margin-top:5px;font-size:13px}
.nav{display:flex;gap:8px;overflow:auto;padding:12px 16px;background:white;border-bottom:1px solid #dde2ea}
.nav a{white-space:nowrap;padding:10px 16px;border-radius:999px;background:#eef1f5;font-weight:800}
.nav a.on{background:#14213d;color:white}.wrap{max-width:1440px;margin:auto;padding:18px}
.card{background:white;border:1px solid #dde2ea;border-radius:18px;padding:20px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.kpi{background:white;border:1px solid #dde2ea;border-radius:16px;padding:18px}
.kpi b{font-size:28px;display:block;margin-bottom:8px}.muted{color:#697386}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:11px;border-bottom:1px solid #e6e9ee;text-align:left;vertical-align:top}
th{background:#f7f8fa}.table{overflow:auto}.btn,button{display:inline-block;border:1px solid #26334d;border-radius:9px;padding:10px 14px;background:white;font-weight:800;cursor:pointer}
button.primary,.primary{background:#14213d;color:white}.notice{background:#fff5cc;border:1px solid #e6d481;border-radius:12px;padding:14px;margin:12px 0;line-height:1.55}
.ok{background:#eaf8ef;border:1px solid #9bd4ac}.bad{background:#fff0f0;border:1px solid #e6aaaa}
form.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}label{font-weight:700}input,select{display:block;margin-top:6px;padding:10px;border:1px solid #c7ccd4;border-radius:8px;min-width:150px}
.auth{max-width:440px;margin:8vh auto;background:white;border:1px solid #dde2ea;border-radius:18px;padding:28px}.auth input{width:100%}.auth button{width:100%;margin-top:14px}
.actions{display:flex;gap:8px;flex-wrap:wrap}.right{float:right}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#eef1f5;font-size:12px;font-weight:800}
.num{text-align:right;white-space:nowrap}.nowrap{white-space:nowrap}.wide{min-width:260px}
@media(max-width:640px){.wrap{padding:10px}.card{padding:14px}.top{padding:14px}.brand{font-size:19px}th,td{padding:9px;font-size:12px}}
"""


@app.middleware("http")
async def backend_gate(request: Request, call_next):
    # Platform liveness/root probes must never wait on persistent storage.
    if request.url.path not in {"/", "/health", "/__ai_space_health", "/live", "/ready"}:
        state = backend_status()
        if not state["backend_ok"]:
            schedule_backend_init()
            state = backend_status()
            return _secure(
                HTMLResponse(
                    "<h2>G2B vNext 저장소 초기화 대기</h2>"
                    "<p>웹 프로세스는 정상 기동했습니다. 데이터 저장소 연결을 백그라운드에서 준비 중입니다.</p>"
                    f"<pre>{esc(state.get('backend_error'))}</pre>",
                    status_code=503,
                )
            )
    return _secure(await call_next(request))


def _session_token(request: Request):
    return str(request.cookies.get(SESSION_COOKIE, "") or "")


def current_user(request: Request):
    return session_user(_session_token(request))


def require_user(request: Request):
    return current_user(request)


def csrf_token(request: Request, path: str):
    token = _session_token(request)
    if not token:
        return ""
    return hashlib.sha256(f"{token}|{path}|g2b-vnext-csrf-v1".encode("utf-8")).hexdigest()


def csrf_input(request: Request, path: str):
    return f'<input type="hidden" name="_csrf" value="{esc(csrf_token(request, path))}">'


def valid_csrf(request: Request, path: str, supplied):
    expected = csrf_token(request, path)
    value = str(supplied or "")
    return bool(expected and value and secrets.compare_digest(expected, value))


def _client_ip(request: Request):
    return str(request.client.host if request.client else "unknown")


def _login_allowed(ip):
    now = time.time()
    with _LOGIN_LOCK:
        recent = [stamp for stamp in _LOGIN_FAILURES.get(ip, []) if now - stamp < LOGIN_WINDOW_SECONDS]
        _LOGIN_FAILURES[ip] = recent
        return len(recent) < LOGIN_MAX_FAILURES


def _login_failed(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.setdefault(ip, []).append(time.time())


def _login_success(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(ip, None)


def layout(title, body, active="", user=None):
    navs = [
        ("대시보드", "/dashboard"),
        ("쇼핑몰 납품요구", "/shopping"),
        ("물품 입찰공고", "/goods"),
        ("용역 라이프사이클", "/service"),
        ("업체 분석", "/vendors"),
        ("예산·영업후보", "/budget"),
        ("RAW 저장소", "/raw"),
        ("설정", "/settings"),
    ]
    nav = "".join(
        f'<a class="{"on" if name==active else ""}" href="{href}">{name}</a>'
        for name, href in navs
    )
    user_html = ""
    if user:
        user_html = (
            f'<span class="right">{esc(user["username"])} · '
            '<a href="/logout">로그아웃</a></span>'
        )
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · SINSUNG G2B vNext</title><style>{STYLE}</style></head><body>
<header class="top"><div class="brand">SINSUNG · 신성라이텍 G2B vNext {user_html}</div>
<div class="sub">전체수집 → RAW 보존 → 정규화 → 후분류 → 영업·조달 분석</div></header>
<nav class="nav">{nav}</nav><main class="wrap">{body}</main></body></html>"""
    )


async def form_data(request: Request):
    raw = (await request.body()).decode("utf-8", "replace")
    return {k: (v[0] if v else "") for k, v in parse_qs(raw).items()}


def _query_options(request: Request):
    q = str(request.query_params.get("q", "") or "").strip()
    category = str(request.query_params.get("category", "") or "").upper().strip()
    categories = (category,) if category in TARGET_CATEGORIES else TARGET_CATEGORIES
    try:
        limit = max(10, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200
    opts = ['<option value="">전체 대상</option>']
    for code in TARGET_CATEGORIES:
        selected = " selected" if category == code else ""
        opts.append(f'<option value="{code}"{selected}>{CATEGORY_LABELS[code]}</option>')
    return q, category, categories, limit, "".join(opts)


def raw_counts():
    if not _BACKEND_STATE["backend_ok"]:
        return []
    with connect() as conn:
        rows = conn.execute(
            "SELECT dataset,COUNT(*) n,MAX(fetched_at) last_at FROM raw_records GROUP BY dataset ORDER BY dataset"
        ).fetchall()
    return [dict(row) for row in rows]


def raw_total():
    return sum(int(row["n"] or 0) for row in raw_counts())


def target_dataset_counts():
    if not _BACKEND_STATE["backend_ok"]:
        return {}
    from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = conn.execute(
            """SELECT r.dataset,COUNT(*) n
               FROM raw_records r
               JOIN classifications c
                 ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                AND c.classifier_version=?
                AND c.source_payload_sha256=r.payload_sha256
               WHERE c.primary_category IN ('LIGHTING','POLE','ELECTRICAL','SOLAR')
               GROUP BY r.dataset""",
            (CLASSIFIER_VERSION,),
        ).fetchall()
    return {str(row["dataset"]): int(row["n"] or 0) for row in rows}


@app.get("/live")
def live():
    return {
        "status": "ok",
        "process_alive": True,
        "runtime": "G2B_VNEXT_CLEAN",
        "version": APP_VERSION,
    }


@app.get("/ready")
def ready():
    state = backend_status()
    if not state["backend_ok"]:
        schedule_backend_init()
        state = backend_status()
    payload = {
        "status": "ready" if state["backend_ok"] else "not_ready",
        "backend_ok": state["backend_ok"],
        "backend_initializing": state["initializing"],
        "backend_error": state["backend_error"],
        "runtime": "G2B_VNEXT_CLEAN",
        "version": APP_VERSION,
    }
    return JSONResponse(payload, status_code=200 if state["backend_ok"] else 503)


@app.get("/health")
@app.get("/__ai_space_health")
def health():
    state = backend_status()
    if not state["backend_ok"]:
        schedule_backend_init()
        state = backend_status()
    return {
        "status": "ok",
        "process_alive": True,
        "backend_ok": state["backend_ok"],
        "backend_initializing": state["initializing"],
        "backend_error": state["backend_error"],
        "backend_init_attempts": state["attempts"],
        "runtime": "G2B_VNEXT_CLEAN",
        "version": APP_VERSION,
        "raw_rows": raw_total() if state["backend_ok"] else 0,
        "db_path": current_db_path(),
        "db_persistent": db_is_persistent(),
        "required_boot_env": [],
    }


@app.get("/")
def root(request: Request):
    state = backend_status()
    if not state["backend_ok"]:
        schedule_backend_init()
        return HTMLResponse(
            "<!doctype html><html lang='ko'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SINSUNG G2B vNext</title>"
            "<body style='font-family:sans-serif;padding:32px'>"
            "<h2>SINSUNG G2B vNext</h2>"
            "<p>웹 서버가 기동되었습니다. 데이터 저장소를 준비 중입니다.</p>"
            "<p><a href='/health'>상태 확인</a></p></body></html>",
            status_code=200,
        )
    if users_empty():
        return RedirectResponse("/setup", 302)
    if not require_user(request):
        return RedirectResponse("/login", 302)
    return RedirectResponse("/dashboard", 302)


@app.get("/setup")
def setup_page(request: Request):
    if not users_empty():
        return RedirectResponse("/login", 302)
    error = request.query_params.get("error", "")
    flash = f'<div class="notice bad">{esc(error)}</div>' if error else ""
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>G2B vNext 관리자 설정</title><style>{STYLE}</style></head><body><section class="auth">
<h2>G2B vNext 최초 관리자</h2>
<div class="notice">Cafe24 배포 로그의 <b>G2B_VNEXT_SETUP_TOKEN=...</b> 값을 설정코드에 입력하세요. 환경변수 <b>G2B_SETUP_TOKEN</b>을 미리 등록해도 됩니다.</div>
{flash}<form method="post" action="/setup">
<label>설정코드<input name="setup_token" autocomplete="off" required></label>
<label>아이디<input name="username" minlength="4" required></label>
<label>비밀번호<input type="password" name="password" minlength="10" required></label>
<label>비밀번호 확인<input type="password" name="confirm" minlength="10" required></label>
<button class="primary">관리자 생성</button></form></section></body></html>"""
    )


@app.post("/setup")
async def setup_submit(request: Request):
    if not users_empty():
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if not validate_setup_token(data.get("setup_token")):
        return RedirectResponse("/setup?error=" + quote("설정코드가 올바르지 않습니다."), 302)
    if data.get("password") != data.get("confirm"):
        return RedirectResponse("/setup?error=" + quote("비밀번호 확인이 일치하지 않습니다."), 302)
    try:
        create_admin(data.get("username"), data.get("password"))
    except ValueError as exc:
        return RedirectResponse("/setup?error=" + quote(str(exc)), 302)
    return RedirectResponse("/login", 302)


@app.get("/login")
def login_page(request: Request):
    if users_empty():
        return RedirectResponse("/setup", 302)
    if require_user(request):
        return RedirectResponse("/dashboard", 302)
    error = request.query_params.get("error", "")
    flash = f'<div class="notice bad">{esc(error)}</div>' if error else ""
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>G2B vNext 로그인</title><style>{STYLE}</style></head><body><section class="auth">
<h2>SINSUNG G2B vNext</h2><p class="muted">공공조달 데이터 플랫폼</p>{flash}
<form method="post" action="/login"><label>아이디<input name="username" required></label>
<label>비밀번호<input type="password" name="password" required></label>
<button class="primary">로그인</button></form></section></body></html>"""
    )


@app.post("/login")
async def login_submit(request: Request):
    ip = _client_ip(request)
    if not _login_allowed(ip):
        return HTMLResponse("로그인 실패가 반복되어 잠시 제한됩니다.", status_code=429)
    data = await form_data(request)
    user = authenticate(data.get("username"), data.get("password"))
    if not user:
        _login_failed(ip)
        return RedirectResponse("/login?error=" + quote("아이디 또는 비밀번호를 확인해 주세요."), 302)
    _login_success(ip)
    token = create_session(user["username"])
    response = RedirectResponse("/dashboard", 302)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=not TEST_MODE,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
def logout(request: Request):
    delete_session(_session_token(request))
    response = RedirectResponse("/login", 302)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=not TEST_MODE,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/dashboard")
def dashboard(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import readiness_vnext
    counts = raw_counts()
    by_name = {row["dataset"]: int(row["n"] or 0) for row in counts}
    target = target_dataset_counts()
    total = sum(by_name.values())
    readiness = readiness_vnext.build_readiness_report()
    body = f"""
<section class="card"><h2>G2B vNext 대시보드</h2>
<div class="notice"><b>운영 원칙:</b> 전체 원천을 RAW로 먼저 보존하고 조명·가로등주·전기·태양광 분류는 수집 후 수행합니다. 저장 건수는 전체 원천 완전수집을 의미하지 않습니다.</div></section>
<div class="grid">
<div class="kpi"><b>{total:,}</b><span>전체 현재 RAW</span></div>
<div class="kpi"><b>{target.get('shopping_delivery',0):,}</b><span>대상 납품요구</span></div>
<div class="kpi"><b>{target.get('bid_notice_goods',0):,}</b><span>대상 물품공고</span></div>
<div class="kpi"><b>{target.get('bid_notice_service',0):,}</b><span>대상 용역공고</span></div>
<div class="kpi"><b>{target.get('budget',0)+target.get('education_budget',0):,}</b><span>대상 예산 RAW</span></div>
</div>
<section class="card"><h3>수집 준비상태</h3>
<p><span class="pill">{esc(readiness.get("status"))}</span> · {esc(readiness.get("status_scope"))}</p>
<p class="muted">실원천 수집은 bounded canary → small-validation 검증 후 범위를 확대합니다. bulk historical은 계속 HOLD입니다.</p></section>
"""
    return layout("대시보드", body, "대시보드", user)


@app.get("/shopping")
def shopping_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import procurement_read_vnext as read
    q, category, categories, limit, opts = _query_options(request)
    rows = read.shopping_rows(categories=categories, query=q, limit=limit)
    trs = "".join(
        f"<tr><td class='nowrap'>{esc(r['source_date'])}</td><td>{esc(r['demand_org'])}</td>"
        f"<td>{esc(r['detail_item_no'])}<br><span class='muted'>{esc(r['detail_item_name'])}</span></td>"
        f"<td>{esc(r['item_id'])}<br><span class='muted'>{esc(r['item_name'])} {esc(r['model_name'])}</span></td>"
        f"<td>{esc(r['vendor_name'])}</td><td class='num'>{money(r['amount'])}</td>"
        f"<td>{esc(CATEGORY_LABELS.get(r['primary_category'],r['primary_category']))}</td></tr>"
        for r in rows
    )
    body = f"""
<section class="card"><h2>쇼핑몰 납품요구</h2>
<p class="muted">나라장터 납품요구 RAW를 전량 보존한 뒤 대상 품목만 후분류하여 조회합니다.</p>
<form class="row" method="get"><label>검색<input name="q" value="{esc(q)}" placeholder="기관·제품·업체·식별번호"></label>
<label>분류<select name="category">{opts}</select></label><label>표시<input name="limit" type="number" min="10" max="1000" value="{limit}"></label>
<button class="primary">조회</button></form></section>
<section class="card"><div class="table"><table><tr><th>원천일자</th><th>수요기관</th><th>세부품명</th><th>제품</th><th>업체</th><th>금액</th><th>분류</th></tr>
{trs or '<tr><td colspan="7">현재 저장된 대상 납품요구 없음</td></tr>'}</table></div></section>
"""
    return layout("쇼핑몰 납품요구", body, "쇼핑몰 납품요구", user)


@app.get("/goods")
def goods_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import procurement_read_vnext as read
    q, category, categories, limit, opts = _query_options(request)
    rows = read.goods_notice_rows(categories=categories, query=q, limit=limit)
    trs = "".join(
        f"<tr><td class='nowrap'>{esc(r['source_date'])}</td><td>{esc(r['notice_no'])}-{esc(r['notice_order'])}</td>"
        f"<td class='wide'>{esc(r['notice_name'])}</td><td>{esc(r['demand_org'] or r['notice_org'])}</td>"
        f"<td class='num'>{money(r['budget_amount'])}</td><td class='num'>{money(r['estimated_price'])}</td>"
        f"<td>{esc(CATEGORY_LABELS.get(r['primary_category'],r['primary_category']))}</td></tr>"
        for r in rows
    )
    body = f"""
<section class="card"><h2>물품 입찰공고</h2>
<p class="muted">물품 기본공고 전체 RAW 중 후분류된 조명·가로등주·전기·태양광 공고를 조회합니다.</p>
<form class="row" method="get"><label>검색<input name="q" value="{esc(q)}" placeholder="공고번호·공고명·기관"></label>
<label>분류<select name="category">{opts}</select></label><label>표시<input name="limit" type="number" min="10" max="1000" value="{limit}"></label>
<button class="primary">조회</button></form></section>
<section class="card"><div class="table"><table><tr><th>원천일자</th><th>공고번호</th><th>공고명</th><th>기관</th><th>배정예산</th><th>추정가격</th><th>분류</th></tr>
{trs or '<tr><td colspan="7">현재 저장된 대상 물품공고 없음</td></tr>'}</table></div></section>
"""
    return layout("물품 입찰공고", body, "물품 입찰공고", user)


@app.get("/service")
def service_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import analysis_vnext
    q, category, categories, limit, opts = _query_options(request)
    rows = analysis_vnext.service_lifecycle_rows(categories=categories, limit=limit)
    if q:
        qf = q.casefold()
        rows = [
            r for r in rows
            if qf in " | ".join(
                str(r.get(k) or "") for k in (
                    "source_key","notice_name","notice_org","demand_org",
                    "first_rank_vendor","final_vendor","contract_vendor",
                )
            ).casefold()
        ]
    trs = "".join(
        f"<tr><td>{esc(r['notice_name'])}<br><span class='muted'>{esc(r['source_key'])}</span></td>"
        f"<td>{esc(r['demand_org'] or r['notice_org'])}</td><td>{esc(r['opening_date'])}</td>"
        f"<td class='num'>{esc(r['participant_count'])}</td><td>{esc(r['first_rank_vendor'])}</td>"
        f"<td>{esc(r['final_vendor'])}</td><td>{esc(r['contract_vendor'])}</td>"
        f"<td class='num'>{money(r['contract_amount'])}</td></tr>"
        for r in rows
    )
    body = f"""
<section class="card"><h2>용역 라이프사이클</h2>
<p class="muted">공고 → 개찰 1순위 → 최종낙찰 → 계약을 동일 실행 단위로 조회합니다.</p>
<form class="row" method="get"><label>검색<input name="q" value="{esc(q)}" placeholder="공고·기관·업체"></label>
<label>분류<select name="category">{opts}</select></label><label>표시<input name="limit" type="number" min="10" max="1000" value="{limit}"></label>
<button class="primary">조회</button></form></section>
<section class="card"><div class="table"><table><tr><th>공고</th><th>기관</th><th>개찰일</th><th>참가</th><th>1순위</th><th>최종낙찰</th><th>계약업체</th><th>계약금액</th></tr>
{trs or '<tr><td colspan="8">현재 저장된 대상 용역 라이프사이클 없음</td></tr>'}</table></div></section>
"""
    return layout("용역 라이프사이클", body, "용역 라이프사이클", user)


@app.get("/vendors")
def vendors_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import procurement_read_vnext as read
    q = str(request.query_params.get("q", "") or "").strip()
    try:
        limit = max(10, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200
    rows = read.vendor_rows(query=q, limit=limit)
    trs = "".join(
        f"<tr><td>{esc(r['vendor_name'])}<br><span class='muted'>{esc(r['vendor_bizno'])}</span></td>"
        f"<td class='num'>{r['shopping_rows']:,}</td><td class='num'>{r['service_contracts']:,}</td>"
        f"<td class='num'>{r['demand_org_count']:,}</td><td>{esc(', '.join(r['categories']))}</td>"
        f"<td class='num'>{money(r['shopping_amount'])}</td><td class='num'>{money(r['contract_amount'])}</td>"
        f"<td class='num'>{money(r['total_amount'])}</td></tr>"
        for r in rows
    )
    body = f"""
<section class="card"><h2>업체 분석</h2>
<p class="muted">현재 저장된 대상 납품요구와 용역 계약 사실을 업체 단위로 합산합니다. 외부 순위나 추정치는 사용하지 않습니다.</p>
<form class="row" method="get"><label>업체검색<input name="q" value="{esc(q)}" placeholder="업체명·사업자번호"></label>
<label>표시<input name="limit" type="number" min="10" max="1000" value="{limit}"></label><button class="primary">조회</button></form></section>
<section class="card"><div class="table"><table><tr><th>업체</th><th>납품건</th><th>용역계약</th><th>수요기관</th><th>분류</th><th>납품금액</th><th>용역계약금액</th><th>합계</th></tr>
{trs or '<tr><td colspan="8">현재 저장된 업체 실적 없음</td></tr>'}</table></div></section>
"""
    return layout("업체 분석", body, "업체 분석", user)


@app.get("/budget")
def budget_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import budget_read_vnext
    year_text = str(request.query_params.get("year", "") or "").strip()
    year = int(year_text) if year_text.isdigit() else None
    category = str(request.query_params.get("category", "") or "").upper().strip()
    categories = (category,) if category in TARGET_CATEGORIES else None
    payload = budget_read_vnext.budget_read_model(
        fiscal_year=year,
        categories=categories,
        limit=200,
    )
    targets = payload.get("target_rows") or []
    prebid = payload.get("prebid_rows") or []
    pipelines = payload.get("project_pipelines") or []
    opts = ['<option value="">전체 대상</option>'] + [
        f'<option value="{code}"{" selected" if category==code else ""}>{CATEGORY_LABELS[code]}</option>'
        for code in TARGET_CATEGORIES
    ]
    target_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td><td>{esc(r.get('org_name') or r.get('institution_name'))}</td>"
        f"<td>{esc(r.get('project_name'))}</td><td>{esc(CATEGORY_LABELS.get(r.get('primary_category'),r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('budget_amount'))}</td><td class='num'>{money(r.get('remaining_amount'))}</td></tr>"
        for r in targets
    )
    prebid_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td><td>{esc(r.get('org_name') or r.get('institution_name'))}</td>"
        f"<td>{esc(r.get('project_name'))}</td><td>{esc(CATEGORY_LABELS.get(r.get('primary_category'),r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('remaining_amount'))}</td></tr>" for r in prebid
    )
    pipeline_rows = "".join(
        f"<tr><td>{esc(r.get('project_name'))}</td><td>{esc(r.get('latest_known_stage'))}</td>"
        f"<td class='num'>{esc(r.get('procurement_candidate_count'))}</td><td class='num'>{money(r.get('remaining_amount'))}</td></tr>"
        for r in pipelines
    )
    body = f"""
<section class="card"><h2>예산 · 영업후보</h2>
<div class="notice">이 화면은 신규 vNext QWGJK/AIDFA/교육 RAW 구조를 사용합니다. 과거 2.2 예산 프로그램과 무관합니다.</div>
<form class="row" method="get"><label>연도<input name="year" value="{esc(year_text)}" placeholder="전체"></label>
<label>분류<select name="category">{''.join(opts)}</select></label><button class="primary">조회</button></form></section>
<div class="grid"><div class="kpi"><b>{len(targets):,}</b><span>대상 예산사업</span></div>
<div class="kpi"><b>{len(prebid):,}</b><span>공고 전 영업후보</span></div>
<div class="kpi"><b>{len(pipelines):,}</b><span>조달 파이프라인</span></div></div>
<section class="card"><h3>공고 전 영업후보</h3><div class="table"><table><tr><th>연도</th><th>기관</th><th>사업명</th><th>분류</th><th>잔액</th></tr>
{prebid_rows or '<tr><td colspan="5">현재 조건의 후보 없음</td></tr>'}</table></div></section>
<section class="card"><h3>대상 예산사업</h3><div class="table"><table><tr><th>연도</th><th>기관</th><th>사업명</th><th>분류</th><th>예산</th><th>잔액</th></tr>
{target_rows or '<tr><td colspan="6">현재 조건의 자료 없음</td></tr>'}</table></div></section>
<section class="card"><h3>예산 → 조달 진행상태</h3><div class="table"><table><tr><th>사업명</th><th>현재단계</th><th>공고후보</th><th>잔액</th></tr>
{pipeline_rows or '<tr><td colspan="4">현재 조건의 자료 없음</td></tr>'}</table></div></section>
"""
    return layout("예산·영업후보", body, "예산·영업후보", user)


@app.get("/raw")
def raw_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    counts = raw_counts()
    with connect() as conn:
        recent = conn.execute(
            "SELECT dataset,source_system,source_operation,source_key,source_date,fetched_at FROM raw_records ORDER BY id DESC LIMIT 200"
        ).fetchall()
    count_rows = "".join(
        f"<tr><td>{esc(r['dataset'])}</td><td class='num'>{int(r['n'] or 0):,}</td><td>{esc(r['last_at'])}</td></tr>"
        for r in counts
    )
    recent_rows = "".join(
        f"<tr><td>{esc(r['dataset'])}</td><td>{esc(r['source_system'])}</td><td>{esc(r['source_operation'])}</td>"
        f"<td>{esc(r['source_key'])}</td><td>{esc(r['source_date'])}</td><td>{esc(r['fetched_at'])}</td></tr>"
        for r in recent
    )
    body = f"""
<section class="card"><h2>RAW 저장소</h2><div class="notice">수집 단계에서 LED/조명 키워드로 버리지 않고 원본을 먼저 보존합니다.</div>
<div class="table"><table><tr><th>데이터셋</th><th>현재 RAW</th><th>최근수집</th></tr>{count_rows or '<tr><td colspan="3">RAW 없음</td></tr>'}</table></div></section>
<section class="card"><h3>최근 RAW 200건</h3><div class="table"><table><tr><th>데이터셋</th><th>원천</th><th>Operation</th><th>Source key</th><th>원천일자</th><th>수집시각</th></tr>
{recent_rows or '<tr><td colspan="6">RAW 없음</td></tr>'}</table></div></section>
"""
    return layout("RAW 저장소", body, "RAW 저장소", user)


@app.get("/settings")
def settings_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import lofin_vnext_http
    import readiness_vnext
    report = readiness_vnext.build_readiness_report()
    g2b_ready = bool(get_service_key(""))
    lofin_ready = bool(lofin_vnext_http.get_lofin_key())
    g2b_help = "연결됨" if g2b_ready else "G2B_SERVICE_KEY 환경변수 필요"
    lofin_help = "연결됨" if lofin_ready else "LOFIN_API_KEY 환경변수 필요"
    body = f"""
<section class="card"><h2>설정 · 운영상태</h2>
<div class="grid"><div class="kpi"><b>{'OK' if g2b_ready else '미설정'}</b><span>나라장터 서비스키</span><small>{esc(g2b_help)}</small></div>
<div class="kpi"><b>{'OK' if lofin_ready else '미설정'}</b><span>지방재정365 키</span><small>{esc(lofin_help)}</small></div>
<div class="kpi"><b>HOLD</b><span>교육 vNext live transport</span></div>
<div class="kpi"><b>HOLD</b><span>bulk historical</span></div></div>
<div class="notice"><b>수집 안전경계:</b> 현재 운영 런타임은 읽기/재정리 기능만 활성화합니다. 실원천은 bounded canary → small-validation 검증 후 확대하며 APPROVED_HISTORICAL은 아직 활성화하지 않습니다.</div>
<p>readiness: <span class="pill">{esc(report.get('status'))}</span> · deployment: <span class="pill">{esc(report.get('deployment_state'))}</span></p></section>
<section class="card"><h3>저장 RAW 재정리</h3>
<p class="muted">외부 API를 호출하지 않고 이미 저장된 RAW만 정규화·후분류합니다.</p>
<div class="actions"><form method="post" action="/organize/budget">{csrf_input(request,'/organize/budget')}<button>예산 RAW 재정리</button></form>
<form method="post" action="/organize/service">{csrf_input(request,'/organize/service')}<button>용역 RAW 재정리</button></form></div></section>
"""
    return layout("설정", body, "설정", user)


@app.post("/organize/budget")
async def organize_budget(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if not valid_csrf(request, "/organize/budget", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    import budget_reorganize_vnext
    budget_reorganize_vnext.reorganize_existing_budget_raw()
    return RedirectResponse("/budget", 303)


@app.post("/organize/service")
async def organize_service(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if not valid_csrf(request, "/organize/service", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    import service_reorganize_vnext
    service_reorganize_vnext.reorganize_existing_service_lifecycle()
    return RedirectResponse("/service", 303)


@app.get("/api/status")
def api_status(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import readiness_vnext
    return readiness_vnext.build_readiness_report()


@app.get("/api/shopping")
def api_shopping(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import procurement_read_vnext
    q, _category, categories, limit, _opts = _query_options(request)
    return procurement_read_vnext.shopping_rows(categories=categories, query=q, limit=limit)


@app.get("/api/goods")
def api_goods(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import procurement_read_vnext
    q, _category, categories, limit, _opts = _query_options(request)
    return procurement_read_vnext.goods_notice_rows(categories=categories, query=q, limit=limit)


@app.get("/api/vendors")
def api_vendors(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import procurement_read_vnext
    q = str(request.query_params.get("q", "") or "")
    return procurement_read_vnext.vendor_rows(query=q, limit=1000)


@app.get("/api/budget")
def api_budget(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import budget_read_vnext
    year_text = str(request.query_params.get("year", "") or "").strip()
    year = int(year_text) if year_text.isdigit() else None
    return budget_read_vnext.budget_read_model(fiscal_year=year, limit=500)


@app.get("/api/service")
def api_service(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import analysis_vnext
    return analysis_vnext.target_service_lifecycle_rows(limit=1000)
