"""Clean production web runtime for SINSUNG G2B vNext.

No legacy 2.2 dashboard, collector patch chain, scheduler, or serving tables are
used by this application. Source collection remains separately gated; this UI
reads and reorganizes already-preserved vNext RAW only.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app_version import APP_VERSION
from db import connect, get_service_key
from vnext_clean_db import (
    authenticate,
    create_admin,
    create_session,
    delete_session,
    ensure_clean_schema,
    session_user,
    users_empty,
)

SESSION_COOKIE = "g2b_vnext_session"
TEST_MODE = str(os.getenv("G2B_TEST_MODE", "0")).lower() in ("1", "true", "yes", "on")

ensure_clean_schema()

import budget_projection_vnext
budget_projection_vnext.ensure_schema()

app = FastAPI(title="SINSUNG G2B vNext", version=APP_VERSION)


STYLE = """
:root{font-family:Inter,Pretendard,Arial,sans-serif;color:#172033;background:#f4f6f9}
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#172033}
a{color:inherit;text-decoration:none}.top{background:#111b31;color:white;padding:18px 22px}
.brand{font-size:22px;font-weight:900}.sub{opacity:.75;margin-top:5px;font-size:13px}
.nav{display:flex;gap:8px;overflow:auto;padding:12px 16px;background:white;border-bottom:1px solid #dde2ea}
.nav a{white-space:nowrap;padding:10px 16px;border-radius:999px;background:#eef1f5;font-weight:800}
.nav a.on{background:#14213d;color:white}.wrap{max-width:1280px;margin:auto;padding:18px}
.card{background:white;border:1px solid #dde2ea;border-radius:18px;padding:20px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.kpi{background:white;border:1px solid #dde2ea;border-radius:16px;padding:18px}
.kpi b{font-size:28px;display:block;margin-bottom:8px}.muted{color:#697386}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:11px;border-bottom:1px solid #e6e9ee;text-align:left;vertical-align:top}
th{background:#f7f8fa}.table{overflow:auto}.btn,button{display:inline-block;border:1px solid #26334d;border-radius:9px;padding:10px 14px;background:white;font-weight:800;cursor:pointer}
button.primary,.primary{background:#14213d;color:white}.notice{background:#fff5cc;border:1px solid #e6d481;border-radius:12px;padding:14px;margin:12px 0;line-height:1.55}
.ok{background:#eaf8ef;border:1px solid #9bd4ac}.bad{background:#fff0f0;border:1px solid #e6aaaa}
form.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}label{font-weight:700}input,select{display:block;margin-top:6px;padding:10px;border:1px solid #c7ccd4;border-radius:8px;min-width:150px}
.auth{max-width:420px;margin:8vh auto;background:white;border:1px solid #dde2ea;border-radius:18px;padding:28px}.auth input{width:100%}.auth button{width:100%;margin-top:14px}
.actions{display:flex;gap:8px;flex-wrap:wrap}.right{float:right}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#eef1f5;font-size:12px;font-weight:800}
@media(max-width:640px){.wrap{padding:10px}.card{padding:14px}.top{padding:14px}.brand{font-size:19px}th,td{padding:9px;font-size:12px}}
"""


def esc(value):
    return html.escape(str(value or ""))


def money(value):
    try:
        return f"{int(value or 0):,}원"
    except Exception:
        return "0원"


def current_user(request: Request):
    return session_user(request.cookies.get(SESSION_COOKIE, ""))


def require_user(request: Request):
    user = current_user(request)
    if user:
        return user
    return None


def layout(title, body, active="", user=None):
    navs = [
        ("대시보드", "/dashboard"),
        ("예산·영업후보", "/budget"),
        ("용역 라이프사이클", "/service"),
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


def raw_counts():
    with connect() as conn:
        rows = conn.execute(
            "SELECT dataset,COUNT(*) n,MAX(fetched_at) last_at FROM raw_records GROUP BY dataset ORDER BY dataset"
        ).fetchall()
    return [dict(row) for row in rows]


def raw_total():
    return sum(int(row["n"] or 0) for row in raw_counts())


@app.get("/health")
@app.get("/__ai_space_health")
def health():
    return {
        "status": "ok",
        "backend_ok": True,
        "runtime": "G2B_VNEXT_CLEAN",
        "version": APP_VERSION,
        "raw_rows": raw_total(),
    }


@app.get("/")
def root(request: Request):
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
<h2>G2B vNext 최초 관리자</h2><p class="muted">기존 G2B 계정과 분리된 새 관리자 계정을 만듭니다.</p>{flash}
<form method="post" action="/setup"><label>아이디<input name="username" minlength="4" required></label>
<label>비밀번호<input type="password" name="password" minlength="10" required></label>
<label>비밀번호 확인<input type="password" name="confirm" minlength="10" required></label>
<button class="primary">관리자 생성</button></form></section></body></html>"""
    )


@app.post("/setup")
async def setup_submit(request: Request):
    if not users_empty():
        return RedirectResponse("/login", 302)
    data = await form_data(request)
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
<h2>SINSUNG G2B vNext</h2><p class="muted">신규 G2B 데이터 플랫폼</p>{flash}
<form method="post" action="/login"><label>아이디<input name="username" required></label>
<label>비밀번호<input type="password" name="password" required></label>
<button class="primary">로그인</button></form></section></body></html>"""
    )


@app.post("/login")
async def login_submit(request: Request):
    data = await form_data(request)
    user = authenticate(data.get("username"), data.get("password"))
    if not user:
        return RedirectResponse("/login?error=" + quote("아이디 또는 비밀번호를 확인해 주세요."), 302)
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
    delete_session(request.cookies.get(SESSION_COOKIE, ""))
    response = RedirectResponse("/login", 302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/dashboard")
def dashboard(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import readiness_vnext
    import budget_collection_status_vnext

    readiness = readiness_vnext.build_readiness_report()
    budget_status = budget_collection_status_vnext.budget_collection_status()
    counts = raw_counts()
    by_name = {row["dataset"]: int(row["n"] or 0) for row in counts}
    total = sum(by_name.values())
    budget_raw = sum(by_name.get(name, 0) for name in ("budget", "budget_appropriation", "education_budget"))
    service_raw = sum(by_name.get(name, 0) for name in ("bid_notice_service", "opening_result_service", "award_result_service", "contract_service"))
    goods_raw = by_name.get("bid_notice_goods", 0)
    shopping_raw = by_name.get("shopping_delivery", 0)
    body = f"""
<section class="card"><h2>G2B vNext 대시보드</h2>
<div class="notice"><b>신규 런타임:</b> 기존 2.2 화면·스케줄러·서빙 테이블을 사용하지 않습니다. 현재 수치는 vNext RAW와 파생 분석만 집계합니다.</div></section>
<div class="grid">
<div class="kpi"><b>{total:,}</b><span>전체 RAW</span></div>
<div class="kpi"><b>{shopping_raw:,}</b><span>쇼핑몰 납품요구 RAW</span></div>
<div class="kpi"><b>{goods_raw:,}</b><span>물품 공고 RAW</span></div>
<div class="kpi"><b>{service_raw:,}</b><span>용역 라이프사이클 RAW</span></div>
<div class="kpi"><b>{budget_raw:,}</b><span>예산 RAW</span></div>
</div>
<section class="card"><h3>실행 준비상태</h3>
<p><span class="pill">{esc(readiness.get("status"))}</span> · {esc(readiness.get("status_scope"))}</p>
<p class="muted">준비상태는 전체 원천수집 완료를 의미하지 않습니다.</p></section>
<section class="card"><h3>예산 저장상태</h3><pre>{esc(json.dumps(budget_status.get("totals",{}),ensure_ascii=False,indent=2))}</pre></section>
"""
    return layout("대시보드", body, "대시보드", user)


@app.get("/budget")
def budget_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import budget_read_vnext

    year_text = str(request.query_params.get("year", "") or "").strip()
    year = int(year_text) if year_text.isdigit() else None
    category = str(request.query_params.get("category", "") or "").upper().strip()
    categories = (category,) if category in ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR") else None
    payload = budget_read_vnext.budget_read_model(
        fiscal_year=year,
        categories=categories,
        limit=200,
    )
    targets = payload.get("target_rows") or []
    prebid = payload.get("prebid_rows") or []
    pipelines = payload.get("project_pipelines") or []
    opts = ['<option value="">전체</option>'] + [
        f'<option value="{code}"{" selected" if category==code else ""}>{code}</option>'
        for code in ("LIGHTING","POLE","ELECTRICAL","SOLAR")
    ]
    target_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td><td>{esc(r.get('org_name') or r.get('institution_name'))}</td>"
        f"<td>{esc(r.get('project_name'))}</td><td>{esc(r.get('primary_category'))}</td>"
        f"<td>{money(r.get('budget_amount'))}</td><td>{money(r.get('remaining_amount'))}</td></tr>"
        for r in targets
    )
    prebid_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td><td>{esc(r.get('org_name') or r.get('institution_name'))}</td>"
        f"<td>{esc(r.get('project_name'))}</td><td>{esc(r.get('primary_category'))}</td>"
        f"<td>{money(r.get('remaining_amount'))}</td></tr>" for r in prebid
    )
    pipeline_rows = "".join(
        f"<tr><td>{esc(r.get('project_name'))}</td><td>{esc(r.get('latest_known_stage'))}</td>"
        f"<td>{esc(r.get('procurement_candidate_count'))}</td><td>{money(r.get('remaining_amount'))}</td></tr>"
        for r in pipelines
    )
    body = f"""
<section class="card"><h2>예산 · 영업후보</h2>
<form class="row" method="get"><label>연도<input name="year" value="{esc(year_text)}" placeholder="전체"></label>
<label>분류<select name="category">{''.join(opts)}</select></label><button class="primary">조회</button></form></section>
<div class="grid"><div class="kpi"><b>{len(targets):,}</b><span>대상 예산사업</span></div>
<div class="kpi"><b>{len(prebid):,}</b><span>공고 전 영업후보</span></div>
<div class="kpi"><b>{len(pipelines):,}</b><span>조달 파이프라인</span></div></div>
<section class="card"><h3>공고 전 영업후보</h3><div class="table"><table><tr><th>연도</th><th>기관</th><th>사업명</th><th>분류</th><th>잔액</th></tr>
{prebid_rows or '<tr><td colspan="5">자료 없음</td></tr>'}</table></div></section>
<section class="card"><h3>대상 예산사업</h3><div class="table"><table><tr><th>연도</th><th>기관</th><th>사업명</th><th>분류</th><th>예산</th><th>잔액</th></tr>
{target_rows or '<tr><td colspan="6">자료 없음</td></tr>'}</table></div></section>
<section class="card"><h3>예산 → 조달 진행상태</h3><div class="table"><table><tr><th>사업명</th><th>현재단계</th><th>공고후보</th><th>잔액</th></tr>
{pipeline_rows or '<tr><td colspan="4">자료 없음</td></tr>'}</table></div></section>
"""
    return layout("예산·영업후보", body, "예산·영업후보", user)


@app.get("/service")
def service_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import analysis_vnext
    rows = analysis_vnext.target_service_lifecycle_rows(limit=300)
    trs = "".join(
        f"<tr><td>{esc(r.get('notice_name'))}</td><td>{esc(r.get('opening_date'))}</td>"
        f"<td>{esc(r.get('participant_count'))}</td><td>{esc(r.get('first_rank_vendor'))}</td>"
        f"<td>{esc(r.get('final_vendor'))}</td><td>{esc(r.get('contract_vendor'))}</td>"
        f"<td>{money(r.get('contract_amount'))}</td></tr>" for r in rows
    )
    body = f"""
<section class="card"><h2>용역 라이프사이클</h2>
<p class="muted">공고 → 개찰 1순위 → 최종낙찰 → 계약을 동일 실행 단위로 조회합니다.</p></section>
<section class="card"><div class="table"><table><tr><th>공고</th><th>개찰일</th><th>참가</th><th>1순위</th><th>최종낙찰</th><th>계약업체</th><th>계약금액</th></tr>
{trs or '<tr><td colspan="7">현재 저장된 용역 라이프사이클 자료 없음</td></tr>'}</table></div></section>
"""
    return layout("용역 라이프사이클", body, "용역 라이프사이클", user)


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
        f"<tr><td>{esc(r['dataset'])}</td><td>{int(r['n'] or 0):,}</td><td>{esc(r['last_at'])}</td></tr>"
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
    body = f"""
<section class="card"><h2>설정 · 운영상태</h2>
<div class="grid"><div class="kpi"><b>{'OK' if g2b_ready else '미설정'}</b><span>나라장터 서비스키</span></div>
<div class="kpi"><b>{'OK' if lofin_ready else '미설정'}</b><span>지방재정365 키</span></div>
<div class="kpi"><b>HOLD</b><span>교육 vNext live transport</span></div></div>
<div class="notice"><b>수집 안전경계:</b> bulk historical과 APPROVED_HISTORICAL은 아직 활성화하지 않습니다. 새 런타임 전환 후 bounded canary → small-validation 순으로 실제 원천을 검증합니다.</div>
<p>readiness: <span class="pill">{esc(report.get('status'))}</span></p></section>
<section class="card"><h3>런타임 상태</h3>
<p class="ok notice"><b>G2B 2.2 제거 완료:</b> 구형 화면·스케줄러·서빙 테이블·구형 DB는 이 런타임에서 사용하지 않습니다.</p></section>
<section class="card"><h3>저장 RAW 재정리</h3>
<div class="actions"><form method="post" action="/organize/budget"><button>예산 RAW 재정리</button></form>
<form method="post" action="/organize/service"><button>용역 RAW 재정리</button></form></div></section>
"""
    return layout("설정", body, "설정", user)


@app.post("/organize/budget")
def organize_budget(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import budget_reorganize_vnext
    budget_reorganize_vnext.reorganize_existing_budget_raw()
    return RedirectResponse("/budget", 303)


@app.post("/organize/service")
def organize_service(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import service_reorganize_vnext
    service_reorganize_vnext.reorganize_existing_service_lifecycle()
    return RedirectResponse("/service", 303)


@app.get("/api/status")
def api_status(request: Request):
    user = require_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import readiness_vnext
    return readiness_vnext.build_readiness_report()


@app.get("/api/budget")
def api_budget(request: Request):
    user = require_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import budget_read_vnext
    year_text = str(request.query_params.get("year", "") or "").strip()
    year = int(year_text) if year_text.isdigit() else None
    return budget_read_vnext.budget_read_model(fiscal_year=year, limit=500)


@app.get("/api/service")
def api_service(request: Request):
    user = require_user(request)
    if not user:
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    import analysis_vnext
    return analysis_vnext.target_service_lifecycle_rows(limit=1000)
