import csv
import datetime as dt
import html
import io
import json
import os
import threading
import traceback
import webbrowser
import base64
import hashlib
import hmac
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

from db import connect, get_setting, init_db, set_setting
from g2b_sync import api_usage, backfill_three_years, sync_bids_period, sync_services_period, sync_shopping_period, test_shopping_api
from scheduler import start_scheduler
from seed import clear_samples, seed_if_empty
from app_version import APP_VERSION

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv('PORT', '8503'))
HOST = os.getenv('HOST', '127.0.0.1')
PUBLIC_MODE = os.getenv('G2B_PUBLIC_MODE', '0').lower() in ('1','true','yes','on')
SESSION_SECRET = os.getenv('DASHBOARD_SECRET', '') or secrets.token_urlsafe(32)
COOKIE_SECURE = os.getenv('G2B_COOKIE_SECURE', '0').lower() in ('1','true','yes','on')
ALLOW_API_URL_EDIT = os.getenv('G2B_ALLOW_API_URL_EDIT', '0').lower() in ('1','true','yes','on')
SESSION_TTL = max(3600, int(os.getenv('DASHBOARD_SESSION_TTL', '43200')))
TODAY = dt.date(2026, 8, 14) if os.getenv('G2B_FIXED_DEMO_DATE','') else dt.date.today()
REGIONS = ['', '서울특별시','부산광역시','대구광역시','인천광역시','광주광역시','대전광역시','울산광역시','세종특별자치시','경기도','강원특별자치도','충청북도','충청남도','전북특별자치도','전라남도','경상북도','경상남도','제주특별자치도']
GROUPS = {
    'led': ('LED 조명 조달내역', ('3910161601','3911160301','3911160501','3911160801','3911161101','3911210201','3911210301')),
    'solar': ('태양광/분전함 조달내역', ('2611160701','3912110101')),
    'pole': ('등주 조달내역', ('3911152601','3911152602','3911152607')),
}
NAVS = [
    ('대시보드','/dashboard'),('LED 조명 조달내역','/g2b/shopping/prdct_detail.php?group=led'),('태양광/분전함 조달내역','/g2b/shopping/prdct_detail.php?group=solar'),('등주 조달내역','/g2b/shopping/prdct_detail.php?group=pole'),
    ('용역현황','/services'),('업체별수주조회','/vendors'),('루스계약','/category?name=루스계약'),('시장예측','/market'),('순위조회','/ranking'),
    ('매출현황','/sales'),('우리제품','/products'),('입찰','/bids'),('예산','/budgets'),('연차관리','/annual')
]

_LOGIN_FAILS = {}
_LOGIN_LOCK = threading.Lock()
_BACKFILL_THREAD = None
_BACKFILL_LOCK = threading.Lock()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')

def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))

def make_session(user: str) -> str:
    exp = int(time.time()) + SESSION_TTL
    payload = f'{user}|{exp}'.encode('utf-8')
    sig = hmac.new(SESSION_SECRET.encode('utf-8'), payload, hashlib.sha256).digest()
    return _b64e(payload) + '.' + _b64e(sig)

def valid_session(token: str) -> bool:
    try:
        p64, s64 = token.split('.', 1)
        payload = _b64d(p64)
        sig = _b64d(s64)
        expected = hmac.new(SESSION_SECRET.encode('utf-8'), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return False
        user, exp = payload.decode('utf-8').rsplit('|', 1)
        with connect() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE username=? AND status='active'", (user,)).fetchone()
        return bool(row) and int(exp) >= int(time.time())
    except Exception:
        return False

def csrf_token(path: str) -> str:
    payload = ('csrf|' + path).encode('utf-8')
    sig = hmac.new(SESSION_SECRET.encode('utf-8'), payload, hashlib.sha256).digest()
    return _b64e(sig)

def csrf_input(path: str) -> str:
    return f'<input type="hidden" name="_csrf" value="{csrf_token(path)}">'

def valid_csrf(path: str, form) -> bool:
    got = (form.get('_csrf') or [''])[0]
    return bool(got) and hmac.compare_digest(got, csrf_token(path))

def company_names():
    primary = get_setting('company_name', '').strip()
    raw = get_setting('company_aliases', '')
    names = [primary] if primary else []
    for x in str(raw or '').replace('\n', ',').split(','):
        x = x.strip()
        if x and x not in names:
            names.append(x)
    return names

def is_own_vendor(name):
    return str(name or '').strip() in set(company_names())

def _login_limited(ip):
    now = time.time()
    with _LOGIN_LOCK:
        arr = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < 600]
        _LOGIN_FAILS[ip] = arr
        return len(arr) >= 6

def _login_failed(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILS.setdefault(ip, []).append(time.time())

def _login_success(ip):
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(ip, None)

def _password_hash(password, iterations=310000):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt,iterations)
    return f'pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(digest)}'

def _password_valid(password, encoded):
    try:
        algo,iters,salt,digest=encoded.split('$',3)
        if algo!='pbkdf2_sha256': return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),_b64d(salt),int(iters))
        return hmac.compare_digest(actual,_b64d(digest))
    except Exception:
        return False

def users_empty(): return scalar('SELECT COUNT(*) FROM users') == 0

def login_html(error=''):
    err = f'<div class="flash error">{html.escape(error)}</div>' if error else ''
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LIGHTING SKETCH 로그인</title><link rel="stylesheet" href="/static/style.css"></head><body>
    <main class="authpage"><section class="card authcard"><div class="authbrand">SINSUNG</div><h2>신성라이텍 G2B</h2><p>조달 데이터 관리 시스템</p>{err}<form method="post" action="/login"><label>아이디<input name="username" autocomplete="username" required></label><label>비밀번호<input type="password" name="password" autocomplete="current-password" required></label><button class="primary" type="submit">로그인</button></form></section></main></body></html>'''

def setup_admin_html(error=''):
    err=f'<div class="flash error">{esc(error)}</div>' if error else ''
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SINSUNG 최초 관리자 설정</title><link rel="stylesheet" href="/static/style.css"></head><body><main class="authpage"><section class="card authcard"><div class="authbrand">SINSUNG</div><h2>최초 관리자 설정</h2><p>처음 사용할 관리자 계정을 직접 만들어 주세요.</p>{err}<form method="post" action="/setup-admin"><label>관리자 아이디<input name="username" minlength="4" maxlength="50" autocomplete="username" required></label><label>비밀번호<input type="password" name="password" minlength="10" autocomplete="new-password" required></label><label>비밀번호 확인<input type="password" name="password_confirm" minlength="10" autocomplete="new-password" required></label><button class="primary" type="submit">관리자 생성</button></form></section></main></body></html>'''

def esc(x): return html.escape(str(x or ''))
def money(v):
    try: return f'{int(round(float(v or 0))):,}'
    except Exception: return '0'
def qty(v):
    try:
        f=float(v or 0); return f'{int(f):,}' if f.is_integer() else f'{f:,.2f}'
    except Exception: return '0'
def pct(v):
    try: return f'{float(v):.2f}%'
    except Exception: return '0.00%'
def selected(a,b): return ' selected' if str(a)==str(b) else ''
def checked(v, arr): return ' checked' if v in arr else ''
def link(path, **kw): return path + ('?' + urlencode(kw, doseq=True) if kw else '')
def scalar(sql, vals=()):
    with connect() as conn:
        r=conn.execute(sql, vals).fetchone()
        return r[0] if r else 0

def qrows(sql, vals=()):
    with connect() as conn: return conn.execute(sql, vals).fetchall()

def date_params(qs, days=14):
    end=(qs.get('end') or [TODAY.isoformat()])[0]
    start=(qs.get('start') or [(TODAY-dt.timedelta(days=days)).isoformat()])[0]
    region=(qs.get('region') or [get_setting('default_region','인천광역시')])[0]
    return start,end,region

def where_shop(start,end,region='',q='',items=None, vendor='',detail_item_nos=None):
    c=['base_date BETWEEN ? AND ?']; v=[start,end]
    if region: c.append('demand_region=?'); v.append(region)
    if q:
        like=f'%{q}%'; c.append('(vendor_name LIKE ? OR demand_org LIKE ? OR top_org LIKE ? OR contract_name LIKE ? OR item_id LIKE ? OR item_name LIKE ? OR model_name LIKE ?)'); v += [like]*7
    if items:
        c.append('detail_item_name IN (%s)'%','.join('?' for _ in items)); v += items
    if detail_item_nos:
        c.append('detail_item_no IN (%s)'%','.join('?' for _ in detail_item_nos)); v += list(detail_item_nos)
    if vendor:
        if isinstance(vendor, (list, tuple, set)):
            names=[str(x) for x in vendor if str(x)]
            if names:
                c.append('vendor_name IN (%s)' % ','.join('?' for _ in names)); v += names
        else:
            c.append('vendor_name=?'); v.append(vendor)
    return ' AND '.join(c),v

def has_real_data(): return scalar('SELECT COUNT(*) FROM shopping_contracts WHERE is_sample=0')>0

