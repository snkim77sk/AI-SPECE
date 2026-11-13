"""G2B result-only viewer. No collector, source keys, scheduler or legacy DB access."""
from __future__ import annotations
import datetime as dt
import gzip
import hashlib
import hmac
import html
import io
import json
import os
from pathlib import Path
import sqlite3
import zlib
from urllib.parse import parse_qs, urlencode

VERSION = '5.0.0'
MAX_BODY = 4 * 1024 * 1024
MAX_JSON = 12 * 1024 * 1024
MAX_ROWS = 500_000
SECTIONS = {'shopping','vendors','service','budget_targets','budget_prebid','budget_pipelines'}
DB_NAME = 'g2b-result-view.sqlite3'

class Invalid(ValueError):
    pass

def db_path():
    # Dedicated NEW filename. Never accept a legacy DB path/DSN from environment.
    return Path(os.environ.get('G2B_RESULT_DATA_DIR','/app/user_data/g2b-result-view')) / DB_NAME

def connect(write=False):
    p=db_path()
    if write:
        p.parent.mkdir(parents=True,exist_ok=True)
        c=sqlite3.connect(p,timeout=10)
        # Fail closed if the target is not this app's DB. Do not mutate another schema.
        names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names and names != {'viewer_meta','viewer_rows'}:
            c.close(); raise Invalid('UNEXPECTED_DATABASE_SCHEMA')
        c.execute('CREATE TABLE IF NOT EXISTS viewer_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS viewer_rows (section TEXT,ordinal INTEGER,category TEXT,source_date TEXT,search TEXT,payload TEXT,PRIMARY KEY(section,ordinal))')
        c.execute('CREATE INDEX IF NOT EXISTS viewer_filter ON viewer_rows(section,category,source_date)')
        c.commit()
    else:
        if not p.is_file(): return None
        c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    c.row_factory=sqlite3.Row
    return c

def canonical(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def validate(p):
    if not isinstance(p,dict) or type(p.get('schema_version')) is not int or p['schema_version']!=1: raise Invalid('SCHEMA_VERSION')
    sections=p.get('sections')
    if not isinstance(sections,dict) or not sections: raise Invalid('SECTIONS_REQUIRED')
    if len(sections)>40: raise Invalid('TOO_MANY_SECTIONS')
    count=0
    for section,rows in sections.items():
        if section not in SECTIONS and not (isinstance(section,str) and section.startswith('vendors:') and len(section)<80): raise Invalid('UNKNOWN_SECTION')
        if not isinstance(rows,list): raise Invalid('SECTION_NOT_LIST')
        count+=len(rows)
        if count>MAX_ROWS: raise Invalid('ROW_LIMIT')
        for row in rows:
            if not isinstance(row,dict): raise Invalid('ROW_NOT_OBJECT')
            if len(canonical(row))>100000: raise Invalid('ROW_TOO_LARGE')
    status=p.get('collection_status',{})
    if not isinstance(status,dict): raise Invalid('STATUS_NOT_OBJECT')
    stages=status.get('stages',[])
    if not isinstance(stages,list) or any(not isinstance(v,dict) for v in stages): raise Invalid('STAGES_NOT_OBJECT_LIST')
    stamp=p.get('generated_at_utc')
    try:
        parsed=dt.datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if parsed.tzinfo is None: raise ValueError()
    except (AttributeError,TypeError,ValueError): raise Invalid('TIMESTAMP_REQUIRED') from None
    if parsed > dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5): raise Invalid('FUTURE_SNAPSHOT')
    for key in ('collection_status','readiness','source_counts'):
        if not isinstance(p.get(key,{}),dict): raise Invalid('METADATA_NOT_OBJECT')
    if not isinstance(p.get('snapshot_id'),str) or not 1<=len(p['snapshot_id'])<=128: raise Invalid('SNAPSHOT_ID_REQUIRED')
    return parsed

