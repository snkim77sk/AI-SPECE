"""Independent bounded LOFIN pipeline using the original G2B budget collectors."""
from __future__ import annotations
import time
ENTRY=time.monotonic()
import argparse
import base64
import gc
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime,timedelta,timezone,date
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'lofin_candidate'
sys.path.insert(0,str(SOURCE))
STATE=Path('D:/G2B/lofin-collection')
KST=timezone(timedelta(hours=9))
MAINTENANCE_END=datetime(2026,11,15,0,0,tzinfo=KST)
DAILY_LIMIT=100
from process_lock import LocalDbProcessLock
from windows_limits import WindowsJob,process_memory
from lofin_receipts import readback,scope_for


class Stop(RuntimeError):
    pass


def now():
    return datetime.now(KST)


def ready(clock=None):
    clock=clock or now()
    if clock<MAINTENANCE_END:
        raise Stop('OFFICIAL_MAINTENANCE_WAIT')
    reset=(clock+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    if (reset-clock).total_seconds()<=90:
        raise Stop('MIDNIGHT_BOUNDARY_WAIT')
    return clock.date().isoformat(),reset.isoformat(timespec='seconds')


def atomic_json(path,data):
    temp=path.with_suffix(path.suffix+'.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
        stream.flush();os.fsync(stream.fileno())
    temp.replace(path)


def ledger_open(root):
    conn=sqlite3.connect(root/'lofin-calls.sqlite3',timeout=1)
    conn.row_factory=sqlite3.Row
    conn.executescript('''
      PRAGMA synchronous=FULL;
      CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,budget_day TEXT,state TEXT);
      CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,run_id TEXT,service TEXT,snapshot TEXT,
        page INTEGER,budget_day TEXT,state TEXT DEFAULT 'RESERVED',attempted INTEGER DEFAULT 0,
        response_received INTEGER DEFAULT 0,response_confirmed INTEGER DEFAULT 0,stored INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0,verified_rows INTEGER DEFAULT 0);
      CREATE UNIQUE INDEX IF NOT EXISTS one_sent_page ON calls(service,snapshot,page) WHERE state!='NOT_SENT';
      CREATE TABLE IF NOT EXISTS jobs(service TEXT,snapshot TEXT,state TEXT DEFAULT 'READY',next_page INTEGER DEFAULT 1,
        saved INTEGER DEFAULT 0,PRIMARY KEY(service,snapshot));
      CREATE TABLE IF NOT EXISTS restart_events(id INTEGER PRIMARY KEY,call_id INTEGER,before_json TEXT,outcome TEXT);
    ''')
    from delayed_retry import ensure_schema
    ensure_schema(conn, 'lofin')
    return conn


def sync_jobs(conn,clock=None):
    latest=min((clock or now()).date()-timedelta(days=1),date(2026,12,31))
    if latest<date(2026,1,1):
        raise Stop('INVALID_COLLECTION_RANGE')
    with conn:
        conn.execute("INSERT OR IGNORE INTO jobs(service,snapshot) VALUES('AIDFA','2026')")
        for offset in range((latest-date(2026,1,1)).days+1):
            conn.execute("INSERT OR IGNORE INTO jobs(service,snapshot) VALUES('QWGJK',?)",((latest-timedelta(days=offset)).isoformat(),))


def unresolved(conn,service=None,snapshot=None):
    from delayed_retry import validate_archives
    validate_archives(conn)
    sql="SELECT 1 FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RETRY_SUPERSEDED')"
    params=()
    if service is not None:
        sql+=' AND service=? AND snapshot=?';params=(service,snapshot)
    return conn.execute(sql+' LIMIT 1',params).fetchone() is not None


def isolate_held_scopes(conn):
    rows=conn.execute("SELECT DISTINCT service,snapshot FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RETRY_SUPERSEDED')").fetchall()
    with conn:
        conn.executemany("UPDATE jobs SET state='HOLD' WHERE service=? AND snapshot=?",[tuple(row) for row in rows])
    return [dict(row) for row in rows]


def record_job(conn,service,snapshot,receipt):
    conn.execute('UPDATE jobs SET state=?,next_page=?,saved=? WHERE service=? AND snapshot=?',
                 ('COMPLETE' if receipt['complete'] else 'READY',receipt['next_page'],receipt['saved'],service,snapshot))


def next_job(conn):
    # Finish a started snapshot before introducing the next day's 478k+ rows.
    # This prevents incomplete snapshots from being abandoned every midnight.
    return conn.execute("""SELECT * FROM jobs WHERE state='READY' ORDER BY
        CASE WHEN NOT EXISTS(SELECT 1 FROM calls WHERE calls.service=jobs.service AND verified=1) THEN 0 ELSE 1 END,
        CASE service WHEN 'AIDFA' THEN 0 ELSE 1 END,
        CASE WHEN next_page>1 THEN 0 ELSE 1 END,snapshot DESC LIMIT 1""").fetchone()


def reconcile(conn,path):
    outcomes=[]
    for row in conn.execute("SELECT * FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RETRY_SUPERSEDED') ORDER BY id").fetchall():
        call=dict(row);outcome='HOLD_UNCERTAIN_NO_RETRY'
        try:
            if path.exists():
                # Opening the schema lets SQLite recover a hot journal. A full
                # quick_check scans every unrelated payload and can consume the
                # entire 90-second run; the scoped receipt/RAW checks below
                # establish whether this exact page can safely resume.
                with closing(sqlite3.connect(path,timeout=1)) as check:
                    check.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
            receipt=readback(path,call['service'],call['snapshot'])
            flags=tuple(call[k] for k in ('attempted','response_received','response_confirmed','stored','verified'))
            with conn:
                if (call['state'] in ('RESERVED','UNCERTAIN') and flags==(0,0,0,0,0) and
                    ((receipt is None and call['page']==1) or
                     (receipt and receipt['verified'] and receipt['status'] in ('RUNNING','FAILED') and receipt['next_page']==call['page']))):
                    if receipt and receipt['status']=='FAILED':
                        # Attempt is committed BEFORE transport. All-zero flags prove
                        # no request was sent. Restore only this verified checkpoint.
                        dataset,scope=scope_for(call['service'],call['snapshot'])
                        with closing(sqlite3.connect(path,timeout=1)) as local:
                            before=local.execute('SELECT status,last_error FROM collection_checkpoints WHERE dataset=? AND scope_key=?',(dataset,scope)).fetchone()
                            call['checkpoint_before']={'status':before[0],'last_error':before[1]}
                            with local:
                                changed=local.execute("UPDATE collection_checkpoints SET status='RUNNING',last_error='' WHERE dataset=? AND scope_key=? AND page_no=? AND saved_count=? AND status='FAILED'",
                                    (dataset,scope,call['page'],receipt['saved'])).rowcount
                                if changed!=1: raise ValueError('UNSENT_CHECKPOINT_CHANGED')
                    conn.execute("UPDATE calls SET state='NOT_SENT' WHERE id=?",(call['id'],))
                    if receipt: record_job(conn,call['service'],call['snapshot'],receipt)
                    outcome='NOT_SENT'
                elif (flags[:3]==(1,1,1) and receipt and receipt['verified']
                      and receipt['status'] in ('RUNNING','COMPLETE') and receipt['next_page']==call['page']+1):
                    conn.execute("UPDATE calls SET state='VERIFIED',stored=1,verified=1,verified_rows=? WHERE id=?",(receipt['page_rows'],call['id']))
                    record_job(conn,call['service'],call['snapshot'],receipt)
                    outcome='VERIFIED_FROM_COMMIT'
        except (ValueError,sqlite3.Error,OSError):
            outcome='HOLD_LOCAL_VALIDATION_FAILED'
        with conn:
            conn.execute('INSERT INTO restart_events(call_id,before_json,outcome) VALUES(?,?,?)',(call['id'],json.dumps(call),outcome))
        outcomes.append({'call_id':call['id'],'outcome':outcome})
    return outcomes


def reserve(conn,plan,service,snapshot,page,retry_of=None):
    budget_day,_=ready()
    if budget_day!=plan['budget_day']:
        raise Stop('KST_DAY_CHANGED')
    conn.execute('BEGIN IMMEDIATE')
    try:
        if retry_of is None and unresolved(conn,service,snapshot):
            raise Stop('UNCERTAIN_NO_RETRY')
        count=conn.execute("SELECT COUNT(*) FROM calls WHERE budget_day=? AND state!='NOT_SENT'",(budget_day,)).fetchone()[0]
        run_count=conn.execute('SELECT COUNT(*) FROM calls WHERE run_id=?',(plan['run_id'],)).fetchone()[0]
        if count>=DAILY_LIMIT or run_count>=plan['max_pages']:
            raise Stop('LOCAL_QUOTA_WAIT')
        if retry_of is not None:
            import delayed_retry
            if not delayed_retry.enabled(plan): raise Stop('DELAYED_RETRY_POLICY_REQUIRED')
            call=delayed_retry.reserve_replacement(conn,retry_of,run_id=plan['run_id'],budget_day=budget_day,
                fields={'service':service,'snapshot':snapshot,'page':page},clock=now())
        else:
            call=conn.execute('INSERT INTO calls(run_id,service,snapshot,page,budget_day) VALUES(?,?,?,?,?)',
                              (plan['run_id'],service,snapshot,page,budget_day)).lastrowid
        conn.commit();return call
    except BaseException:
        conn.rollback();raise


def phase(conn,call,event):
    if event not in ('attempted','response_received','response_confirmed','stored'):
        raise Stop('INVALID_PHASE')
    with conn:
        conn.execute(f'UPDATE calls SET {event}=1 WHERE id=?',(call,))


def load_key():
    helper=Path('D:/LOFIN365/secure-setup-20261113/program/secure_key_reentry_20261113.py')
    path=Path('D:/LOFIN365/secure-setup-20261113/credentials/lofin365-current-user-reentry-20261113.dpapi.json')
    spec=importlib.util.spec_from_file_location('existing_lofin_dpapi',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('format')!='windows-dpapi-current-user-v1':
        raise Stop('CREDENTIAL_FORMAT_INVALID')
    raw=module.dpapi(base64.b64decode(data['ciphertext'],validate=True),False)
    try:
        value=raw.decode('ascii').strip()
        if not value or any(ord(c)<32 or ord(c)==127 for c in value):
            raise Stop('CREDENTIAL_INVALID')
        return value
    finally:
        raw=None


def safe_error(exc):
    value=str(exc)
    if type(exc) is Stop:
        return value
    if re.fullmatch(r'HTTP_[0-9]{3}',value):
        return value
    if re.fullmatch(r'API: [A-Z0-9_-]{1,32}',value):
        return value.replace(': ','_')
    return type(exc).__name__


def worker(run_dir):
    if sys.stdin.buffer.readline()!=b'GO\n':
        raise Stop('PARENT_HANDSHAKE_REQUIRED')
    plan=json.loads((run_dir/'plan.json').read_text(encoding='utf-8'))
    ready()
    root=run_dir.parent;path=root/'budget-2026.sqlite3'
    import db
    import budget_vnext
    import budget_appropriation_vnext
    import lofin_vnext_http as http
    import urllib.request,urllib.error
    from vnext_store import ensure_foundation
    from vnext_source_guard import lofin_bounded_source_context
    db.DB_PATH=str(path)
    os.environ.update(G2B_RUNTIME_ROLE='LOCAL_COLLECTOR',G2B_AUTO_SYNC='0',G2B_DB_PATH=str(path),
                      G2B_SQLITE_TIMEOUT='1',LOFIN_VNEXT_API_DAILY_LIMIT=str(DAILY_LIMIT))
    os.environ.pop('G2B_SERVICE_KEY',None)
    conn=ledger_open(root);call=None;reason='PAGE_LIMIT';memory=[]
    real_request=http._request
    old_open=urllib.request.urlopen
    deadline=plan['started_monotonic']+80
    import delayed_retry
    retried=False
    retry=None
    try:
        os.environ['LOFIN_API_KEY']=load_key()
        db.init_db();ensure_foundation()
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,request,fp,code,message,headers,new_url):
                raise urllib.error.HTTPError('https://www.lofin365.go.kr/lf/hub/',code,'SOURCE_REDIRECT_REFUSED',headers,None)
        urllib.request.urlopen=urllib.request.build_opener(NoRedirect()).open
        with LocalDbProcessLock(path):
            for _ in range(plan['max_pages']):
                ready()
                if time.monotonic()>deadline-25:
                    reason='DEADLINE_MARGIN';break
                retry=delayed_retry.next_due(conn,now()) if delayed_retry.enabled(plan) and not retried else None
                selected=retry or next_job(conn)
                if selected is None:
                    reason='NO_READY_SCOPES';break
                service,snapshot=selected['service'],selected['snapshot']
                previous=readback(path,service,snapshot)
                if retry and not delayed_retry.checkpoint_matches(previous,retry['page']):
                    raise Stop('RETRY_CHECKPOINT_REQUIRES_REVIEW')
                if previous:
                    if previous['complete']:
                        with conn: record_job(conn,service,snapshot,previous)
                        continue
                    if previous['status']!='RUNNING' and not retry:
                        raise Stop('CHECKPOINT_REQUIRES_REVIEW')
                page=previous['next_page'] if previous else 1
                call=reserve(conn,plan,service,snapshot,page,retry_of=retry['id'] if retry else None)
                if retry:
                    retried=True
                    dataset,scope=scope_for(service,snapshot)
                    delayed_retry.restore_failed_checkpoint(conn,call,path,dataset,scope,previous)
                request_count=0
                def request_once(params,retries=1,timeout=45,*,service_code=http.SERVICE_CODE):
                    nonlocal request_count
                    request_count+=1
                    if request_count!=1 or time.monotonic()>deadline-25:
                        raise Stop('NO_RETRY_OR_LATE_CALL')
                    return real_request(params,retries=1,timeout=10,service_code=service_code,
                                        observer=lambda event:phase(conn,call,event))
                http._request=request_once
                with lofin_bounded_source_context(service=service,snapshot=snapshot,page=page):
                    result=(budget_appropriation_vnext.collect_full_appropriation(2026,page_size=999,max_pages=1,resume=True)
                            if service=='AIDFA' else budget_vnext.collect_full_budget(2026,snapshot,page_size=999,max_pages=1,resume=True))
                if result['status'] not in ('RUNNING','COMPLETE') or result.get('reason'):
                    raise Stop('SOURCE_PAGE_REJECTED')
                phase(conn,call,'stored')
                receipt=readback(path,service,snapshot,expected_page=page)
                with conn:
                    stages=conn.execute('SELECT attempted,response_received,response_confirmed,stored FROM calls WHERE id=?',(call,)).fetchone()
                    if not all(stages) or not receipt or not receipt['verified']:
                        raise Stop('PAGE_CONFIRMATION_MISSING')
                    conn.execute("UPDATE calls SET state='VERIFIED',verified=1,verified_rows=? WHERE id=?",(receipt['page_rows'],call))
                    record_job(conn,service,snapshot,receipt)
                call=None
                with (run_dir/'pages.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps({'service':service,'snapshot':snapshot,'page':page,**receipt})+'\n')
                    stream.flush();os.fsync(stream.fileno())
                del result,receipt,previous
                gc.collect();memory.append(process_memory()|{'service':service,'snapshot':snapshot,'page':page})
    except Exception as exc:
        reason=safe_error(exc)
        if retry and call is None:
            delayed_retry.defer_local_validation(conn,retry['id'],now())
        if call is not None:
            with conn:
                conn.execute("UPDATE calls SET state='UNCERTAIN' WHERE id=?",(call,))
                conn.execute("UPDATE jobs SET state='HOLD' WHERE service=? AND snapshot=?",(service,snapshot))
            if delayed_retry.enabled(plan):
                delayed_retry.enqueue(conn,call,reason=reason,clock=now())
            if reason=='NO_RETRY_OR_LATE_CALL':
                with LocalDbProcessLock(path):
                    reconcile(conn,path)
                if conn.execute('SELECT state FROM calls WHERE id=?',(call,)).fetchone()[0]=='NOT_SENT':
                    reason='DEADLINE_MARGIN'
    finally:
        http._request=real_request;urllib.request.urlopen=old_open
        os.environ.pop('LOFIN_API_KEY',None);conn.close()
        atomic_json(run_dir/'worker-result.json',{'stop_reason':reason,'memory_samples':memory})


def main(run_id,max_pages=1,scheduled=False):
    budget_day,reset=ready()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id) or not 1<=max_pages<=8:
        raise Stop('INVALID_RUN_PARAMETERS')
    STATE.mkdir(exist_ok=True)
    with LocalDbProcessLock(STATE/'lofin-calls.sqlite3'):
        run_dir=STATE/run_id
        run_dir.mkdir()
        plan={'run_id':run_id,'budget_day':budget_day,'max_pages':max_pages,'started_monotonic':ENTRY,
              'started_at_kst':now().isoformat(timespec='seconds'),'range_start':'2026-01-01',
              'daily_limit':DAILY_LIMIT,'scheduled_entry':scheduled,'source':'LOFIN','max_seconds':90}
        if scheduled:
            import delayed_retry
            plan['delayed_retry']=delayed_retry.POLICY
        manifest=ROOT/'runtime-manifest.json'
        plan['runtime_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else None
        atomic_json(run_dir/'plan.json',plan)
        conn=ledger_open(STATE)
        try:
            sync_jobs(conn)
            with LocalDbProcessLock(STATE/'budget-2026.sqlite3'):
                recovery=reconcile(conn,STATE/'budget-2026.sqlite3')
            if scheduled:
                delayed_retry.arm_pending(conn,STATE,clock=now())
            atomic_json(run_dir/'restart-recovery.json',{'network_calls':0,'outcomes':recovery})
            isolate_held_scopes(conn)
            if conn.execute("SELECT COUNT(*) FROM calls WHERE budget_day=? AND state!='NOT_SENT'",(budget_day,)).fetchone()[0]>=DAILY_LIMIT:
                raise Stop('LOCAL_QUOTA_WAIT')
            with conn: conn.execute("INSERT INTO runs VALUES(?,?,'RUNNING')",(run_id,budget_day))
        finally:
            conn.close()
        env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('TOKEN','SECRET','SERVICE_KEY','API_KEY','PROVENANCE','SOURCE_COMMIT_SHA','GITHUB_SHA'))}
        env['PYTHONDONTWRITEBYTECODE']='1'
        process=None;job=None;reason='FINISHED';peak=0
        with (run_dir/'worker.log').open('w',encoding='utf-8') as log:
            try:
                process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',str(run_dir)],
                    stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,env=env,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
                job=WindowsJob(process,256)
                process.stdin.write(b'GO\n');process.stdin.flush();process.stdin.close()
                while process.poll() is None:
                    if time.monotonic()-ENTRY>=85:
                        reason='TIMEOUT_NO_RETRY';job.close();break
                    try: peak=max(peak,process_memory(int(process._handle))['private_bytes'])
                    except OSError: pass
                    time.sleep(0.05)
                process.wait(timeout=3)
            except Exception as exc:
                reason='SUPERVISOR_'+type(exc).__name__
            finally:
                if job: job.close()
                if process and process.poll() is None: process.kill();process.wait(timeout=1)
        with closing(sqlite3.connect((STATE/'lofin-calls.sqlite3').as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            counts=dict(conn.execute('''SELECT COUNT(*) reserved,COALESCE(SUM(attempted),0) attempted,
                COALESCE(SUM(response_received),0) response_received,COALESCE(SUM(response_confirmed),0) response_confirmed,
                COALESCE(SUM(stored),0) stored_pages,COALESCE(SUM(verified),0) verified_pages,COALESCE(SUM(verified_rows),0) verified_rows,
                COALESCE(SUM(state='NOT_SENT'),0) not_sent
                FROM calls WHERE run_id=?''',(run_id,)).fetchone())
            used=conn.execute("SELECT COUNT(*) FROM calls WHERE budget_day=? AND state!='NOT_SENT'",(budget_day,)).fetchone()[0]
            retry_attempts=conn.execute('SELECT COALESCE(SUM(c.attempted),0) FROM delayed_retries r JOIN calls c ON c.id=r.replacement_id WHERE c.run_id=?',(run_id,)).fetchone()[0]
            held_scopes=[dict(row) for row in conn.execute("SELECT DISTINCT service,snapshot FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RETRY_SUPERSEDED')")]
            touched=list(conn.execute('SELECT DISTINCT service,snapshot FROM calls WHERE run_id=?',(run_id,)))
        result={**plan,**counts,'supervisor_outcome':reason,'worker_exit_code':process.returncode if process else None,
                'elapsed_seconds':round(time.monotonic()-ENTRY,3),'peak_private_bytes':peak,'memory_limit_mb':256,
                'local_reserved_today':used,'remaining_to_configured_budget':max(0,DAILY_LIMIT-used),
                'official_remaining':None,'quota_reset_at_kst':reset,'source_retry_performed':bool(retry_attempts),
                'delayed_retry_attempts':retry_attempts,'readbacks':{}}
        if (run_dir/'worker-result.json').exists():
            result.update(json.loads((run_dir/'worker-result.json').read_text(encoding='utf-8')))
        for service,snapshot in touched:
            try: result['readbacks'][service+':'+snapshot]=readback(STATE/'budget-2026.sqlite3',service,snapshot)
            except Exception as exc: result['readbacks'][service+':'+snapshot]={'verified':False,'error':type(exc).__name__}
        normal=result.get('stop_reason') in ('PAGE_LIMIT','DEADLINE_MARGIN','NO_READY_SCOPES','LOCAL_QUOTA_WAIT')
        ok=(reason=='FINISHED' and result['worker_exit_code']==0 and normal and time.monotonic()-ENTRY<90
            and counts['reserved']==counts['verified_pages']+counts['not_sent'] and all(r and r.get('verified') for r in result['readbacks'].values()))
        result.update(state='VERIFIED' if ok else 'CONTINUING_WITH_SCOPE_HOLD' if held_scopes else 'ERROR_RECHECK_NEXT_SCHEDULE',
            held_scopes=held_scopes,other_scopes_continue=True,finished_at_kst=now().isoformat(timespec='seconds'))
        with closing(sqlite3.connect(STATE/'lofin-calls.sqlite3',timeout=1)) as conn:
            with conn: conn.execute('UPDATE runs SET state=? WHERE id=?',(result['state'],run_id))
        atomic_json(run_dir/'result.json',result)
        atomic_json(STATE/'lofin-status.json',result|{'result_path':str(run_dir/'result.json'),'pid':os.getpid(),'parent_pid':os.getppid()})
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--execute-once',action='store_true')
    parser.add_argument('--run-id')
    parser.add_argument('--max-pages',type=int,default=1)
    args=parser.parse_args()
    try:
        if args.worker: worker(args.worker.resolve())
        elif args.execute_once: main(args.run_id,args.max_pages)
        else: parser.error('Explicit --execute-once is required')
    except Exception as exc:
        print(json.dumps({'state':'STOPPED','safe_reason':safe_error(exc),'automatic_retry':False}))
        raise SystemExit(1)