def sample_banner():
    sample=scalar('SELECT COUNT(*) FROM shopping_contracts WHERE is_sample=1')
    real=scalar('SELECT COUNT(*) FROM shopping_contracts WHERE is_sample=0')
    if sample and not real:
        return '<div class="demo-banner"><b>샘플 데이터 모드</b> · 화면/검색/분석 검증용 데이터입니다. 설정에서 서비스키 연결 후 실데이터를 수집하세요.</div>'
    if sample and real:
        return '<div class="demo-banner warn"><b>샘플 + 실데이터 혼합</b> · 정확한 실적분석 전 설정에서 샘플 데이터를 삭제하는 것을 권장합니다.</div>'
    return ''

def base_html(content, active='대시보드', flash='', flash_error=False):
    nav=''.join(f'<a class="{"active" if n==active else ""}" href="{href}">{esc(n)}</a>' for n,href in NAVS)
    last=get_setting('last_sync') or 'LOCAL'
    flash_html=f'<div class="flash {"error" if flash_error else "ok"}">{esc(flash)}</div>' if flash else ''
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LIGHTING SKETCH G2B DATA VIEW v2.3 REVIEWED</title><link rel="stylesheet" href="/static/style.css"></head><body>
<header class="topbar"><div class="brand"><div class="logo">S</div><div><h1>SINSUNG · 신성라이텍 G2B</h1><div>나라장터 쇼핑몰 · 입찰 · 예산 · 시장분석 모니터링</div></div></div><div class="topright"><div><a href="/settings">설정</a> <span>/</span> <a href="/dashboard">새로고침</a> <span>/</span> <a href="/logout">로그아웃</a></div><span>{esc(APP_VERSION)} · {esc(last)}</span></div></header>
<nav class="nav">{nav}</nav><main>{sample_banner()}{flash_html}{content}</main></body></html>'''

def pathbar(path, suffix='lighting-sketch / g2b'):
    return f'<div class="path">{esc(path)} <span>{esc(suffix)}</span></div>'

def regopts(region): return ''.join(f'<option value="{esc(r)}"{selected(region,r)}>{esc(r or "전국")}</option>' for r in REGIONS)

def bars(rows, label_key, value_key, max_rows=8):
    rs=list(rows)[:max_rows]
    mx=max([float(r[value_key] or 0) for r in rs] or [1])
    out=['<div class="bars">']
    for r in rs:
        val=float(r[value_key] or 0); width=(val/mx*100 if mx else 0)
        out.append(f'<div class="barrow"><div class="barlabel" title="{esc(r[label_key])}">{esc(r[label_key])}</div><div class="bartrack"><i style="width:{width:.2f}%"></i></div><div class="barvalue">{money(val)}</div></div>')
    out.append('</div>'); return ''.join(out)

def line_svg(points, width=760, height=220):
    vals=[float(p[1] or 0) for p in points]
    if not vals: return '<div class="emptybox">데이터가 없습니다.</div>'
    mn=min(vals); mx=max(vals); span=mx-mn or 1
    pad=24
    coords=[]
    for i,v in enumerate(vals):
        x=pad + (width-2*pad)*(i/max(1,len(vals)-1)); y=height-pad-(height-2*pad)*(v-mn)/span
        coords.append(f'{x:.1f},{y:.1f}')
    labels=''.join(f'<text x="{pad+(width-2*pad)*(i/max(1,len(vals)-1)):.1f}" y="{height-4}" text-anchor="middle">{esc(p[0][-5:])}</text>' for i,p in enumerate(points) if i%max(1,len(points)//6)==0 or i==len(points)-1)
    return f'<svg class="linechart" viewBox="0 0 {width} {height}" preserveAspectRatio="none"><polyline points="{" ".join(coords)}" fill="none" stroke="currentColor" stroke-width="3"/><line x1="{pad}" x2="{width-pad}" y1="{height-pad}" y2="{height-pad}" stroke="currentColor" opacity=".18"/>{labels}</svg>'

def dashboard_html():
    end=TODAY; start=end-dt.timedelta(days=30); prev_start=start-dt.timedelta(days=31); prev_end=start-dt.timedelta(days=1)
    region=get_setting('default_region','인천광역시')
    where,vals=where_shop(start.isoformat(),end.isoformat(),region)
    pwhere,pvals=where_shop(prev_start.isoformat(),prev_end.isoformat(),region)
    company=get_setting('company_name')
    total=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where}',vals)
    prev=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {pwhere}',pvals)
    own_names=company_names(); own_clause='vendor_name IN (%s)'%','.join('?' for _ in own_names) if own_names else '1=0'
    own=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where} AND {own_clause}',vals+own_names)
    cnt=scalar(f'SELECT COUNT(*) FROM shopping_contracts WHERE {where}',vals)
    vendors=scalar(f'SELECT COUNT(DISTINCT vendor_name) FROM shopping_contracts WHERE {where}',vals)
    bid_where='notice_date BETWEEN ? AND ?'+(' AND region=?' if region else '')
    bid_vals=[start.isoformat(),end.isoformat()]+([region] if region else [])
    bid_cnt=scalar(f'SELECT COUNT(*) FROM bids WHERE {bid_where}',bid_vals)
    bid_budget=scalar(f'SELECT COALESCE(SUM(budget_amount),0) FROM bids WHERE {bid_where}',bid_vals)
    growth=(total-prev)/prev*100 if prev else 0
    share=own/total*100 if total else 0
    top_v=qrows(f'SELECT vendor_name,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY vendor_name ORDER BY amount DESC LIMIT 8',vals)
    top_o=qrows(f'SELECT demand_org,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY demand_org ORDER BY amount DESC LIMIT 8',vals)
    recent=qrows(f'SELECT * FROM shopping_contracts WHERE {where} ORDER BY base_date DESC,id DESC LIMIT 8',vals)
    month_rows=qrows('''SELECT substr(base_date,1,7) ym,SUM(supply_amount) amount FROM shopping_contracts WHERE base_date BETWEEN ? AND ? AND (?='' OR demand_region=?) GROUP BY ym ORDER BY ym''',((end-dt.timedelta(days=365)).isoformat(),end.isoformat(),region,region))
    trend=line_svg([(r['ym'],r['amount']) for r in month_rows])
    top_org=top_o[0]['demand_org'] if top_o else '-'
    recent_rows=''.join(f'<tr><td>{esc(r["base_date"])}</td><td>{esc(r["demand_org"])}</td><td>{esc(r["detail_item_name"])}</td><td>{esc(r["vendor_name"])}</td><td class="num">{money(r["supply_amount"])}</td></tr>' for r in recent)
    body=f'''{pathbar('/dashboard','LIGHTING SKETCH / G2B DATA VIEW')}
