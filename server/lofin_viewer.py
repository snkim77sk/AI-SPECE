"""Loopback-only budget search. Read-only SQLite; no API keys or source calls."""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
import urllib.parse
import webbrowser
from contextlib import closing
from datetime import date
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from lofin_analysis import amount
from budget_projection_vnext import project_payload

STATE=Path('D:/G2B/lofin-collection')
IDENTITY='G2B_LOCAL_BUDGET_VIEWER_V2'


def read_rows(path,dataset='budget',query='',offset=0,limit=100,snapshot='latest'):
    if dataset not in ('budget','budget_appropriation') or not 0<=offset<=5000000 or not 1<=limit<=100 or len(query)>200:
        raise ValueError('INVALID_QUERY')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        deadline=time.monotonic()+3
        conn.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        if snapshot=='latest':
            snapshot=conn.execute('SELECT MAX(source_date) FROM raw_records WHERE dataset=?',(dataset,)).fetchone()[0]
        elif dataset=='budget':
            if date.fromisoformat(snapshot).year!=2026: raise ValueError('INVALID_SNAPSHOT')
        elif snapshot!='2026': raise ValueError('INVALID_FISCAL_YEAR')
        if snapshot is None: return {'rows':[],'total':0,'offset':offset,'limit':limit,'snapshot':None,'has_more':False}
        where='dataset=? AND source_date=?';params=[dataset,snapshot]
        if query:
            escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            where+=" AND payload_json LIKE ? ESCAPE '\\'";params.append('%'+escaped+'%')
        total=conn.execute('SELECT COUNT(*) FROM raw_records WHERE '+where,params).fetchone()[0]
        rows=[]
        for raw in conn.execute('SELECT source_key,source_date,payload_json,payload_sha256 FROM raw_records WHERE '+where+' ORDER BY id LIMIT ? OFFSET ?',params+[limit,offset]):
            if hashlib.sha256(raw['payload_json'].encode('utf-8')).hexdigest()!=raw['payload_sha256']:
                raise ValueError('RAW_INTEGRITY_ERROR')
            payload=json.loads(raw['payload_json']);fact=project_payload(dataset,payload,source_date=raw['source_date'])
            budget=amount(payload.get('bdg_cash_amt' if dataset=='budget' else 'biz_bdg_tott_amt'))
            executed=amount(payload.get('ep_amt')) if dataset=='budget' else None
            rows.append({'organization':fact['org_name'],'project':fact['project_name'],'account':fact['account_name'],
                'budget':str(budget) if budget is not None else None,'executed':str(executed) if executed is not None else None,
                'remaining':str(budget-executed) if budget is not None and executed is not None else None,
                'administration':payload.get('padm_oper_exps_tott_amt'),'financial_activity':payload.get('fin_acv_tott_amt'),
                'raw':payload,'source_key':raw['source_key']})
        return {'dataset':dataset,'snapshot':snapshot,'rows':rows,'total':total,'offset':offset,'limit':limit,
                'has_more':offset+len(rows)<total,'read_only':True,'source_api_calls':0,'unit_verified':False}


def overview(root=STATE):
    def status(path):
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            return {k:value.get(k) for k in ('state','attempted','actual_attempts','verified_rows','local_reserved_today',
                'local_calls_today','remaining_to_configured_budget','official_remaining','started_at_kst','finished_at_kst','stop_reason','safe_reason')}
        except (OSError,ValueError): return {'state':'대기'}
    result={'identity':IDENTITY,'read_only':True,'source_api_calls':0,
            'g2b':status(Path('D:/G2B/live-bounded-20261114/automatic-status.json')),
            'lofin':status(root/'lofin-status.json'),'scopes':[]}
    path=root/'budget-2026.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('PRAGMA query_only=ON')
            result['scopes']=[dict(r) for r in conn.execute("SELECT dataset,scope_key,page_no,saved_count,source_total,status FROM collection_checkpoints ORDER BY dataset,scope_key DESC LIMIT 400")]
    return result