def decode(body,encoding):
    if len(body)>MAX_BODY: raise Invalid('COMPRESSED_LIMIT')
    if encoding=='gzip':
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as f: raw=f.read(MAX_JSON+1)
        except (OSError,EOFError,zlib.error): raise Invalid('INVALID_GZIP') from None
    elif encoding in ('','identity'): raw=body
    else: raise Invalid('UNSUPPORTED_ENCODING')
    if len(raw)>MAX_JSON: raise Invalid('JSON_LIMIT')
    try: return json.loads(raw.decode('utf-8'),parse_constant=lambda _: (_ for _ in ()).throw(Invalid('NONFINITE_JSON')))
    except (UnicodeError,ValueError,RecursionError): raise Invalid('INVALID_JSON') from None

def install(p):
    stamp=validate(p)
    digest=hashlib.sha256(canonical(p).encode()).hexdigest()
    c=connect(True)
    try:
        c.execute('BEGIN IMMEDIATE')
        old={r['key']:r['value'] for r in c.execute('SELECT * FROM viewer_meta')}
        if old.get('digest')==digest:
            c.rollback(); return {'ok':True,'unchanged':True,'snapshot_id':p['snapshot_id']}
        if old.get('snapshot_id')==p['snapshot_id']: raise Invalid('SNAPSHOT_ID_CONFLICT')
        if old.get('generated_at_utc') and stamp<=dt.datetime.fromisoformat(old['generated_at_utc']): raise Invalid('STALE_SNAPSHOT')
        # Replaces ONLY this app's derived projection, within a single transaction.
        c.execute('DELETE FROM viewer_rows')
        for section,rows in p['sections'].items():
            for i,row in enumerate(rows):
                text=' '.join(str(v) for v in row.values() if isinstance(v,(str,int,float)))
                c.execute('INSERT INTO viewer_rows VALUES(?,?,?,?,?,?)',(section,i,str(row.get('primary_category','')),str(row.get('source_date','')),text[:12000].casefold(),canonical(row)))
        meta={k:p.get(k,{}) for k in ('collection_status','readiness','source_counts')}
        # Stored operational truth stays intact; viewer never enables source controls.
        meta.update(snapshot_id=p['snapshot_id'],generated_at_utc=stamp.isoformat(),source_version=str(p.get('source_version','')),row_counts={k:len(v) for k,v in p['sections'].items()},received_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        c.execute('DELETE FROM viewer_meta')
        for k,v in {'digest':digest,'snapshot_id':p['snapshot_id'],'generated_at_utc':stamp.isoformat(),'manifest':canonical(meta)}.items(): c.execute('INSERT INTO viewer_meta VALUES(?,?)',(k,v))
        c.commit()
        return {'ok':True,'snapshot_id':p['snapshot_id'],'row_counts':meta['row_counts'],'total_rows':sum(meta['row_counts'].values())}
    except BaseException:
        c.rollback();raise
    finally:c.close()

def read_data(query):
    section=query.get('section',['shopping'])[0]
    if section not in ('shopping','vendors'): raise Invalid('INVALID_SECTION')
    q=query.get('q',[''])[0][:150].casefold()
    category=query.get('category',[''])[0][:40]
    try: page=max(1,min(10000,int(query.get('page',['1'])[0])))
    except ValueError: raise Invalid('INVALID_PAGE') from None
    c=connect()
    if c is None:return {'manifest':None,'rows':[],'count':0,'page':page,'section':section}
    try:
        c.execute('BEGIN')
        meta=c.execute("SELECT value FROM viewer_meta WHERE key='manifest'").fetchone()
        manifest=json.loads(meta[0]) if meta else None
        where='section=?'; args=[section]
        if q:where+=' AND instr(search,?)>0';args.append(q)
        if category:where+=' AND category=?';args.append(category)
        count=c.execute('SELECT COUNT(*) FROM viewer_rows WHERE '+where,args).fetchone()[0]
        rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM viewer_rows WHERE '+where+' ORDER BY source_date DESC,ordinal LIMIT 50 OFFSET ?',[*args,(page-1)*50])]
        return {'manifest':manifest,'rows':rows,'count':count,'page':page,'section':section,'collection_controls_enabled':False}
    finally:c.close()

def esc(v):return html.escape(str(v),quote=True)

def page_html(data,query):
    m=data['manifest'] or {}; counts=m.get('row_counts',{})
    stage=next((s for s in m.get('collection_status',{}).get('stages',[]) if s.get('dataset')=='shopping_delivery'),{})
    # Never equate a successful sync with completed source collection.
    state=str(stage.get('state') or 'UNKNOWN')
    if state=='COMPLETE':status='수집기 보고: 해당 범위 완료 (전체 원천 검증 아님)'
    elif not m:status='아직 동기화된 자료가 없습니다.'
    else:status='일부 자료 · 수집 완료 미확인 ('+state+')'
    rows=''
    for row in data['rows']:
        cells=[]
        for keys in (('source_date','fetched_at'),('item_name','product_name','prdct_nm','vendor_name'),('demand_org','demand_org_name','buyer_name','organization_name'),('vendor_name','vendor_bizno'),('amount','total_amount'),('primary_category',)):
            cells.append(next((row[k] for k in keys if row.get(k) not in (None,'')),''))
        details='<details><summary>상세 보기</summary><dl>'+''.join('<dt>'+esc(k)+'</dt><dd>'+esc(v)+'</dd>' for k,v in row.items() if v not in (None,''))+'</dl></details>'
        rows+='<tr>'+''.join('<td>'+esc(v)+'</td>' for v in cells)+'<td>'+details+'</td></tr>'
    if not rows:rows='<tr><td colspan="7">표시할 자료가 없습니다.</td></tr>'
    q=query.get('q',[''])[0][:150];cat=query.get('category',[''])[0][:40]
    def link(n):return '/?'+urlencode({'section':data['section'],'q':q,'category':cat,'page':n})
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>G2B 결과 조회</title><link rel="stylesheet" href="/style.css"><body><main>'
    '<header><span class="badge">G2B · 조회 전용</span><h1>나라장터 수집 결과</h1><p>수집과 원본 저장은 김수남 서버 D:에서 수행합니다. 이 사이트는 수집·수정·삭제하지 않습니다.</p></header>'
    '<section class="cards"><article><small>납품 조회</small><strong>'+esc(counts.get('shopping',0))+'</strong></article><article><small>업체</small><strong>'+esc(counts.get('vendors',0))+'</strong></article><article><small>원천 상태</small><b>'+esc(status)+'</b><p>'+esc(stage.get('saved_count','-'))+' / '+esc(stage.get('source_total','-'))+'</p></article></section>'
    '<p class="muted">자료 생성(UTC): '+esc(m.get('generated_at_utc','-'))+' · 마지막 동기화(UTC): '+esc(m.get('received_at_utc','-'))+'</p>'
    '<nav><a href="/?section=shopping">납품 자료</a><a href="/?section=vendors">업체 조회</a></nav>'
    '<form method="get"><input type="hidden" name="section" value="'+esc(data['section'])+'"><label>검색 <input name="q" value="'+esc(q)+'" placeholder="품명 · 기관 · 업체"></label><label>분류 <select name="category"><option value="">전체</option>'+''.join('<option value="'+c+'"'+(' selected' if cat==c else '')+'>'+label+'</option>' for c,label in [('LIGHTING','조명'),('POLE','등주'),('ELECTRICAL','전기'),('SOLAR','태양광')])+'</select></label><button>조회</button></form>'
    '<p>검색 결과 '+esc(data['count'])+'건 · '+esc(data['page'])+'페이지</p><div class="table"><table><thead><tr>'+''.join('<th>'+t+'</th>' for t in ['날짜','품명/업체','수요기관','납품업체','금액','분류','상세'])+'</tr></thead><tbody>'+rows+'</tbody></table></div><footer>'+('<a href="'+esc(link(data['page']-1))+'">이전</a>' if data['page']>1 else '')+(' <a href="'+esc(link(data['page']+1))+'">다음</a>' if data['page']*50<data['count'] else '')+'<p>G2B Result View '+VERSION+' · 자동 수집 기능 없음 · NO1과 별개</p></footer></main></body></html>').encode()