<section class="card page"><div class="pagehead"><div><h2>관급 조달 영업 대시보드</h2><p>{esc(region or '전국')} · 최근 30일 자동 집계</p></div><div class="quick"><a class="btn" href="/g2b/shopping/prdct_detail.php">조달내역</a><a class="btn" href="/vendors">업체순위</a><a class="btn" href="/settings">데이터수집</a></div></div>
<div class="kpis five"><div><span>신규 조달건</span><strong>{cnt:,} 건</strong><small>최근 30일</small></div><div><span>조달시장 금액</span><strong>{money(total)} 원</strong><small>전월 대비 {growth:+.1f}%</small></div><div><span>{esc(company)} 수주</span><strong>{money(own)} 원</strong><small>점유율 {share:.2f}%</small></div><div><span>경쟁 업체</span><strong>{vendors:,} 사</strong><small>1위 수요기관 {esc(top_org)}</small></div><div><span>조명 입찰공고</span><strong>{bid_cnt:,} 건</strong><small>예산합계 {money(bid_budget)}원</small></div></div>
<div class="grid2"><section class="panel"><h3>최근 12개월 조달시장 추이</h3>{trend}</section><section class="panel"><h3>업체별 수주 TOP 8</h3>{bars(top_v,'vendor_name','amount')}</section></div>
<div class="grid2"><section class="panel"><h3>수요기관 구매 TOP 8</h3>{bars(top_o,'demand_org','amount')}</section><section class="panel"><h3>최근 조달내역</h3><div class="tablewrap compact"><table><thead><tr><th>일자</th><th>수요기관</th><th>품목</th><th>업체</th><th>금액</th></tr></thead><tbody>{recent_rows or '<tr><td colspan="5" class="empty">데이터 없음</td></tr>'}</tbody></table></div></section></div></section>'''
    return base_html(body,'대시보드')

def build_shop_params(qs):
    start,end,region=date_params(qs,14)
    group=(qs.get('group') or ['led'])[0]
    if group not in GROUPS: group='led'
    try: page=max(1,int((qs.get('page') or ['1'])[0]))
    except ValueError: page=1
    return {'start':start,'end':end,'region':region,'q':(qs.get('q') or [''])[0].strip(),'items':[],'view':(qs.get('view') or ['detail'])[0],'group':group,'detail_item_nos':GROUPS[group][1],'page':page}

def query_shop(p, detail_limit=100):
    where,vals=where_shop(p['start'],p['end'],p['region'],p['q'],p['items'],detail_item_nos=p['detail_item_nos'])
    v=p['view']
    detail_sql=f'SELECT * FROM shopping_contracts WHERE {where} ORDER BY base_date DESC,id DESC'
    if detail_limit:
        detail_sql += f' LIMIT {int(detail_limit)} OFFSET {(p["page"]-1)*int(detail_limit)}'
    sql={
        'detail':detail_sql,
        'itemname':f'SELECT item_id,item_name,detail_item_name,SUM(quantity) quantity,SUM(supply_amount) supply_amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY item_id,item_name,detail_item_name ORDER BY supply_amount DESC',
        'detailitem':f'SELECT detail_item_name,SUM(quantity) quantity,SUM(supply_amount) supply_amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY detail_item_name ORDER BY supply_amount DESC',
        'region':f'SELECT demand_region,SUM(supply_amount) supply_amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY demand_region ORDER BY supply_amount DESC',
        'org':f'SELECT demand_org,SUM(supply_amount) supply_amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY demand_org ORDER BY supply_amount DESC',
        'quarter':f"SELECT substr(base_date,1,4) year, ((CAST(substr(base_date,6,2) AS INTEGER)-1)/3)+1 quarter,SUM(supply_amount) supply_amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY year,quarter ORDER BY year DESC,quarter DESC",
    }
    return qrows(sql.get(v,sql['detail']),vals),where,vals

def shop_table(p,rows,total):
    v=p['view']; out=[]
    if v=='detail':
        out.append('<table><thead><tr><th>계약/납품요구일자</th><th>계약/납품요구번호</th><th>최종</th><th>수요기관</th><th>세부품명번호·명</th><th>물품식별번호·품목명·모델명</th><th>업체명·사업자번호</th><th>계약명·방법</th><th>납품기한</th><th>단위</th><th>단가</th><th>수량</th><th>공급금액</th></tr></thead><tbody>')
        for r in rows:
            ihref=link('/g2b/shopping/prdct_detail.php',group=p['group'],start=p['start'],end=p['end'],region=p['region'],q=r['item_id'],view='detail')
            vhref=link('/vendor',name=r['vendor_name'],start=p['start'],end=p['end'],region=p['region'])
            ohref=link('/org',name=r['demand_org'],start=p['start'],end=p['end'])
            dates='<small>계약 '+esc(r['contract_date'] or '-')+'</small><small>납품 '+esc(r['delivery_req_date'] or '-')+'</small>'
            numbers='<small>계약 '+esc(r['contract_no'] or '-')+'</small><small>납품 '+esc(r['delivery_req_no'] or '-')+'</small>'
            out.append(f'<tr><td>{dates}</td><td>{numbers}</td><td>{esc(r["final_yn"] or "-")}</td><td><a href="{ohref}">{esc(r["demand_org"])}</a></td><td>{esc(r["detail_item_no"])}<small>{esc(r["detail_item_name"])}</small></td><td><a class="itemid" href="{ihref}">{esc(r["item_id"])}</a><small>{esc(r["item_name"])}{", "+esc(r["model_name"]) if r["model_name"] else ""}</small></td><td><a href="{vhref}">{esc(r["vendor_name"])}</a><small>{esc(r["vendor_bizno"])}</small></td><td>{esc(r["contract_name"])}<small>{esc(r["contract_method"])}</small></td><td>{esc(r["delivery_deadline"])}</td><td>{esc(r["unit"])}</td><td class="num">{money(r["unit_price"])}</td><td class="num">{qty(r["quantity"])}</td><td class="num">{money(r["supply_amount"])}</td></tr>')
        if not rows: out.append('<tr><td colspan="13" class="empty">검색 결과가 없습니다.</td></tr>')
    elif v=='itemname':
        out.append('<table><thead><tr><th>물품식별번호</th><th>물품식별명</th><th>세부품목명</th><th>건수</th><th>수량</th><th>공급금액</th></tr></thead><tbody>')
        for r in rows:
            href=link('/g2b/shopping/prdct_detail.php',group=p['group'],start=p['start'],end=p['end'],region=p['region'],q=r['item_id'],view='detail')
            out.append(f'<tr><td><a class="itemid" href="{href}">{esc(r["item_id"])}</a></td><td>{esc(r["item_name"])}</td><td>{esc(r["detail_item_name"])}</td><td class="num">{r["cnt"]}</td><td class="num">{qty(r["quantity"])}</td><td class="num">{money(r["supply_amount"])}</td></tr>')
    elif v=='detailitem':
        out.append('<table><thead><tr><th>세부품목명</th><th>건수</th><th>수량</th><th>공급금액</th></tr></thead><tbody>')
        for r in rows: out.append(f'<tr><td>{esc(r["detail_item_name"])}</td><td class="num">{r["cnt"]}</td><td class="num">{qty(r["quantity"])}</td><td class="num">{money(r["supply_amount"])}</td></tr>')
    elif v=='region':
        out.append('<table><thead><tr><th>수요기관 지역</th><th>건수</th><th>공급금액</th></tr></thead><tbody>')
        for r in rows: out.append(f'<tr><td>{esc(r["demand_region"] or "미분류")}</td><td class="num">{r["cnt"]}</td><td class="num">{money(r["supply_amount"])}</td></tr>')
    elif v=='org':
        out.append('<table><thead><tr><th>수요기관명</th><th>건수</th><th>공급금액</th></tr></thead><tbody>')
        for r in rows:
            href=link('/org',name=r['demand_org'],start=p['start'],end=p['end'])
            out.append(f'<tr><td><a href="{href}">{esc(r["demand_org"])}</a></td><td class="num">{r["cnt"]}</td><td class="num">{money(r["supply_amount"])}</td></tr>')
    elif v=='quarter':
        out.append('<table><thead><tr><th>연도</th><th>분기</th><th>건수</th><th>공급금액</th></tr></thead><tbody>')
        for r in rows: out.append(f'<tr><td>{esc(r["year"])}</td><td>{int(r["quarter"])}분기</td><td class="num">{r["cnt"]}</td><td class="num">{money(r["supply_amount"])}</td></tr>')
    out.append('</tbody></table>'); return ''.join(out)

def shopping_html(p):
    rows,where,vals=query_shop(p); company=get_setting('company_name')
    result_count=scalar(f'SELECT COUNT(*) FROM shopping_contracts WHERE {where}',vals) if p['view']=='detail' else len(rows)
    total=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where}',vals)
    own_names=company_names(); own_clause='vendor_name IN (%s)'%','.join('?' for _ in own_names) if own_names else '1=0'
    own=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where} AND {own_clause}',vals+own_names)
    share=own/total*100 if total else 0
    tabs=[('detail','상세내역'),('itemname','물품식별명별 합계'),('detailitem','세부품목별 합계'),('region','수요기관지역별 합계'),('org','수요기관명별 합계'),('quarter','분기별(전체) 합계')]
    tabhtml=[]
    for i,(k,lbl) in enumerate(tabs):
        href=link('/g2b/shopping/prdct_detail.php',group=p['group'],start=p['start'],end=p['end'],region=p['region'],q=p['q'],view=k)
        tabhtml.append(f'<a class="{"on" if p["view"]==k else ""}" href="{href}">{lbl}</a>')
        if i<len(tabs)-1: tabhtml.append('<b>|</b>')
    fixed=', '.join(p['detail_item_nos'])
    export_q=urlencode({'group':p['group'],'start':p['start'],'end':p['end'],'region':p['region'],'q':p['q'],'view':p['view']},doseq=True)
    title=GROUPS[p['group']][0]
    page_links=''
    if p['view']=='detail':
        page_count=max(1,(result_count+99)//100)
        prev=link('/g2b/shopping/prdct_detail.php',group=p['group'],start=p['start'],end=p['end'],region=p['region'],q=p['q'],view=p['view'],page=max(1,p['page']-1))
        nxt=link('/g2b/shopping/prdct_detail.php',group=p['group'],start=p['start'],end=p['end'],region=p['region'],q=p['q'],view=p['view'],page=min(page_count,p['page']+1))
        page_links=f'<div class="pagination"><a class="btn" href="{prev}">이전</a><b>{p["page"]:,} / {page_count:,}</b><a class="btn" href="{nxt}">다음</a></div>'
    body=f'''{pathbar('/g2b/shopping_prdct_detail.php','lighting-sketch / g2b / shopping')}
