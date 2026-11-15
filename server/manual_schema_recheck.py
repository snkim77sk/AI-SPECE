"""One explicitly authorized Nov 14/page 1 recheck; no automatic replay path."""
import time
ENTRY=time.monotonic()
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import ExitStack,closing
from pathlib import Path
import live_collection_once as live
from process_lock import LocalDbProcessLock
from windows_limits import WindowsJob,process_memory
from receipt_validation import readback
from response_evidence import summarize

ROOT=Path(__file__).resolve().parent
STATE=Path('D:/G2B/live-bounded-20261114')
DAY='2026-11-14'
RUN='manual-schema-recheck-20261115-01'
AUTH='USER_20261115_SCHEMA_RECHECK'


def reserve_recheck(conn,run_id,original_id,budget_day):
    original=dict(conn.execute('SELECT * FROM calls WHERE id=?',(original_id,)).fetchone())
    if (original['source_day']!=DAY or original['page']!=1 or original['state']!='UNCERTAIN'
        or tuple(original[k] for k in ('attempted','response_received','response_confirmed','stored','verified'))!=(1,1,0,0,0)):
        raise ValueError('EXACT_UNCONFIRMED_RESPONSE_REQUIRED')
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS manual_rechecks(
            original_id INTEGER PRIMARY KEY,recheck_id INTEGER UNIQUE NOT NULL,
            authorization TEXT NOT NULL,authorized_at_kst TEXT NOT NULL,
            original_json TEXT NOT NULL,state TEXT NOT NULL,readback_json TEXT NOT NULL DEFAULT '{}')''')
        if conn.execute('SELECT 1 FROM manual_rechecks WHERE original_id=?',(original_id,)).fetchone():
            raise ValueError('MANUAL_RECHECK_ALREADY_RESERVED')
        if conn.execute('SELECT COUNT(*) FROM calls WHERE budget_day=?',(budget_day,)).fetchone()[0]>=950:
            raise ValueError('DAILY_QUOTA_WAIT')
        conn.execute('DROP INDEX calls_one_sent_page')
        conn.execute("CREATE UNIQUE INDEX calls_one_sent_page ON calls(source_day,page) WHERE state NOT IN ('NOT_SENT','RECHECK_PENDING','RECHECK_SUPERSEDED')")
        conn.execute("UPDATE calls SET state='RECHECK_PENDING' WHERE id=?",(original_id,))
        conn.execute("INSERT INTO runs VALUES(?,?,'RUNNING')",(run_id,budget_day))
        cur=conn.execute('INSERT INTO calls(run_id,source_day,page,budget_day) VALUES(?,?,1,?)',(run_id,DAY,budget_day))
        conn.execute('INSERT INTO manual_rechecks(original_id,recheck_id,authorization,authorized_at_kst,original_json,state) VALUES(?,?,?,?,?,?)',
            (original_id,cur.lastrowid,AUTH,live.now().isoformat(),json.dumps(original,sort_keys=True),'PENDING'))
        conn.commit()
        return cur.lastrowid
    except BaseException:
        conn.rollback();raise


def finish_recheck(conn,call_id,receipt):
    with conn:
        row=conn.execute('SELECT * FROM calls WHERE id=?',(call_id,)).fetchone()
        if not all(row[k] for k in ('attempted','response_received','response_confirmed','stored')):
            raise ValueError('MISSING_RECHECK_PHASE')
        if not receipt['verified'] or receipt['next_page']!=2:
            raise ValueError('RECHECK_RECEIPT_INVALID')
        conn.execute("UPDATE calls SET state='VERIFIED',verified=1,verified_rows=? WHERE id=?",(receipt['page_rows'],call_id))
        conn.execute("UPDATE manual_rechecks SET state='VERIFIED',readback_json=? WHERE recheck_id=?",(json.dumps(receipt,sort_keys=True),call_id))
        conn.execute("UPDATE calls SET state='RECHECK_SUPERSEDED' WHERE id=(SELECT original_id FROM manual_rechecks WHERE recheck_id=?)",(call_id,))
        conn.execute("UPDATE runs SET state='FINISHED' WHERE id=?",(row['run_id'],))
    import restart_recovery
    restart_recovery.validate_manual_rechecks(conn)


def worker(run):
    if sys.stdin.buffer.readline()!=b'GO\n': raise ValueError('PARENT_REQUIRED')
    plan=json.loads((run/'plan.json').read_text(encoding='utf-8'))
    if plan['budget_day']!=live.now().date().isoformat() or plan['authorization']!=AUTH:
        raise ValueError('EXPLICIT_CURRENT_DAY_AUTHORIZATION_REQUIRED')
    import db,shopping_vnext as shop,vnext_http,urllib.request,urllib.error
    from vnext_source_guard import manual_backfill_source_context
    path=STATE/f'g2b-real-{DAY}.sqlite3'
    db.DB_PATH=str(path)
    os.environ.update(G2B_DB_PATH=str(path),G2B_RUNTIME_ROLE='LOCAL_COLLECTOR',G2B_AUTO_SYNC='0',
        G2B_SQLITE_TIMEOUT='1',G2B_VNEXT_API_DAILY_LIMIT='950')
    os.environ['G2B_SERVICE_KEY']=live.load_key()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise urllib.error.HTTPError('https://apis.data.go.kr/',302,'REDIRECT_REFUSED',{},None)
    urllib.request.urlopen=urllib.request.build_opener(NoRedirect()).open
    conn=live.ledger_open(STATE/'live-calls.sqlite3')
    result={'attempted':0,'stored_rows':0,'source_retry_automatic':False}
    try:
        with LocalDbProcessLock(path):
            before=readback(path,DAY)
            if not before or before['next_page']!=1 or before['saved']!=0 or before['status']!='FAILED':
                raise ValueError('ORIGINAL_EMPTY_FAILED_CHECKPOINT_REQUIRED')
            requests=0
            def request(url,kind):
                nonlocal requests
                requests+=1
                if requests!=1 or time.monotonic()-plan['started_monotonic']>30:
                    raise ValueError('ONE_MANUAL_CALL_ONLY')
                return vnext_http.request(url,kind,timeout=10,retries=1,strict_response_limit=True,
                    observer=lambda phase:live.phase(conn,plan['call_id'],phase),
                    response_observer=lambda raw,status,mime:live.write_json(run/'response-shape.json',summarize(raw,status,mime)))
            shop._request=request
            with manual_backfill_source_context(collection_date=DAY,range_start='2026-01-01',range_end=DAY,
                    held_dates=['2026-11-12'],max_requests=1):
                collected=shop.collect_all(DAY,DAY,page_size=999,max_pages=1,resume=True)
            if collected['status'] not in ('RUNNING','COMPLETE') or collected.get('reason'):
                raise ValueError('SOURCE_PAGE_REJECTED')
            live.phase(conn,plan['call_id'],'stored')
            receipt=readback(path,DAY,expected_page=1)
            finish_recheck(conn,plan['call_id'],receipt)
            import backfill_queue
            backfill_queue.record_verified(conn,DAY,receipt)
            result.update(state='VERIFIED',readback=receipt,stored_rows=receipt['page_rows'])
    except Exception as exc:
        code=str(getattr(exc,'code',''))
        result.update(state='HOLD',error_type=type(exc).__name__,safe_error_code=code if code.replace('_','').isalnum() else '')
        with conn: conn.execute("UPDATE calls SET state='UNCERTAIN' WHERE id=?",(plan['call_id'],))
    finally:
        row=dict(conn.execute('SELECT * FROM calls WHERE id=?',(plan['call_id'],)).fetchone())
        result.update(call=row,attempted=row['attempted'],memory=process_memory(),finished_at_kst=live.now().isoformat())
        live.write_json(run/'worker-result.json',result)
        conn.close();os.environ.pop('G2B_SERVICE_KEY',None)


def main():
    run=STATE/RUN
    run.mkdir()  # an existing run can only be inspected, never called again
    plan={'authorization':AUTH,'source_day':DAY,'page':1,'max_calls':1,'max_seconds':85,
        'started_monotonic':ENTRY,'started_at_kst':live.now().isoformat(),
        'budget_day':live.now().date().isoformat(),'hold_sha256':hashlib.sha256(live.HOLD.read_bytes()).hexdigest()}
    with ExitStack() as stack:
        stack.enter_context(LocalDbProcessLock(STATE/'live-calls.sqlite3'))
        for path in (live.BASE/'data/g2b-local.sqlite3.collector.lock',live.BASE/'isolated-prep-20261113/data/g2b-new-20261112.sqlite3.collector.lock'):
            handle=live.existing_lock(path);stack.callback(live.unlock,handle)
        with closing(live.ledger_open(STATE/'live-calls.sqlite3')) as conn:
            plan['call_id']=reserve_recheck(conn,RUN,65,plan['budget_day'])
        live.write_json(run/'plan.json',plan)
        env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('TOKEN','SECRET','SERVICE_KEY','API_KEY','PROVENANCE'))}
        with (run/'worker.log').open('w',encoding='utf-8') as log:
            process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',str(run)],
                stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
            job=WindowsJob(process,256);peak=0
            try:
                process.stdin.write(b'GO\n');process.stdin.flush();process.stdin.close()
                while process.poll() is None and time.monotonic()-ENTRY<80:
                    try: peak=max(peak,process_memory(int(process._handle))['private_bytes'])
                    except OSError: pass
                    time.sleep(.05)
            finally:
                job.close()
                if process.poll() is None: process.kill()
                process.wait(timeout=3)
        result=json.loads((run/'worker-result.json').read_text(encoding='utf-8')) if (run/'worker-result.json').exists() else {'state':'HOLD','reason':'WORKER_INTERRUPTED_NO_RETRY'}
        result.update(elapsed_seconds=round(time.monotonic()-ENTRY,3),peak_private_bytes=peak,
            historical_hold_unchanged=hashlib.sha256(live.HOLD.read_bytes()).hexdigest()==plan['hold_sha256'])
        live.write_json(run/'result.json',result)
        print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker',type=Path);parser.add_argument('--execute-once',action='store_true');args=parser.parse_args()
    if args.worker:worker(args.worker)
    elif args.execute_once:main()
    else:parser.error('--execute-once required')