CSS=b'''*{box-sizing:border-box}body{margin:0;background:#f3f6f9;color:#182b3b;font:15px/1.6 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:30px 20px}h1{font-size:32px;margin:12px 0}.badge{background:#143a57;color:white;border-radius:20px;padding:5px 14px}.cards{display:grid;grid-template-columns:1fr 1fr 2fr;gap:14px;margin:24px 0}article{padding:18px;background:white;border:1px solid #d6e0e8;border-radius:12px}small{display:block;color:#516778}strong{font-size:32px}nav{display:flex;gap:24px}a{color:#005e90}form{display:flex;align-items:end;gap:15px;margin:20px 0;flex-wrap:wrap}label{display:grid}input,select,button{font:inherit;padding:10px;border:1px solid #a8bac9;border-radius:6px}button{background:#143a57;color:white}.table{overflow:auto;background:white;border:1px solid #d6e0e8;border-radius:8px}table{width:100%;border-collapse:collapse}th,td{padding:10px;text-align:left;border-bottom:1px solid #e0e7ee;vertical-align:top;min-width:100px}th{background:#e9eff4}td{max-width:280px;overflow-wrap:anywhere}dt{font-weight:bold}dd{margin:0 0 8px}details{min-width:170px}.muted,footer{color:#607180;font-size:13px}footer{margin-top:24px}@media(max-width:600px){main{padding:20px 12px}.cards{grid-template-columns:1fr 1fr}.cards article:last-child{grid-column:1/-1}h1{font-size:27px}input{width:100%}}'''