<section class="card page"><h2>{esc(title)}</h2><div class="notice">고정 세부품명번호: {esc(fixed)}</div><div class="subtabs">{''.join(tabhtml)}</div>
<form method="get" class="filters"><input type="hidden" name="group" value="{esc(p['group'])}"><input type="hidden" name="view" value="{esc(p['view'])}"><div class="filterline"><strong>기간</strong><input type="date" name="start" value="{esc(p['start'])}"><span>~</span><input type="date" name="end" value="{esc(p['end'])}"><strong>지역</strong><select name="region">{regopts(p['region'])}</select><strong>통합 검색</strong><input class="q" type="text" name="q" value="{esc(p['q'])}" placeholder="업체명, 수요기관명, 계약명, 식별명 검색"></div>
<div class="actions"><button class="primary" type="submit">검색</button><a class="btn" href="/export.csv?{export_q}">CSV</a><span class="syncinfo">{esc(get_setting('last_sync_result'))}</span></div></form>
<div class="kpis"><div><span>총 공급금액 (전체 업체)</span><strong>{money(total)} 원</strong></div><div><span>{esc(company)} 공급금액</span><strong>{money(own)} 원</strong></div><div><span>{esc(company)} 점유율</span><strong>{share:.2f} %</strong></div></div>{f'<div class="notice">검색 결과 {result_count:,}건 · 페이지당 100건 · CSV는 전체 결과를 내보냅니다.</div>' if p['view']=='detail' else ''}<div class="tablewrap">{shop_table(p,rows,total)}</div>{page_links}</section>'''
    return base_html(body,title)

def vendors_html(qs):
    start,end,region=date_params(qs,365); q=(qs.get('q') or [''])[0].strip()
    where,vals=where_shop(start,end,region,q)
    total=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where}',vals)
    rows=qrows(f'SELECT vendor_name,COUNT(*) cnt,SUM(quantity) qty,SUM(supply_amount) amount,COUNT(DISTINCT demand_org) orgs,COUNT(DISTINCT item_id) products FROM shopping_contracts WHERE {where} GROUP BY vendor_name ORDER BY amount DESC LIMIT 1000',vals)
    vendor_count=scalar(f'SELECT COUNT(DISTINCT vendor_name) FROM shopping_contracts WHERE {where}',vals)
    company=get_setting('company_name')
    own_set=set(company_names()); own_rank=next((i for i,r in enumerate(rows,1) if r['vendor_name'] in own_set),None)
    tr=[]
    for i,r in enumerate(rows,1):
        href=link('/vendor',name=r['vendor_name'],start=start,end=end,region=region)
        share=r['amount']/total*100 if total else 0
        cls=' class="ownrow"' if r['vendor_name'] in own_set else ''
        tr.append(f'<tr{cls}><td>{i}</td><td><a class="itemid" href="{href}">{esc(r["vendor_name"])}</a></td><td class="num">{r["cnt"]:,}</td><td class="num">{r["orgs"]:,}</td><td class="num">{r["products"]:,}</td><td class="num">{money(r["amount"])}</td><td class="num">{share:.2f}%</td></tr>')
    body=f'''{pathbar('/g2b/vendor_rank.php','lighting-sketch / g2b / analytics')}
<section class="card page"><h2>업체별 수주조회</h2><form class="filters"><div class="filterline"><strong>기간</strong><input type="date" name="start" value="{start}"><span>~</span><input type="date" name="end" value="{end}"><strong>지역</strong><select name="region">{regopts(region)}</select><strong>업체/기관</strong><input class="q" name="q" value="{esc(q)}"><button class="primary">검색</button></div></form>
<div class="kpis"><div><span>시장 총액</span><strong>{money(total)} 원</strong></div><div><span>참여 업체수</span><strong>{vendor_count:,} 사</strong></div><div><span>{esc(company)} 순위</span><strong>{own_rank if own_rank else '-'} 위</strong></div></div>
<div class="tablewrap"><table><thead><tr><th>순위</th><th>업체명</th><th>수주건수</th><th>수요기관수</th><th>제품수</th><th>공급금액</th><th>점유율</th></tr></thead><tbody>{''.join(tr) or '<tr><td colspan="7" class="empty">결과 없음</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'업체별수주조회')

def vendor_html(qs):
    name=(qs.get('name') or [''])[0]; start,end,region=date_params(qs,365)
    where,vals=where_shop(start,end,region,vendor=name)
    total=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where}',vals)
    market_where,market_vals=where_shop(start,end,region)
    market=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {market_where}',market_vals)
    cnt=scalar(f'SELECT COUNT(*) FROM shopping_contracts WHERE {where}',vals)
    orgs=qrows(f'SELECT demand_org,SUM(supply_amount) amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY demand_org ORDER BY amount DESC LIMIT 10',vals)
    items=qrows(f'SELECT detail_item_name,SUM(supply_amount) amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY detail_item_name ORDER BY amount DESC LIMIT 10',vals)
    months=qrows(f"SELECT substr(base_date,1,7) ym,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY ym ORDER BY ym",vals)
    recent=qrows(f'SELECT * FROM shopping_contracts WHERE {where} ORDER BY base_date DESC LIMIT 20',vals)
    rec=''.join(f'<tr><td>{esc(r["base_date"])}</td><td>{esc(r["demand_org"])}</td><td>{esc(r["contract_name"])}</td><td>{esc(r["detail_item_name"])}</td><td class="num">{money(r["supply_amount"])}</td></tr>' for r in recent)
    body=f'''{pathbar('/g2b/vendor_detail.php','lighting-sketch / vendor')}
<section class="card page"><div class="pagehead"><div><h2>{esc(name)} 분석</h2><p>{start} ~ {end} · {esc(region or '전국')}</p></div><a class="btn" href="/vendors?start={start}&end={end}&region={quote(region)}">업체목록</a></div>
<div class="kpis"><div><span>수주금액</span><strong>{money(total)} 원</strong></div><div><span>시장점유율</span><strong>{(total/market*100 if market else 0):.2f}%</strong></div><div><span>수주건수</span><strong>{cnt:,} 건</strong></div></div>
<div class="grid2"><section class="panel"><h3>월별 수주 추이</h3>{line_svg([(r['ym'],r['amount']) for r in months])}</section><section class="panel"><h3>주요 수요기관</h3>{bars(orgs,'demand_org','amount',10)}</section></div>
<div class="grid2"><section class="panel"><h3>주요 품목</h3>{bars(items,'detail_item_name','amount',10)}</section><section class="panel"><h3>최근 수주내역</h3><div class="tablewrap compact"><table><thead><tr><th>일자</th><th>수요기관</th><th>계약명</th><th>품목</th><th>금액</th></tr></thead><tbody>{rec or '<tr><td colspan="5" class="empty">데이터 없음</td></tr>'}</tbody></table></div></section></div></section>'''
    return base_html(body,'업체별수주조회')

def org_html(qs):
    name=(qs.get('name') or [''])[0]; start=(qs.get('start') or [(TODAY-dt.timedelta(days=365*3)).isoformat()])[0]; end=(qs.get('end') or [TODAY.isoformat()])[0]
    where='base_date BETWEEN ? AND ? AND demand_org=?'; vals=[start,end,name]
    total=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where}',vals)
    vendors=qrows(f'SELECT vendor_name,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY vendor_name ORDER BY amount DESC LIMIT 10',vals)
    items=qrows(f'SELECT detail_item_name,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY detail_item_name ORDER BY amount DESC LIMIT 10',vals)
    months=qrows(f"SELECT substr(base_date,1,7) ym,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY ym ORDER BY ym",vals)
    company=get_setting('company_name'); own_names=company_names(); own_clause='vendor_name IN (%s)'%','.join('?' for _ in own_names) if own_names else '1=0'; own=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where} AND {own_clause}',vals+own_names)
    body=f'''{pathbar('/g2b/demand_org_detail.php','lighting-sketch / demand-org')}
<section class="card page"><h2>수요기관 분석 · {esc(name)}</h2><div class="kpis"><div><span>최근 3년 구매액</span><strong>{money(total)} 원</strong></div><div><span>{esc(company)} 납품액</span><strong>{money(own)} 원</strong></div><div><span>자사 점유율</span><strong>{(own/total*100 if total else 0):.2f}%</strong></div></div>
<div class="grid2"><section class="panel"><h3>월별 구매 추이</h3>{line_svg([(r['ym'],r['amount']) for r in months])}</section><section class="panel"><h3>납품업체 TOP 10</h3>{bars(vendors,'vendor_name','amount',10)}</section></div><section class="panel"><h3>주요 구매품목</h3>{bars(items,'detail_item_name','amount',10)}</section></section>'''
    return base_html(body,'조명 조달내역')

def market_html(qs):
    region=(qs.get('region') or [get_setting('default_region','인천광역시')])[0]
    end=TODAY; start=end-dt.timedelta(days=730)
    rows=qrows("SELECT substr(base_date,1,7) ym,SUM(supply_amount) amount FROM shopping_contracts WHERE base_date BETWEEN ? AND ? AND (?='' OR demand_region=?) GROUP BY ym ORDER BY ym",(start.isoformat(),end.isoformat(),region,region))
    vals=[float(r['amount'] or 0) for r in rows]
    recent=vals[-6:] if vals else []
    avg=sum(recent)/len(recent) if recent else 0
    # 단순 추세선: 최근 12개월 선형 기울기 + 6개월 평균을 혼합해 다음달 규모 제시
    last12=vals[-12:]
    slope=0
    if len(last12)>=2:
        n=len(last12); xs=list(range(n)); xm=sum(xs)/n; ym=sum(last12)/n
        den=sum((x-xm)**2 for x in xs) or 1
        slope=sum((x-xm)*(y-ym) for x,y in zip(xs,last12))/den
    forecast=max(0,(last12[-1]+slope if last12 else avg)*0.55 + avg*0.45)
    last=vals[-1] if vals else 0; change=(forecast-last)/last*100 if last else 0
    top_items=qrows("SELECT detail_item_name,SUM(supply_amount) amount FROM shopping_contracts WHERE base_date BETWEEN ? AND ? AND (?='' OR demand_region=?) GROUP BY detail_item_name ORDER BY amount DESC LIMIT 10",((end-dt.timedelta(days=365)).isoformat(),end.isoformat(),region,region))
    body=f'''{pathbar('/g2b/market_forecast.php','lighting-sketch / forecast')}
<section class="card page"><div class="pagehead"><div><h2>시장예측</h2><p>최근 24개월 조달실적을 이용한 단순 추세·평균 결합 예측</p></div><form><select name="region" onchange="this.form.submit()">{regopts(region)}</select></form></div>
<div class="kpis"><div><span>최근월 시장규모</span><strong>{money(last)} 원</strong></div><div><span>다음달 추정규모</span><strong>{money(forecast)} 원</strong></div><div><span>예상 증감</span><strong>{change:+.1f}%</strong></div></div>
<div class="grid2"><section class="panel"><h3>월별 시장 추이</h3>{line_svg([(r['ym'],r['amount']) for r in rows])}</section><section class="panel"><h3>최근 1년 품목별 시장규모</h3>{bars(top_items,'detail_item_name','amount',10)}</section></div><div class="notice">예측값은 입찰 예정가격 예측이 아니라 <b>월간 조달시장 규모 참고치</b>입니다. 실데이터가 누적될수록 기관별·품목별 계절성을 추가할 수 있습니다.</div></section>'''
    return base_html(body,'시장예측')