PAGE='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>G2B 예산 분석</title>
<style>body{font-family:system-ui,"Malgun Gothic",sans-serif;margin:0;background:#eef3f9;color:#18263d}main{max-width:1320px;margin:auto;padding:30px 22px}h1{font-size:30px;margin:8px 0 20px}h2{font-size:19px}.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card,section{background:white;border-radius:12px;padding:22px;margin-bottom:18px;box-shadow:0 2px 10px #243b530b}small,p{color:#56677e}p{line-height:1.6}strong{color:#1261aa}.toolbar{display:flex;gap:10px;flex-wrap:wrap}input,select,button{padding:11px;border:1px solid #bdcbdc;border-radius:7px;font-size:14px}input{flex:1;min-width:230px}button{background:#175ca3;color:white;cursor:pointer}button:disabled{opacity:.45}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:11px;border-bottom:1px solid #e4ebf3;white-space:nowrap}th{background:#edf3fa}td:nth-child(2){white-space:normal;min-width:240px}pre{white-space:pre-wrap;font-size:12px;background:#f3f6fb;padding:14px}.note{border-left:4px solid #cb8b1b;padding:12px 16px;background:#fff8e8;font-size:14px}.pager{display:flex;align-items:center;gap:15px;margin-top:18px}#error{color:#a52929}@media(max-width:700px){.cards{grid-template-columns:1fr}main{padding:18px 12px}}</style>
<main><small>사내 서버 · 저장 자료 읽기 전용</small><h1>G2B 예산 수집·분석</h1><div class="cards"><div class="card"><h2>나라장터</h2><strong id="gstate">확인 중</strong><p id="gcounts"></p></div><div class="card"><h2>지방재정365</h2><strong id="lstate">확인 중</strong><p id="lcounts"></p></div></div>
<p class="note">조회는 API를 호출하지 않습니다. 금액은 제공처 원문 값이며 단위 확인 전에는 원화 총예산으로 확정하지 않습니다. 구조별 예산과 세부사업 예산, 서로 다른 기준일의 금액은 합산하지 않습니다.</p>
<section><div class="toolbar"><select id="dataset"><option value="budget">세부사업 예산·집행</option><option value="budget_appropriation">구조별 세출예산</option></select><input id="query" maxlength="200" placeholder="기관·사업·분야 이름으로 검색"><button id="search">조회</button></div><p id="coverage"></p><p id="summary"></p><p id="error"></p><div class="table"><table><thead id="thead"></thead><tbody id="rows"></tbody></table></div><div class="pager"><button id="prev">이전 100건</button><span id="page"></span><button id="next">다음 100건</button></div></section>
<section><h2>선택한 행의 원본 항목</h2><p>표의 행을 누르면 제공처의 모든 항목을 확인할 수 있습니다.</p><pre id="raw">선택한 자료가 없습니다.</pre></section></main>
<script>
let offset=0,more=false,scopes=[];const el=id=>document.getElementById(id);const fmt=v=>v===null||v===undefined?'미확인':String(v);async function get(url){const r=await fetch(url,{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'조회 실패');return d}
async function status(){const d=await get('/api/overview');scopes=d.scopes;for(const [prefix,v]of [['g',d.g2b],['l',d.lofin]]){el(prefix+'state').textContent=fmt(v.state);el(prefix+'counts').textContent='이번 회차 '+fmt(v.attempted??v.actual_attempts??0)+'회 호출 · '+fmt(v.verified_rows??0)+'건 저장·재조회 | 오늘 예약 '+fmt(v.local_reserved_today??v.local_calls_today??0)+'회';}}
async function load(){el('error').textContent='';try{await status();const dataset=el('dataset').value;const p=new URLSearchParams({dataset,query:el('query').value,offset,limit:100});const d=await get('/api/rows?'+p);more=d.has_more;const detail=dataset==='budget';const headers=detail?['기관','사업','회계','예산현액','집행','잔액']:['기관','분야·부문','회계','정책사업예산','행정운영경비','재무활동'];const keys=detail?['organization','project','account','budget','executed','remaining']:['organization','project','account','budget','administration','financial_activity'];el('thead').replaceChildren();const tr=document.createElement('tr');for(const h of headers){const th=document.createElement('th');th.textContent=h;tr.append(th)}el('thead').append(tr);el('rows').replaceChildren();for(const row of d.rows){const tr=document.createElement('tr');for(const k of keys){const td=document.createElement('td');td.textContent=fmt(row[k]);tr.append(td)}tr.onclick=()=>el('raw').textContent=JSON.stringify(row.raw,null,2);el('rows').append(tr)}const cp=scopes.find(x=>x.dataset===dataset&&(detail?x.scope_key.endsWith(':'+d.snapshot):true));el('coverage').textContent='기준일 '+fmt(d.snapshot)+(cp?' | 저장 '+cp.saved_count.toLocaleString()+' / 제공처 '+cp.source_total.toLocaleString()+'건 | '+cp.status:'');el('summary').textContent='검색된 저장 자료 '+d.total.toLocaleString()+'건 · 미확인 금액은 0으로 처리하지 않습니다.';el('page').textContent=(d.rows.length?(offset+1)+'–'+(offset+d.rows.length):'0')+' / '+d.total.toLocaleString();el('prev').disabled=offset===0;el('next').disabled=!more;}catch(e){el('error').textContent=e.message}}
el('search').onclick=()=>{offset=0;load()};el('dataset').onchange=()=>{offset=0;load()};el('query').onkeydown=e=>{if(e.key==='Enter'){offset=0;load()}};el('prev').onclick=()=>{offset=Math.max(0,offset-100);load()};el('next').onclick=()=>{if(more){offset+=100;load()}};load();
</script></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def respond(self,code,data,content_type='application/json; charset=utf-8'):
        raw=data.encode('utf-8') if isinstance(data,str) else json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(code);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        host=self.headers.get('Host','').split(':')[0]
        if host not in ('127.0.0.1','localhost'): return self.respond(403,{'error':'LOCALHOST_ONLY'})
        target=urllib.parse.urlsplit(self.path)
        try:
            if target.path=='/': return self.respond(200,PAGE,'text/html; charset=utf-8')
            if target.path=='/api/overview': return self.respond(200,overview())
            if target.path=='/api/rows':
                p=urllib.parse.parse_qs(target.query,keep_blank_values=True)
                if set(p)-{'dataset','query','offset','limit','snapshot'} or any(len(v)!=1 for v in p.values()): raise ValueError('INVALID_QUERY')
                one=lambda k,default:p.get(k,[default])[0]
                return self.respond(200,read_rows(STATE/'budget-2026.sqlite3',one('dataset','budget'),one('query',''),
                    int(one('offset','0')),int(one('limit','100')),one('snapshot','latest')))
            self.respond(404,{'error':'NOT_FOUND'})
        except (ValueError,sqlite3.Error,OSError): self.respond(400,{'error':'조회할 수 없습니다. 검색 조건을 확인하거나 잠시 후 다시 조회하세요.'})
    def do_POST(self): self.respond(405,{'error':'READ_ONLY'})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--open-browser',action='store_true');args=parser.parse_args()
    url='http://127.0.0.1:8766/'
    try: server=ThreadingHTTPServer(('127.0.0.1',8766),Handler)
    except OSError:
        import urllib.request
        try:
            with urllib.request.urlopen(url+'api/overview',timeout=2) as response: existing=json.load(response)
            if existing.get('identity')!=IDENTITY: raise RuntimeError('VIEWER_PORT_OCCUPIED')
        except Exception: raise SystemExit('VIEWER_PORT_OCCUPIED') from None
        if args.open_browser: webbrowser.open(url)
        return
    if args.open_browser: webbrowser.open(url)
    server.serve_forever(poll_interval=0.5)


if __name__=='__main__': main()