def application(env,start_response):
    method=env.get('REQUEST_METHOD','GET');path=env.get('PATH_INFO','/')
    code=200;headers=[];kind='application/json; charset=utf-8'
    try:
        if path in ('/live','/health','/__ai_space_health') and method in ('GET','HEAD'):
            body=canonical({'status':'ok','version':VERSION,'runtime_role':'RESULT_VIEW_ONLY','source_collection_enabled':False}).encode()
        elif path=='/api/result-sync' and method=='POST':
            token=os.environ.get('G2B_RESULT_SYNC_TOKEN','')
            if len(token)<32:code=503;body=b'{"error":"SYNC_NOT_CONFIGURED"}'
            elif not hmac.compare_digest(env.get('HTTP_AUTHORIZATION','').encode(),('Bearer '+token).encode()):code=401;body=b'{"error":"UNAUTHORIZED"}'
            else:
                length=env.get('CONTENT_LENGTH','')
                if not length.isdigit():raise Invalid('CONTENT_LENGTH_REQUIRED')
                length=int(length)
                if length>MAX_BODY:raise Invalid('COMPRESSED_LIMIT')
                data=env['wsgi.input'].read(length)
                if len(data)!=length:raise Invalid('TRUNCATED_BODY')
                body=canonical(install(decode(data,env.get('HTTP_CONTENT_ENCODING','')))).encode()
        elif method not in ('GET','HEAD'):
            code=405;body=b'{"error":"READ_ONLY"}'
        elif path not in ('/','/api/results','/style.css','/ready'):
            code=404;body=b'{"error":"NOT_FOUND"}'
        elif os.environ.get('G2B_PUBLIC_READ','0')!='1':
            # No newly invented admin/member passwords; no silent public exposure.
            code=503;body=b'{"error":"READ_ACCESS_NOT_ENABLED"}'
        elif path=='/style.css':body=CSS;kind='text/css; charset=utf-8'
        else:
            data=read_data(parse_qs(env.get('QUERY_STRING',''),max_num_fields=12))
            if path=='/ready':
                code=200 if data['manifest'] else 503
                body=canonical({'status':'ready' if code==200 else 'awaiting_snapshot','source_collection_enabled':False}).encode()
            elif path=='/api/results':body=canonical(data).encode()
            else:body=page_html(data,parse_qs(env.get('QUERY_STRING',''),max_num_fields=12));kind='text/html; charset=utf-8'
    except (Invalid,ValueError,RecursionError):code=400;body=b'{"error":"INVALID_REQUEST_OR_SNAPSHOT"}'
    except sqlite3.Error:code=503;body=b'{"error":"STORAGE_UNAVAILABLE"}'
    except OSError:code=503;body=b'{"error":"SERVICE_UNAVAILABLE"}'
    headers.extend([('Content-Type',kind),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")])
    reason={200:'OK',400:'Bad Request',401:'Unauthorized',404:'Not Found',405:'Method Not Allowed',503:'Service Unavailable'}[code]
    start_response(str(code)+' '+reason,headers)
    return [b'' if method=='HEAD' else body]
app=application