def ranking_html(qs):
    start,end,region=date_params(qs,365); period=(qs.get('period') or ['year'])[0]
    group="substr(base_date,1,4)" if period=='year' else "substr(base_date,1,7)"
    where,vals=where_shop(start,end,region)
    rows=qrows(f'SELECT {group} period,vendor_name,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY period,vendor_name ORDER BY period DESC,amount DESC',vals)
    grouped={}
    for r in rows: grouped.setdefault(r['period'],[]).append(r)
    out=[]
    company=get_setting('company_name'); own_set=set(company_names())
    for period_name, rs in list(grouped.items())[:12]:
        total=sum(r['amount'] for r in rs)
        for i,r in enumerate(rs[:15],1):
            cls=' class="ownrow"' if r['vendor_name'] in own_set else ''
            out.append(f'<tr{cls}><td>{esc(period_name)}</td><td>{i}</td><td>{esc(r["vendor_name"])}</td><td class="num">{money(r["amount"])}</td><td class="num">{(r["amount"]/total*100 if total else 0):.2f}%</td></tr>')
    body=f'''{pathbar('/g2b/ranking.php','lighting-sketch / ranking')}<section class="card page"><h2>순위조회</h2><form class="filters"><div class="filterline"><strong>기간</strong><input type="date" name="start" value="{start}"><span>~</span><input type="date" name="end" value="{end}"><strong>지역</strong><select name="region">{regopts(region)}</select><strong>집계</strong><select name="period"><option value="year"{selected(period,'year')}>연도별</option><option value="month"{selected(period,'month')}>월별</option></select><button class="primary">조회</button></div></form><div class="tablewrap"><table><thead><tr><th>기간</th><th>순위</th><th>업체명</th><th>수주금액</th><th>점유율</th></tr></thead><tbody>{''.join(out) or '<tr><td colspan="5" class="empty">결과 없음</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'순위조회')

def sales_html(qs):
    start,end,region=date_params(qs,730); company=get_setting('company_name')
    where,vals=where_shop(start,end,region,vendor=company_names())
    total=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where}',vals)
    months=qrows(f"SELECT substr(base_date,1,7) ym,SUM(supply_amount) amount,COUNT(*) cnt FROM shopping_contracts WHERE {where} GROUP BY ym ORDER BY ym",vals)
    items=qrows(f'SELECT detail_item_name,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY detail_item_name ORDER BY amount DESC',vals)
    orgs=qrows(f'SELECT demand_org,SUM(supply_amount) amount FROM shopping_contracts WHERE {where} GROUP BY demand_org ORDER BY amount DESC LIMIT 12',vals)
    avg=total/max(1,len(months)); latest=months[-1]['amount'] if months else 0
    body=f'''{pathbar('/g2b/sales.php','lighting-sketch / own-sales')}<section class="card page"><h2>매출현황 · {esc(company)}</h2><form class="filters"><div class="filterline"><strong>기간</strong><input type="date" name="start" value="{start}"><span>~</span><input type="date" name="end" value="{end}"><strong>지역</strong><select name="region">{regopts(region)}</select><button class="primary">조회</button></div></form><div class="kpis"><div><span>기간 수주액</span><strong>{money(total)} 원</strong></div><div><span>월평균</span><strong>{money(avg)} 원</strong></div><div><span>최근월</span><strong>{money(latest)} 원</strong></div></div><div class="grid2"><section class="panel"><h3>월별 수주 추이</h3>{line_svg([(r['ym'],r['amount']) for r in months])}</section><section class="panel"><h3>품목별 매출</h3>{bars(items,'detail_item_name','amount',10)}</section></div><section class="panel"><h3>주요 수요기관</h3>{bars(orgs,'demand_org','amount',12)}</section></section>'''
    return base_html(body,'매출현황')

def products_html(qs):
    start,end,region=date_params(qs,1095); company=get_setting('company_name')
    own_where,own_vals=where_shop(start,end,region,vendor=company_names())
    rows=qrows(f'SELECT item_id,item_name,model_name,detail_item_name,MAX(unit_price) unit_price,SUM(quantity) qty,SUM(supply_amount) amount,COUNT(*) cnt FROM shopping_contracts WHERE {own_where} GROUP BY item_id,item_name,model_name,detail_item_name ORDER BY amount DESC',own_vals)
    tr=[]
    for r in rows:
        mwhere,mvals=where_shop(start,end,region,q=r['item_id'])
        market=scalar(f'SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {mwhere}',mvals)
        tr.append(f'<tr><td><a class="itemid" href="{link("/g2b/shopping/prdct_detail.php",q=r["item_id"],start=start,end=end,region=region)}">{esc(r["item_id"])}</a></td><td>{esc(r["detail_item_name"])}</td><td>{esc(r["model_name"])}</td><td>{esc(r["item_name"])}</td><td class="num">{money(r["unit_price"])}</td><td class="num">{qty(r["qty"])}</td><td class="num">{money(r["amount"])}</td><td class="num">{(r["amount"]/market*100 if market else 0):.2f}%</td></tr>')
    body=f'''{pathbar('/g2b/our_products.php','lighting-sketch / products')}<section class="card page"><h2>우리제품</h2><p>{esc(company)}로 납품된 물품식별번호를 자동 집계합니다.</p><div class="tablewrap"><table><thead><tr><th>식별번호</th><th>세부품목</th><th>모델명</th><th>식별명</th><th>최근단가</th><th>누적수량</th><th>누적매출</th><th>동일식별 시장점유</th></tr></thead><tbody>{''.join(tr) or '<tr><td colspan="8" class="empty">회사명과 일치하는 데이터가 없습니다.</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'우리제품')

def bids_html(qs):
    start,end,region=date_params(qs,60); q=(qs.get('q') or [''])[0].strip(); status=(qs.get('status') or ['all'])[0]
    clauses=['notice_date BETWEEN ? AND ?']; vals=[start,end]
    if region: clauses.append('region=?'); vals.append(region)
    if q:
        like=f'%{q}%'; clauses.append('(notice_name LIKE ? OR notice_org LIKE ? OR demand_org LIKE ?)'); vals += [like]*3
    if status=='open': clauses.append('(close_date="" OR close_date>=?)'); vals.append(TODAY.isoformat())
    where=' AND '.join(clauses)
    rows=qrows(f'SELECT * FROM bids WHERE {where} ORDER BY notice_date DESC,id DESC LIMIT 1000',vals)
    budget=sum(r['budget_amount'] for r in rows); open_cnt=sum(1 for r in rows if not r['close_date'] or r['close_date']>=TODAY.isoformat())
    tr=[]
    for r in rows:
        nm=esc(r['notice_name']); nm=f'<a target="_blank" rel="noopener" href="{esc(r["url"])}">{nm}</a>' if r['url'] else nm
        tr.append(f'<tr><td>{esc(r["notice_date"])}</td><td>{esc(r["notice_no"])}</td><td>{nm}</td><td>{esc(r["demand_org"] or r["notice_org"])}</td><td>{esc(r["method_name"])}</td><td class="num">{money(r["budget_amount"])}</td><td>{esc(r["close_date"])}</td></tr>')
    body=f'''{pathbar('/g2b/bids.php','lighting-sketch / bids')}<section class="card page"><h2>입찰</h2><form class="filters"><div class="filterline"><strong>기간</strong><input type="date" name="start" value="{start}"><span>~</span><input type="date" name="end" value="{end}"><strong>지역</strong><select name="region">{regopts(region)}</select><select name="status"><option value="all"{selected(status,'all')}>전체</option><option value="open"{selected(status,'open')}>진행중</option></select><input class="q" name="q" value="{esc(q)}" placeholder="공고명/기관"><button class="primary">검색</button></div></form><div class="kpis"><div><span>검색 공고</span><strong>{len(rows):,} 건</strong></div><div><span>진행중</span><strong>{open_cnt:,} 건</strong></div><div><span>공고 예산 합계</span><strong>{money(budget)} 원</strong></div></div><div class="tablewrap"><table><thead><tr><th>공고일</th><th>공고번호</th><th>공고명</th><th>수요기관</th><th>입찰방법</th><th>예산</th><th>마감일</th></tr></thead><tbody>{''.join(tr) or '<tr><td colspan="7" class="empty">결과 없음</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'입찰')

def budgets_html(qs):
    year=int((qs.get('year') or [str(TODAY.year)])[0]); region=(qs.get('region') or [get_setting('default_region','인천광역시')])[0]; q=(qs.get('q') or [''])[0]
    clauses=['fiscal_year=?']; vals=[year]
    if region: clauses.append('region=?'); vals.append(region)
    if q:
        like=f'%{q}%'; clauses.append('(org_name LIKE ? OR project_name LIKE ? OR category LIKE ?)'); vals += [like]*3
    where=' AND '.join(clauses); rows=qrows(f'SELECT * FROM budget_items WHERE {where} ORDER BY budget_amount DESC,id DESC LIMIT 1000',vals)
    total=sum(r['budget_amount'] for r in rows)
    tr=''.join(f'<tr><td>{r["fiscal_year"]}</td><td>{esc(r["region"])}</td><td>{esc(r["org_name"])}</td><td>{esc(r["project_name"])}</td><td>{esc(r["category"])}</td><td class="num">{money(r["budget_amount"])}</td><td>{esc(r["status"])}</td><td>{esc(r["source"])}</td></tr>' for r in rows)
    body=f'''{pathbar('/g2b/budget.php','lighting-sketch / budget')}<section class="card page"><h2>예산</h2><form class="filters"><div class="filterline"><strong>연도</strong><select name="year">{''.join(f'<option{selected(year,y)}>{y}</option>' for y in range(TODAY.year-2,TODAY.year+3))}</select><strong>지역</strong><select name="region">{regopts(region)}</select><input class="q" name="q" value="{esc(q)}" placeholder="기관/사업명/품목"><button class="primary">검색</button></div></form><div class="kpis"><div><span>예산 사업수</span><strong>{len(rows):,} 건</strong></div><div><span>예산 합계</span><strong>{money(total)} 원</strong></div><div><span>데이터 상태</span><strong>{'샘플/수기' if rows and rows[0]['is_sample'] else '실데이터/수기' if rows else '-'}</strong></div></div><div class="notice">예산 데이터는 기관별 공개형식이 달라 현재 버전에서는 DB/CSV 적재 구조를 우선 제공합니다. 인천·서울·경기 등 기관별 예산 공개자료 자동수집은 별도 연동이 필요합니다.</div><div class="tablewrap"><table><thead><tr><th>연도</th><th>지역</th><th>기관</th><th>사업명</th><th>분류</th><th>예산액</th><th>상태</th><th>출처</th></tr></thead><tbody>{tr or '<tr><td colspan="8" class="empty">결과 없음</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'예산')

def annual_html():
    company=get_setting('company_name'); names=company_names(); placeholders=','.join('?' for _ in names) or "''"
    rows=qrows(f'''SELECT substr(base_date,1,4) yr,SUM(supply_amount) market,SUM(CASE WHEN vendor_name IN ({placeholders}) THEN supply_amount ELSE 0 END) own,COUNT(*) cnt,COUNT(DISTINCT vendor_name) vendors FROM shopping_contracts GROUP BY yr ORDER BY yr DESC''', names)
    tr=[]
    prev=None
    for r in reversed(rows):
        growth=(r['own']-prev)/prev*100 if prev else 0; prev=r['own']
        tr.append((r,growth))
    bodyrows=''.join(f'<tr><td>{esc(r["yr"])}</td><td class="num">{money(r["market"])}</td><td class="num">{money(r["own"])}</td><td class="num">{(r["own"]/r["market"]*100 if r["market"] else 0):.2f}%</td><td class="num">{growth:+.1f}%</td><td class="num">{r["cnt"]:,}</td><td class="num">{r["vendors"]:,}</td></tr>' for r,growth in reversed(tr))
    body=f'''{pathbar('/g2b/annual.php','lighting-sketch / annual')}<section class="card page"><h2>연차관리</h2><p>연도별 조달시장·자사 매출·점유율 추이를 자동 집계합니다.</p><div class="tablewrap"><table><thead><tr><th>연도</th><th>시장규모</th><th>{esc(company)} 매출</th><th>점유율</th><th>자사 성장률</th><th>계약건수</th><th>업체수</th></tr></thead><tbody>{bodyrows or '<tr><td colspan="7" class="empty">데이터 없음</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'연차관리')

def services_html(qs):
    # 입찰 표준데이터 중 용역/설계 키워드 조회 + 쇼핑 DB와 분리 표시
    start,end,region=date_params(qs,180); q=(qs.get('q') or [''])[0]
    clauses=['notice_date BETWEEN ? AND ?']; vals=[start,end]
    if region: clauses.append('region=?'); vals.append(region)
    clauses.append('(business_type LIKE ? OR notice_name LIKE ? OR notice_name LIKE ?)'); vals += ['%용역%','%설계%','%용역%']
    if q: clauses.append('(notice_name LIKE ? OR demand_org LIKE ?)'); vals += [f'%{q}%',f'%{q}%']
    rows=qrows(f"SELECT * FROM bids WHERE {' AND '.join(clauses)} ORDER BY notice_date DESC LIMIT 1000",vals)
    tr=''.join(f'<tr><td>{esc(r["notice_date"])}</td><td>{esc(r["notice_name"])}</td><td>{esc(r["demand_org"] or r["notice_org"])}</td><td>{esc(r["business_type"])}</td><td class="num">{money(r["budget_amount"])}</td><td>{esc(r["close_date"])}</td></tr>' for r in rows)
    body=f'''{pathbar('/g2b/services.php','lighting-sketch / service')}<section class="card page"><h2>용역현황</h2><p>입찰공고 데이터 중 용역·설계 관련 공고를 분리 조회합니다.</p><form class="filters"><div class="filterline"><strong>기간</strong><input type="date" name="start" value="{start}"><span>~</span><input type="date" name="end" value="{end}"><strong>지역</strong><select name="region">{regopts(region)}</select><input class="q" name="q" value="{esc(q)}"><button class="primary">조회</button></div></form><div class="tablewrap"><table><thead><tr><th>공고일</th><th>용역명</th><th>수요기관</th><th>구분</th><th>예산</th><th>마감</th></tr></thead><tbody>{tr or '<tr><td colspan="6" class="empty">결과 없음</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'용역현황')

def category_html(qs):
    name=(qs.get('name') or ['기타'])[0]; start,end,region=date_params(qs,365)
    terms=['태양광','분전함'] if '태양광' in name else ['루스','LUS','루스테크']
    clauses=['base_date BETWEEN ? AND ?']; vals=[start,end]
    if region: clauses.append('demand_region=?'); vals.append(region)
    sub=[]
    for t in terms:
        sub.append('(contract_name LIKE ? OR item_name LIKE ? OR detail_item_name LIKE ? OR vendor_name LIKE ?)'); vals += [f'%{t}%']*4
    clauses.append('('+' OR '.join(sub)+')')
    rows=qrows(f"SELECT * FROM shopping_contracts WHERE {' AND '.join(clauses)} ORDER BY base_date DESC LIMIT 1000",vals)
    tr=''.join(f'<tr><td>{esc(r["base_date"])}</td><td>{esc(r["demand_org"])}</td><td>{esc(r["contract_name"])}</td><td>{esc(r["item_name"])}</td><td>{esc(r["vendor_name"])}</td><td class="num">{money(r["supply_amount"])}</td></tr>' for r in rows)
    body=f'''{pathbar('/g2b/category.php','lighting-sketch / category')}<section class="card page"><h2>{esc(name)}</h2><div class="notice">현재 DB에서 관련 키워드로 자동 분류합니다. 해당 품목을 실데이터로 수집하면 자동으로 목록에 반영됩니다.</div><div class="tablewrap"><table><thead><tr><th>일자</th><th>수요기관</th><th>계약명</th><th>품목</th><th>업체</th><th>금액</th></tr></thead><tbody>{tr or '<tr><td colspan="6" class="empty">현재 수집 데이터에 해당 품목이 없습니다.</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,name if name in [n for n,_ in NAVS] else '')

def settings_html(msg='', error=False):
    logs=qrows('SELECT * FROM sync_logs ORDER BY id DESC LIMIT 12')
    env_api = bool(os.getenv('G2B_SERVICE_KEY'))
    api_value = '' if env_api else get_setting('api_key')
    api_placeholder = '서버 환경변수(G2B_SERVICE_KEY)로 설정됨' if env_api else '공공데이터포털 서비스키 입력'
    logrows=''.join(f'<tr><td>{esc(r["sync_type"])}</td><td>{esc(r["range_start"])} ~ {esc(r["range_end"])}</td><td>{esc(r["status"])}</td><td class="num">{r["processed"]:,}</td><td>{esc(r["started_at"])}</td><td>{esc(r["message"])}</td></tr>' for r in logs)
    ae=get_setting('auto_sync_enabled','0'); status=get_setting('backfill_status','대기'); progress=get_setting('backfill_progress','0'); bmsg=get_setting('backfill_message','')
    shop_calls,call_limit=api_usage('shop'); bid_calls,_=api_usage('bid')
    body=f'''{pathbar('/settings','lighting-sketch / system')}<section class="card settings"><h2>데이터 수집 설정</h2><p>종합쇼핑몰 납품요구 데이터와 입찰공고를 자체 SQLite DB에 누적합니다. 서버 공개형에서는 서비스키를 환경변수에 저장하는 방식을 권장합니다.</p>
<form method="post" action="/settings">{csrf_input('/settings')}<label>우리 회사명<input name="company_name" value="{esc(get_setting('company_name'))}"></label><label>회사명 별칭(쉼표 구분)<input name="company_aliases" value="{esc(get_setting('company_aliases'))}" placeholder="(주)라이팅스케치, 라이팅스케치"></label><label>기본 지역<select name="default_region">{regopts(get_setting('default_region'))}</select></label><label>공공데이터포털 서비스키<input type="password" name="api_key" value="{esc(api_value)}" placeholder="{esc(api_placeholder)}"{' disabled' if env_api else ''}></label><label>쇼핑몰 API Base URL<input name="shop_api_base_url" value="{esc(get_setting('shop_api_base_url'))}"{' disabled' if PUBLIC_MODE and not ALLOW_API_URL_EDIT else ''}></label><label>납품요구상세 오퍼레이션명<input name="shop_api_operation" value="{esc(get_setting('shop_api_operation'))}" placeholder="getDlvrReqDtlInfoList"{' disabled' if PUBLIC_MODE and not ALLOW_API_URL_EDIT else ''}></label><label>입찰 API Base URL<input name="bid_api_base_url" value="{esc(get_setting('bid_api_base_url'))}"{' disabled' if PUBLIC_MODE and not ALLOW_API_URL_EDIT else ''}></label><div class="settingrow"><label><input type="checkbox" name="auto_sync_enabled" value="1"{' checked' if ae=='1' else ''}> 프로그램 실행 중 자동수집</label><label>수집주기(시간)<input type="number" min="1" name="auto_sync_hours" value="{esc(get_setting('auto_sync_hours','3'))}"></label><label>쇼핑몰 최근 수집일수<input type="number" min="1" max="90" name="auto_sync_days" value="{esc(get_setting('auto_sync_days','14'))}"></label><label>API 일일 안전한도<input type="number" min="100" max="100000" name="api_daily_limit" value="{esc(get_setting('api_daily_limit','900'))}"{' disabled' if os.getenv('G2B_API_DAILY_LIMIT') else ''}></label></div><button class="primary">설정 저장</button></form>
<hr><h3>수동 동기화</h3><div class="syncgrid"><form method="post" action="/sync-shop" class="syncform">{csrf_input('/sync-shop')}<b>쇼핑몰</b><input type="date" name="start" value="{(TODAY-dt.timedelta(days=14)).isoformat()}"><span>~</span><input type="date" name="end" value="{TODAY.isoformat()}"><button class="primary">수집 실행</button></form><form method="post" action="/sync-bids" class="syncform">{csrf_input('/sync-bids')}<b>물품 입찰공고</b><input type="date" name="start" value="{(TODAY-dt.timedelta(days=27)).isoformat()}"><span>~</span><input type="date" name="end" value="{TODAY.isoformat()}"><button class="primary">수집 실행</button></form><form method="post" action="/sync-services" class="syncform">{csrf_input('/sync-services')}<b>용역공고</b><input type="date" name="start" value="{(TODAY-dt.timedelta(days=27)).isoformat()}"><span>~</span><input type="date" name="end" value="{TODAY.isoformat()}"><button class="primary">수집 실행</button></form></div>
<div class="actions"><form method="post" action="/api-test">{csrf_input('/api-test')}<button class="btn">쇼핑몰 API 연결 테스트</button></form><form method="post" action="/backfill">{csrf_input('/backfill')}<button class="btn danger-lite" onclick="return confirm('최근 3년을 월 단위로 순차 수집합니다. API 호출량이 많을 수 있습니다. 시작할까요?')">최근 3년 구축 시작</button></form><form method="post" action="/clear-samples">{csrf_input('/clear-samples')}<button class="btn" onclick="return confirm('샘플 데이터만 삭제합니다. 실데이터는 삭제하지 않습니다.')">샘플 데이터 삭제</button></form></div>
<hr><h3>쇼핑몰 실데이터 초기화</h3><div class="notice danger-notice">shopping_contracts 실데이터만 삭제합니다. API 키·자동수집·사용자·입찰·용역 데이터는 유지됩니다.</div><form method="post" action="/reset-shopping-data">{csrf_input('/reset-shopping-data')}<label>확인문구 <input name="confirmation" autocomplete="off" placeholder="쇼핑몰 실데이터 초기화" required></label><button class="btn danger-lite">실데이터 초기화</button></form>
<div class="progress"><div><b>3년 구축 상태:</b> {esc(status)} · {esc(progress)}%</div><div class="bartrack"><i style="width:{esc(progress)}%"></i></div><small>{esc(bmsg)}</small></div>
<p><b>오늘 API 호출:</b> 쇼핑몰 {shop_calls:,}/{call_limit:,} · 입찰/용역 {bid_calls:,}/{call_limit:,}</p><p><b>쇼핑몰 최근 동기화:</b> {esc(get_setting('last_sync') or '-')}<br>{esc(get_setting('last_sync_result'))}</p><p><b>물품 입찰 최근 동기화:</b> {esc(get_setting('last_bid_sync') or '-')}<br>{esc(get_setting('last_bid_sync_result'))}</p><p><b>용역 최근 동기화:</b> {esc(get_setting('last_service_sync') or '-')}<br>{esc(get_setting('last_service_sync_result'))}</p>
<div class="notice"><b>쇼핑몰 계약자료 수집 진단</b><br>원본 {esc(get_setting('last_shop_raw_count','0'))}건 → 조명 대상 {esc(get_setting('last_shop_matched_count','0'))}건 → 저장·갱신 {esc(get_setting('last_shop_saved_count','0'))}건 · 필수값 누락 {esc(get_setting('last_shop_skipped_count','0'))}건<br>첫 응답 필드: {esc(get_setting('last_shop_first_fields') or '-')}<br>최근 오류: {esc(get_setting('last_shop_error') or '-')}</div>
<div class="notice">2026-08-15 재검토 기준 조달청 공식 명세에 맞춰 쇼핑몰은 <b>getDlvrReqDtlInfoList</b>, 입찰은 <b>getBidPblancListInfoThng</b> 기본값을 사용합니다. 특별한 변경 공지가 없는 한 오퍼레이션명을 수정할 필요가 없습니다.</div>
<hr><h3>최근 수집 로그</h3><div class="tablewrap compact"><table><thead><tr><th>구분</th><th>기간</th><th>상태</th><th>처리</th><th>시작</th><th>메시지</th></tr></thead><tbody>{logrows or '<tr><td colspan="6" class="empty">로그 없음</td></tr>'}</tbody></table></div></section>'''
    return base_html(body,'',msg,error)


def export_csv(qs):
    p=build_shop_params(qs); rows,_,_=query_shop(p, detail_limit=None); out=io.StringIO(); w=csv.writer(out)
    if p['view']=='detail':
        w.writerow(['계약일자','납품요구일자','계약번호','납품요구번호','최종여부','수요기관','지역','세부품명번호','세부품명','물품식별번호','품목명','모델명','업체명','사업자번호','계약명','계약방법','납품기한','단위','단가','수량','공급금액'])
        for r in rows: w.writerow([r['contract_date'],r['delivery_req_date'],r['contract_no'],r['delivery_req_no'],r['final_yn'],r['demand_org'],r['demand_region'],r['detail_item_no'],r['detail_item_name'],r['item_id'],r['item_name'],r['model_name'],r['vendor_name'],r['vendor_bizno'],r['contract_name'],r['contract_method'],r['delivery_deadline'],r['unit'],r['unit_price'],r['quantity'],r['supply_amount']])
    elif rows:
        w.writerow(rows[0].keys())
        for r in rows: w.writerow(list(r))
    return ('\ufeff'+out.getvalue()).encode('utf-8')


def start_backfill_thread():
    global _BACKFILL_THREAD
    with _BACKFILL_LOCK:
        if _BACKFILL_THREAD is not None and _BACKFILL_THREAD.is_alive():
            return False
        def runner():
            try:
                backfill_three_years()
            except Exception:
                traceback.print_exc()
        _BACKFILL_THREAD = threading.Thread(target=runner,name='g2b-backfill',daemon=True)
        _BACKFILL_THREAD.start()
        return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): print('[HTTP]', fmt%args)
    def send_bytes(self, data, content_type='text/html; charset=utf-8', status=200, headers=None):
        if isinstance(data,str): data=data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        self.send_header('Cache-Control','no-store')
        if headers:
            for k,v in headers.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(data)
    def redirect(self,path,headers=None):
        self.send_response(302); self.send_header('Location',path)
        self.send_header('Cache-Control','no-store')
        if headers:
            for k,v in headers.items(): self.send_header(k,v)
        self.end_headers()
    def parse_post(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        if n < 0 or n > 65536:
            raise ValueError('POST body too large')
        return parse_qs(self.rfile.read(n).decode('utf-8'))
    def client_ip(self):
        forwarded=(self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
        return forwarded or self.client_address[0]
    def cookie(self, name):
        raw=self.headers.get('Cookie','')
        for part in raw.split(';'):
            if '=' in part:
                k,v=part.strip().split('=',1)
                if k==name: return v
        return ''
    def authenticated(self):
        return valid_session(self.cookie('ls_session'))
    def require_auth(self, path):
        if path in ('/login','/setup-admin','/health') or path.startswith('/static/'):
            return False
        if users_empty():
            self.redirect('/setup-admin')
            return True
        if self.authenticated():
            return False
        self.redirect('/login')
        return True
    def do_GET(self):
        try:
            u=urlparse(self.path); qs=parse_qs(u.query)
            if self.require_auth(u.path): return
            if u.path=='/setup-admin':
                if not users_empty(): return self.redirect('/login')
                return self.send_bytes(setup_admin_html((qs.get('error') or [''])[0]))
            if u.path=='/login':
                if users_empty(): return self.redirect('/setup-admin')
                if self.authenticated(): return self.redirect('/dashboard')
                return self.send_bytes(login_html((qs.get('error') or [''])[0]))
            if u.path=='/logout':
                cookie='ls_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax' + ('; Secure' if COOKIE_SECURE else '')
                return self.redirect('/login', {'Set-Cookie':cookie})
            if u.path=='/': return self.redirect('/dashboard')
            if u.path=='/static/style.css':
                with open(os.path.join(BASE_DIR,'static','style.css'),'rb') as f: return self.send_bytes(f.read(),'text/css; charset=utf-8')
            routes={
                '/dashboard':lambda:dashboard_html(),
                '/g2b/shopping/prdct_detail.php':lambda:shopping_html(build_shop_params(qs)),
                '/vendors':lambda:vendors_html(qs), '/vendor':lambda:vendor_html(qs), '/org':lambda:org_html(qs),
                '/market':lambda:market_html(qs), '/ranking':lambda:ranking_html(qs), '/sales':lambda:sales_html(qs),
                '/products':lambda:products_html(qs), '/bids':lambda:bids_html(qs), '/budgets':lambda:budgets_html(qs),
                '/annual':lambda:annual_html(), '/services':lambda:services_html(qs), '/category':lambda:category_html(qs),
                '/settings':lambda:settings_html((qs.get('msg') or [''])[0], (qs.get('error') or ['0'])[0]=='1'),
            }
            if u.path in routes: return self.send_bytes(routes[u.path]())
            if u.path=='/export.csv': return self.send_bytes(export_csv(qs),'text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=lighting_sketch_g2b_export.csv'})
            if u.path=='/health':
                return self.send_bytes(json.dumps({'ok':True,'version':APP_VERSION,'shopping_rows':scalar('SELECT COUNT(*) FROM shopping_contracts'),'bid_rows':scalar('SELECT COUNT(*) FROM bids')},ensure_ascii=False),'application/json; charset=utf-8')
            return self.send_bytes('Not Found','text/plain; charset=utf-8',404)
        except Exception as e:
            traceback.print_exc(); return self.send_bytes(f'<pre>{esc(e)}</pre>',status=500)
    def do_POST(self):
        try:
            u=urlparse(self.path); form=self.parse_post()
            if u.path=='/setup-admin':
                if not users_empty(): return self.redirect('/login')
                user=(form.get('username') or [''])[0].strip(); password=(form.get('password') or [''])[0]; confirm=(form.get('password_confirm') or [''])[0]
                if len(user)<4 or len(password)<10 or password!=confirm:
                    return self.redirect('/setup-admin?error='+quote('아이디는 4자 이상, 비밀번호는 10자 이상이며 확인값과 같아야 합니다.'))
                with connect() as conn: conn.execute("INSERT INTO users(username,password_hash,role,status) VALUES (?,?, 'admin','active')",(user,_password_hash(password)))
                return self.redirect('/login')
            if u.path=='/login':
                if users_empty(): return self.redirect('/setup-admin')
                ip=self.client_ip()
                if _login_limited(ip):
                    return self.send_bytes(login_html('로그인 실패가 반복되어 10분간 잠시 제한됩니다.'), status=429)
                user=(form.get('username') or [''])[0]
                password=(form.get('password') or [''])[0]
                with connect() as conn: row=conn.execute("SELECT password_hash FROM users WHERE username=? AND status='active'",(user,)).fetchone()
                if row and _password_valid(password,row['password_hash']):
                    _login_success(ip)
                    cookie=f'ls_session={make_session(user)}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax' + ('; Secure' if COOKIE_SECURE else '')
                    return self.redirect('/dashboard', {'Set-Cookie':cookie})
                _login_failed(ip)
                return self.redirect('/login?error='+quote('아이디 또는 비밀번호가 올바르지 않습니다.'))
            if self.require_auth(u.path): return
            if not valid_csrf(u.path, form):
                return self.send_bytes('CSRF validation failed','text/plain; charset=utf-8',403)
            if u.path=='/settings':
                for k in ['company_name','company_aliases','default_region','auto_sync_hours','auto_sync_days','api_daily_limit']:
                    if k in form: set_setting(k,form[k][0])
                if (not PUBLIC_MODE or ALLOW_API_URL_EDIT):
                    for k in ['shop_api_base_url','shop_api_operation','bid_api_base_url']:
                        if k in form: set_setting(k,form[k][0])
                if 'api_key' in form and form['api_key'][0].strip() and not os.getenv('G2B_SERVICE_KEY'):
                    set_setting('api_key',form['api_key'][0].strip())
                set_setting('auto_sync_enabled','1' if 'auto_sync_enabled' in form else '0')
                return self.redirect('/settings?msg='+quote('설정을 저장했습니다.'))
            if u.path=='/sync-shop':
                start=(form.get('start') or [(TODAY-dt.timedelta(days=14)).isoformat()])[0]; end=(form.get('end') or [TODAY.isoformat()])[0]
                try: msg=f'쇼핑몰 동기화 완료: {sync_shopping_period(start,end):,}건 저장·갱신'; err=0
                except Exception as e: msg=f'쇼핑몰 동기화 실패: {e}'; err=1
                return self.redirect('/settings?error=%d&msg=%s'%(err,quote(msg)))
            if u.path=='/sync-bids':
                start=(form.get('start') or [(TODAY-dt.timedelta(days=27)).isoformat()])[0]; end=(form.get('end') or [TODAY.isoformat()])[0]
                try: msg=f'입찰공고 동기화 완료: {sync_bids_period(start,end):,}건 저장·갱신'; err=0
                except Exception as e: msg=f'입찰공고 동기화 실패: {e}'; err=1
                return self.redirect('/settings?error=%d&msg=%s'%(err,quote(msg)))
            if u.path=='/sync-services':
                start=(form.get('start') or [(TODAY-dt.timedelta(days=27)).isoformat()])[0]; end=(form.get('end') or [TODAY.isoformat()])[0]
                try: msg=f'용역공고 동기화 완료: {sync_services_period(start,end):,}건 저장·갱신'; err=0
                except Exception as e: msg=f'용역공고 동기화 실패: {e}'; err=1
                return self.redirect('/settings?error=%d&msg=%s'%(err,quote(msg)))
            if u.path=='/api-test':
                try:
                    n,total=test_shopping_api(); msg=f'API 연결 성공: 첫 페이지 {n:,}건 / 전체 {total:,}건 응답'; err=0
                except Exception as e: msg=f'API 연결 실패: {e}'; err=1
                return self.redirect('/settings?error=%d&msg=%s'%(err,quote(msg)))
            if u.path=='/backfill':
                ok=start_backfill_thread(); msg='최근 3년 구축을 백그라운드에서 시작했습니다.' if ok else '이미 3년 구축이 실행 중입니다.'
                return self.redirect('/settings?msg='+quote(msg))
            if u.path=='/clear-samples':
                n=clear_samples(); return self.redirect('/settings?msg='+quote(f'샘플 데이터 {n:,}건을 삭제했습니다.'))
            if u.path=='/reset-shopping-data':
                if (form.get('confirmation') or [''])[0] != '쇼핑몰 실데이터 초기화':
                    return self.redirect('/settings?error=1&msg='+quote('확인문구가 일치하지 않아 삭제하지 않았습니다.'))
                with connect() as conn: n=conn.execute('DELETE FROM shopping_contracts WHERE is_sample=0').rowcount
                return self.redirect('/settings?msg='+quote(f'쇼핑몰 실데이터 {n:,}건을 초기화했습니다. 다른 설정과 데이터는 유지했습니다.'))
            return self.send_bytes('Not Found','text/plain; charset=utf-8',404)
        except Exception as e:
            traceback.print_exc(); return self.send_bytes(f'<pre>{esc(e)}</pre>',status=500)


def main(open_browser=True):
    if PUBLIC_MODE:
        if not os.getenv('DASHBOARD_SECRET') or os.getenv('DASHBOARD_SECRET','').startswith('여기에_'):
            raise RuntimeError('인터넷 공개 모드에서는 DASHBOARD_SECRET을 반드시 설정해야 합니다. scripts/make_secret.py로 생성하세요.')
    init_db()
    if get_setting('backfill_status') == '실행중':
        set_setting('backfill_status','중단됨')
        set_setting('backfill_message','이전 실행이 서버 재시작으로 중단되었습니다. 다시 시작하면 저장된 월부터 재개합니다.')
    seeded = 0
    start_scheduler()
    print(f'LIGHTING SKETCH G2B DATA VIEW v{APP_VERSION} - http://{HOST}:{PORT}/dashboard')
    print(f'Authentication: DATABASE USERS / Public mode: {PUBLIC_MODE}')
    if seeded: print(f'샘플 조달 데이터 {seeded:,}건을 초기 등록했습니다.')
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    should_open = open_browser and HOST in ('127.0.0.1','localhost') and os.getenv('G2B_OPEN_BROWSER','1').lower() in ('1','true','yes','on')
    if should_open:
        threading.Timer(0.8,lambda:webbrowser.open(f'http://127.0.0.1:{PORT}/dashboard')).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
