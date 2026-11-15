"""Bounded manual or Windows-scheduled G2B collection using the existing collector.

User's 900 remaining calls are an estimate, not a provider-verified counter.
Timed retries are separate counted requests after local receipt reconciliation.
"""
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
from contextlib import closing, ExitStack
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'g2b_recovery_candidate'
BASE=Path('D:/G2B')
HOLD=BASE/'isolated-prep-20261113/AUTO_COLLECTION_HOLD.json'
KST=timezone(timedelta(hours=9))
sys.path.insert(0,str(SOURCE))


class Stop(RuntimeError):
    pass


def now():
    return datetime.now(KST)


def quota_window(clock=None):
    """User-confirmed daily boundary; rollover never deletes calls or checkpoints."""
    clock=clock or now()
    if clock.tzinfo is None:
        raise Stop('TIMEZONE_REQUIRED')
    local=clock.astimezone(KST)
    reset=(local+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    return {'budget_day':local.date().isoformat(),'quota_timezone':'Asia/Seoul',
            'quota_reset_at_kst':reset.isoformat(timespec='seconds'),
            'quota_reset_basis':'USER_CONFIRMED_MIDNIGHT',
            'quota_reset_starts_collection':False}


def validate_plan(plan,clock=None):
    clock=(clock or now()).astimezone(KST)
    if plan.get('mode') not in ('EXPLICIT_MANUAL_LATEST_ONLY','EXPLICIT_MANUAL_BACKFILL','WINDOWS_SCHEDULED_BACKFILL'):
        raise Stop('MANUAL_MODE_REQUIRED')
    if plan['mode']=='WINDOWS_SCHEDULED_BACKFILL':
        from schedule_policy import verify_plan_budget
        try:
            verify_plan_budget(plan,clock)
        except ValueError:
            raise Stop('SCHEDULE_BUDGET_MISMATCH') from None
    elif plan.get('budget_basis')!='USER_ASSUMPTION' or plan.get('assumed_remaining')!=900:
        raise Stop('USER_BUDGET_REQUIRED')
    if plan.get('budget_day')!=clock.date().isoformat():
        raise Stop('KST_DAY_CHANGED')
    if plan.get('source_day')!=(clock.date()-timedelta(days=1)).isoformat():
        raise Stop('ONLY_LATEST_COMPLETED_DAY_ALLOWED')
    if not 1<=plan.get('max_pages',0)<=8 or plan.get('max_seconds')!=85:
        raise Stop('INVALID_RUN_LIMIT')
    if plan.get('source_day')==plan.get('held_source_day'):
        raise Stop('HELD_SCOPE_MUST_NOT_BE_RETRIED')
    if plan['mode'] in ('EXPLICIT_MANUAL_BACKFILL','WINDOWS_SCHEDULED_BACKFILL'):
        if plan.get('range_start')!='2026-01-01' or plan.get('range_end')!=plan['source_day']:
            raise Stop('INVALID_BACKFILL_RANGE')


def validate_request_day(plan,day):
    from historical_resume import allowed
    if day==plan['held_source_day'] and not allowed(plan):
        raise Stop('HELD_SCOPE_MUST_NOT_BE_RETRIED')
    if plan['mode'] in ('EXPLICIT_MANUAL_BACKFILL','WINDOWS_SCHEDULED_BACKFILL'):
        try:
            parsed=datetime.fromisoformat(day).date().isoformat()
        except ValueError:
            raise Stop('INVALID_SOURCE_DAY') from None
        if parsed!=day or not plan['range_start']<=day<=plan['range_end']:
            raise Stop('SOURCE_DAY_OUTSIDE_AUTHORIZED_RANGE')
    elif day!=plan['source_day']:
        raise Stop('ONLY_LATEST_COMPLETED_DAY_ALLOWED')


def write_json(path,value):
    with path.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2)
        f.flush()
        os.fsync(f.fileno())


def ledger_open(path):
    conn=sqlite3.connect(path,timeout=1)
    conn.row_factory=sqlite3.Row
    from restart_recovery import ensure_schema
    ensure_schema(conn)
    return conn


def reserve(conn,plan,page,source_day=None,retry_of=None):
    validate_plan(plan)
    source_day=source_day or plan['source_day']
    validate_request_day(plan,source_day)
    conn.execute('BEGIN IMMEDIATE')
    try:
        from restart_recovery import unresolved
        if retry_of is None and unresolved(conn,source_day):
            raise Stop('PENDING_OR_UNCERTAIN_NO_RETRY')
        count=conn.execute("SELECT COUNT(*) FROM calls WHERE budget_day=? AND state!='NOT_SENT'",(plan['budget_day'],)).fetchone()[0]
        run_count=conn.execute('SELECT COUNT(*) FROM calls WHERE run_id=?',(plan['run_id'],)).fetchone()[0]
        day_limit=plan['effective_day_budget'] if plan['mode']=='WINDOWS_SCHEDULED_BACKFILL' else min(900,950,plan['assumed_remaining'])
        if count>=day_limit or run_count>=plan['max_pages']:
            raise Stop('BUDGET_EXHAUSTED')
        if retry_of is not None:
            import delayed_retry
            if not delayed_retry.enabled(plan):
                raise Stop('DELAYED_RETRY_POLICY_REQUIRED')
            call_id=delayed_retry.reserve_replacement(conn,retry_of,run_id=plan['run_id'],
                budget_day=plan['budget_day'],fields={'source_day':source_day,'page':page},clock=now())
        else:
            call_id=conn.execute('INSERT INTO calls(run_id,source_day,page,budget_day) VALUES(?,?,?,?)',
                         (plan['run_id'],source_day,page,plan['budget_day'])).lastrowid
        conn.commit()
        return call_id
    except BaseException:
        conn.rollback()
        raise


def phase(conn,call_id,event):
    if event not in ('attempted','response_received','response_confirmed','stored'):
        raise Stop('INVALID_PHASE')
    with conn:
        conn.execute(f'UPDATE calls SET {event}=1 WHERE id=?',(call_id,))


def load_key():
    """Read existing current-user DPAPI in memory. No plaintext file or logging."""
    path=BASE/'isolated-prep-20261113/program/secure_key_handoff.py'
    spec=importlib.util.spec_from_file_location('existing_g2b_dpapi',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data=json.loads(module.TARGET.read_text(encoding='utf-8-sig'))
    if data.get('format')!='windows-dpapi-current-user-v1':
        raise Stop('INVALID_CREDENTIAL_CONTAINER')
    plain=module.dpapi(base64.b64decode(data['ciphertext'],validate=True),False)
    try:
        return plain.decode('ascii').strip()
    finally:
        plain=None


def worker(run_dir):
    # Parent has already assigned the memory-limited Job and holds legacy locks.
    if sys.stdin.buffer.readline()!=b'GO\n':
        raise Stop('PARENT_HANDSHAKE_REQUIRED')
    plan=json.loads((run_dir/'plan.json').read_text(encoding='utf-8'))
    validate_plan(plan)
    db_path=run_dir.parent/f"g2b-real-{plan['source_day']}.sqlite3"
    ledger_path=run_dir.parent/'live-calls.sqlite3'
    import db
    import shopping_vnext as shop
    import vnext_http
    import urllib.request
    import urllib.error
    from process_lock import LocalDbProcessLock
    from receipt_validation import readback
    from vnext_source_guard import operational_recent_source_context,manual_backfill_source_context
    from vnext_store import ensure_foundation
    from windows_limits import process_memory
    deadline=plan['started_monotonic']+80
    current_call=None
    error_code=''
    memory=[]
    reason='PAGE_LIMIT'
    old_request=shop._request
    old_urlopen=urllib.request.urlopen
    ledger=None
    source_day=plan['source_day']
    is_backfill=plan['mode'] in ('EXPLICIT_MANUAL_BACKFILL','WINDOWS_SCHEDULED_BACKFILL')
    import backfill_queue
    import delayed_retry
    from historical_resume import allowed as historical_allowed
    retried=False
    retry=None
    try:
        os.environ['G2B_RUNTIME_ROLE']='LOCAL_COLLECTOR'
        os.environ['G2B_AUTO_SYNC']='0'
        os.environ['G2B_VNEXT_API_DAILY_LIMIT']=str(plan.get('effective_day_budget',900))
        os.environ['G2B_SQLITE_TIMEOUT']='1'
        os.environ['G2B_SERVICE_KEY']=load_key()
        if not os.environ['G2B_SERVICE_KEY']:
            raise Stop('EMPTY_CREDENTIAL')
        ledger=ledger_open(ledger_path)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                raise urllib.error.HTTPError('https://apis.data.go.kr/',302,'REDIRECT_REFUSED',{},None)
        opener=urllib.request.build_opener(NoRedirect())
        urllib.request.urlopen=opener.open
        for _ in range(plan['max_pages']):
            validate_plan(plan)
            if hashlib.sha256(HOLD.read_bytes()).hexdigest()!=plan['hold_sha256']:
                raise Stop('EXTERNAL_HOLD_CHANGED')
            if deadline-time.monotonic()<=30:
                reason='DEADLINE_MARGIN'
                break
            if process_memory()['private_bytes']>256*1024*1024:
                raise Stop('MEMORY_LIMIT')
            if is_backfill:
                retry=(delayed_retry.next_due(ledger,now()) if delayed_retry.enabled(plan) and not retried else None)
                selected=({'day':retry['source_day'],'next_page':retry['page'],
                           'db_path':str(run_dir.parent/f"g2b-real-{retry['source_day']}.sqlite3")}
                          if retry else backfill_queue.next_job(ledger,plan['range_start'],plan['range_end']))
                if selected is None:
                    reason='NO_READY_DAYS'
                    break
                source_day=selected['day']
                db_path=run_dir.parent/f'g2b-real-{source_day}.sqlite3'
                if Path(selected['db_path']).resolve()!=db_path.resolve():
                    raise Stop('QUEUE_DB_BINDING_MISMATCH')
            validate_request_day(plan,source_day)
            with LocalDbProcessLock(db_path):
                db.DB_PATH=str(db_path)
                os.environ['G2B_DB_PATH']=str(db_path)
                db.init_db()
                ensure_foundation()
                previous=readback(db_path,source_day)
                if is_backfill and retry and not delayed_retry.checkpoint_matches(previous,retry['page']):
                    raise Stop('RETRY_CHECKPOINT_REQUIRES_REVIEW')
                if previous and previous['status'] not in ('RUNNING','COMPLETE') and not (is_backfill and retry):
                    raise Stop('CHECKPOINT_REQUIRES_REVIEW')
                if previous and previous['complete']:
                    raise Stop('ALREADY_COMPLETE_NO_CALL')
                page=previous['next_page'] if previous else 1
                if is_backfill and page!=selected['next_page']:
                    raise Stop('QUEUE_CHECKPOINT_MISMATCH')
                current_call=reserve(ledger,plan,page,source_day,retry_of=retry['id'] if is_backfill and retry else None)
                if is_backfill and retry:
                    retried=True
                    delayed_retry.restore_failed_checkpoint(ledger,current_call,db_path,'shopping_delivery',source_day+':'+source_day,previous)
                del previous
                request_count=0
                def request_once(url,kind):
                    nonlocal request_count
                    request_count+=1
                    if request_count!=1:
                        raise Stop('NO_RETRY_ALLOWED')
                    left=deadline-time.monotonic()
                    if left<=30:
                        raise Stop('DEADLINE_MARGIN')
                    from response_evidence import summarize
                    return vnext_http.request(url,kind,timeout=min(10,left-20),retries=1,
                        observer=lambda event:phase(ledger,current_call,event),strict_response_limit=True,
                        response_observer=lambda raw,status,mime:write_json(run_dir/f'response-{current_call}.json',summarize(raw,status,mime)))
                shop._request=request_once
                context=(manual_backfill_source_context(collection_date=source_day,range_start=plan['range_start'],
                    range_end=plan['range_end'],held_dates=[] if historical_allowed(plan) else [plan['held_source_day']],max_requests=1) if is_backfill
                    else operational_recent_source_context(collection_date=source_day,max_requests=1))
                with context:
                    result=shop.collect_all(source_day,source_day,page_size=999,max_pages=1,resume=True)
                if result['status'] not in ('RUNNING','COMPLETE') or result.get('reason'):
                    raise Stop('SOURCE_PAGE_REJECTED')
                phase(ledger,current_call,'stored')
                verified=readback(db_path,source_day,expected_page=page)
                if not verified or not verified['verified']:
                    raise Stop('READBACK_FAILED')
                with ledger:
                    stages=ledger.execute('SELECT attempted,response_received,response_confirmed,stored FROM calls WHERE id=?',(current_call,)).fetchone()
                    if not all(stages):
                        raise Stop('MISSING_CALL_CONFIRMATION')
                    ledger.execute("UPDATE calls SET state='VERIFIED',verified=1,verified_rows=? WHERE id=?",(verified['page_rows'],current_call))
                complete=verified['complete']
                if is_backfill:
                    backfill_queue.record_verified(ledger,source_day,verified)
                current_call=None
                event={'source_day':source_day,'page':page,'saved_total':verified['saved'],'page_rows':verified['page_rows'],'next_page':verified['next_page'],
                       'status':verified['status'],'readback':True}
                with (run_dir/'pages.jsonl').open('a',encoding='utf-8') as f:
                    f.write(json.dumps(event)+'\n');f.flush();os.fsync(f.fileno())
                page=verified['next_page']
                del result,verified
                gc.collect()
                memory.append(process_memory()|{'page':page-1,'source_day':source_day})
                if complete and not is_backfill:
                    reason='COMPLETE'
                    break
    except Exception as exc:
        reason=str(exc) if type(exc) is Stop else type(exc).__name__
        code=str(getattr(exc,'code','') or '')
        error_code=code if re.fullmatch(r'[A-Z0-9_]{1,48}',code) else ''
        if ledger and retry and current_call is None:
            delayed_retry.defer_local_validation(ledger,retry['id'],now())
        if ledger and current_call is not None:
            with ledger:
                ledger.execute("UPDATE calls SET state='UNCERTAIN' WHERE id=?",(current_call,))
                if is_backfill:
                    ledger.execute("UPDATE backfill_days SET state='HOLD',reason='UNCERTAIN_NO_RETRY' WHERE day=?",(source_day,))
            if delayed_retry.enabled(plan):
                delayed_retry.enqueue(ledger,current_call,reason=error_code or reason,clock=now())
            if reason=='DEADLINE_MARGIN':
                from restart_recovery import reconcile
                reconcile(ledger,run_dir.parent,held_day='' if historical_allowed(plan) else plan['held_source_day'],finalize_runs=False)
                if ledger.execute('SELECT state FROM calls WHERE id=?',(current_call,)).fetchone()[0]=='NOT_SENT' and is_backfill:
                    backfill_queue.record_verified(ledger,source_day,readback(db_path,source_day))
    finally:
        shop._request=old_request
        urllib.request.urlopen=old_urlopen
        os.environ.pop('G2B_SERVICE_KEY',None)
        if ledger:
            ledger.close()
        write_json(run_dir/'worker-result.json',{'stop_reason':reason,'safe_error_code':error_code,'memory_samples':memory})


def existing_lock(path):
    import msvcrt
    handle=path.open('r+b')
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    except BaseException:
        handle.close()
        raise Stop('EXISTING_COLLECTOR_LOCK_BUSY') from None
    return handle


def unlock(handle):
    import msvcrt
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    finally:
        handle.close()


def main(run_id,assumed_remaining,max_pages,range_start=None,scheduled=False):
    clock=now()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',run_id) or assumed_remaining!=900:
        raise Stop('INVALID_MANUAL_INVOCATION')
    hold_bytes=HOLD.read_bytes()
    hold=json.loads(hold_bytes)
    plan={'run_id':run_id,'mode':'EXPLICIT_MANUAL_LATEST_ONLY','budget_basis':'USER_ASSUMPTION',
          'started_at_kst':clock.isoformat(timespec='seconds'),
          'assumed_remaining':assumed_remaining,'provider_remaining_verified':False,
          'budget_day':clock.date().isoformat(),'source_day':(clock.date()-timedelta(days=1)).isoformat(),
          'held_source_day':hold['source_day'],'held_page':hold['next_page'],
          'historical_hold_preserved':True,'hold_sha256':hashlib.sha256(hold_bytes).hexdigest(),
          'max_pages':max_pages,'max_seconds':85,'started_monotonic':ENTRY,
          'endpoint':'https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList'}
    plan.update(quota_window(clock))
    if range_start is not None:
        plan.update(mode='EXPLICIT_MANUAL_BACKFILL',range_start=range_start,range_end=plan['source_day'])
    if scheduled:
        from schedule_policy import day_policy,cycle_fits_before_reset
        if range_start!='2026-01-01':
            raise Stop('SCHEDULE_RANGE_REQUIRED')
        if not cycle_fits_before_reset(clock):
            raise Stop('MIDNIGHT_BOUNDARY_WAIT')
        plan.update(day_policy(clock))
        plan.update(mode='WINDOWS_SCHEDULED_BACKFILL',schedule_name='G2B-Bounded-Backfill-20260101',
                    invocation_kind='WINDOWS_TASK_ENTRY')
        import delayed_retry
        plan['delayed_retry']=delayed_retry.POLICY
    validate_plan(plan,clock)
    # The backfill keeps the existing real-call ledger and checkpoints across KST dates.
    root=BASE/('live-bounded-20261114' if range_start else f"live-bounded-{clock.strftime('%Y%m%d')}")
    root.mkdir(exist_ok=True)
    run_dir=root/run_id
    run_dir.mkdir()  # exclusive run ID; never retry an uncertain tool outcome
    write_json(run_dir/'plan.json',plan)
    from process_lock import LocalDbProcessLock
    from windows_limits import WindowsJob,process_memory
    from receipt_validation import readback
    process=None
    job=None
    reason='FINISHED'
    peak=0
    with ExitStack() as stack:
        stack.enter_context(LocalDbProcessLock(root/'live-calls.sqlite3'))
        for path in (BASE/'data/g2b-local.sqlite3.collector.lock',BASE/'isolated-prep-20261113/data/g2b-new-20261112.sqlite3.collector.lock'):
            handle=existing_lock(path)
            stack.callback(unlock,handle)
        ledger=ledger_open(root/'live-calls.sqlite3')
        try:
            from restart_recovery import reconcile
            if scheduled:
                from historical_resume import adopt
                plan['historical_resume']=adopt(ledger,root,BASE/'isolated-prep-20261113/data/g2b-new-20261112.sqlite3',HOLD,clock=clock)
                write_json(run_dir/'plan.json',plan)
            recovery=reconcile(ledger,root,held_day='' if plan.get('historical_resume') else plan['held_source_day'])
            if scheduled:
                delayed_retry.arm_pending(ledger,root,clock=clock)
            write_json(run_dir/'restart-recovery.json',recovery)
            if recovery['unresolved'] and not range_start:
                raise Stop('PRIOR_CALL_UNCERTAIN_NO_RETRY')
            if scheduled:
                used_today=ledger.execute("SELECT COUNT(*) FROM calls WHERE budget_day=? AND state!='NOT_SENT'",(plan['budget_day'],)).fetchone()[0]
                if used_today>=plan['effective_day_budget']:
                    raise Stop('DAILY_QUOTA_WAIT_FOR_KST_RESET')
            if range_start:
                import backfill_queue
                jobs=backfill_queue.inventory(start=range_start,end=plan['range_end'],
                    held_day=plan['held_source_day'],held_page=plan['held_page'],held_saved=hold['verified_saved'],
                    original_db=BASE/'data/g2b-local.sqlite3',live_root=root,cache_conn=ledger,resume_held=bool(plan.get('historical_resume')))
                backfill_queue.save_inventory(ledger,jobs)
                backfill_queue.isolate_unresolved_days(ledger)
                write_json(run_dir/'range-before.json',backfill_queue.summary(ledger,range_start,plan['range_end']))
            with ledger:
                ledger.execute("INSERT INTO runs VALUES(?,?,'RUNNING')",(run_id,plan['budget_day']))
        finally:
            ledger.close()
        env=dict(os.environ)
        for key in list(env):
            if any(s in key.upper() for s in ('TOKEN','SECRET','SERVICE_KEY','API_KEY','PROVENANCE')):
                env.pop(key,None)
        env['PYTHONDONTWRITEBYTECODE']='1'
        with (run_dir/'worker.log').open('w',encoding='utf-8') as log:
            try:
                process=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker',str(run_dir)],
                    stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
                job=WindowsJob(process,256)
                process.stdin.write(b'GO\n');process.stdin.flush();process.stdin.close()
                while process.poll() is None:
                    if time.monotonic()-ENTRY>=85:
                        reason='TIMEOUT_NO_RETRY';job.close();break
                    try:
                        peak=max(peak,process_memory(int(process._handle))['private_bytes'])
                    except OSError:
                        pass
                    time.sleep(0.05)
                process.wait(timeout=max(0.001,min(3,ENTRY+89-time.monotonic())))
            except Exception as exc:
                reason='SUPERVISOR_'+type(exc).__name__
            finally:
                if job:
                    job.close()
                if process and process.poll() is None:
                    process.kill()
                    try:
                        process.wait(timeout=max(0.001,min(1,ENTRY+89-time.monotonic())))
                    except subprocess.TimeoutExpired:
                        reason='TERMINATION_UNCONFIRMED'
        db_path=root/f"g2b-real-{plan['source_day']}.sqlite3"
        with closing(sqlite3.connect((root/'live-calls.sqlite3').as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            counts=dict(conn.execute('''SELECT COUNT(*) reserved,COALESCE(SUM(attempted),0) attempted,
              COALESCE(SUM(response_received),0) response_received,COALESCE(SUM(response_confirmed),0) response_confirmed,
              COALESCE(SUM(stored),0) stored_pages,COALESCE(SUM(verified),0) verified_pages,
              COALESCE(SUM(verified_rows),0) verified_rows,COALESCE(SUM(state='NOT_SENT'),0) not_sent FROM calls WHERE run_id=?''',(run_id,)).fetchone())
            used=conn.execute("SELECT COUNT(*) FROM calls WHERE budget_day=? AND state!='NOT_SENT'",(plan['budget_day'],)).fetchone()[0]
            actual_today=conn.execute('SELECT COALESCE(SUM(attempted),0) FROM calls WHERE budget_day=?',(plan['budget_day'],)).fetchone()[0]
            retry_attempts=conn.execute('SELECT COALESCE(SUM(c.attempted),0) FROM delayed_retries r JOIN calls c ON c.id=r.replacement_id WHERE c.run_id=?',(run_id,)).fetchone()[0]
            historical_attempts=conn.execute('SELECT COALESCE(SUM(attempted),0) FROM calls WHERE run_id=? AND source_day=?',(run_id,plan['held_source_day'])).fetchone()[0]
            touched_days=[r[0] for r in conn.execute('SELECT DISTINCT source_day FROM calls WHERE run_id=? ORDER BY source_day DESC',(run_id,))]
            range_summary=backfill_queue.summary(conn,range_start,plan['range_end']) if range_start else None
            from restart_recovery import unresolved_days
            held_days=unresolved_days(conn)
        report={'mode':'REAL_API_MANUAL','source_day':plan['source_day'],'db_path':str(db_path),**counts,
            'started_at_kst':plan['started_at_kst'],
            'supervisor_outcome':reason,'worker_exited':bool(process and process.poll() is not None),
            'worker_exit_code':process.returncode if process else None,'elapsed_seconds':round(time.monotonic()-ENTRY,3),
            'peak_private_bytes':peak,'memory_limit_mb':256,'budget_basis':'USER_ASSUMPTION',
            'user_assumed_remaining_before':900,'local_reserved_today':used,'actual_calls_today':actual_today,'remaining_from_assumption':max(0,900-used),
            'official_remaining':None,'historical_hold_unchanged':hashlib.sha256(HOLD.read_bytes()).hexdigest()==plan['hold_sha256'],
            'held_scope_retried':bool(historical_attempts),'source_retry_performed':bool(retry_attempts),
            'delayed_retry_attempts':retry_attempts,'scheduled_execution':False,'main_merged_or_pushed':False}
        report.update({key:plan[key] for key in ('budget_day','quota_timezone','quota_reset_at_kst',
                                               'quota_reset_basis','quota_reset_starts_collection')})
        if scheduled:
            report.update(mode='REAL_API_SCHEDULED_ENTRY',scheduled_execution=True,
                schedule_name=plan['schedule_name'],budget_basis=plan['budget_basis'],
                effective_day_budget=plan['effective_day_budget'],configured_daily_ceiling=950,
                remaining_from_assumption=max(0,900-used) if plan['budget_day']=='2026-11-14' else None,
                remaining_to_configured_budget=max(0,plan['effective_day_budget']-used))
        if range_start:
            report['collection_range']=range_summary
            report['held_days']=held_days
            report['other_dates_continue']=True
        report['days_readback']={}
        for day in touched_days:
            try:
                report['days_readback'][day]=readback(root/f'g2b-real-{day}.sqlite3',day)
            except Exception as exc:
                report['days_readback'][day]={'verified':False,'reason':type(exc).__name__}
        if (run_dir/'worker-result.json').exists():
            report.update(json.loads((run_dir/'worker-result.json').read_text(encoding='utf-8')))
        try:
            report['checkpoint_readback']=readback(db_path,plan['source_day']) if db_path.exists() else None
        except Exception as exc:
            report['checkpoint_readback']={'verified':False,'reason':type(exc).__name__}
        normal_stop=report.get('stop_reason') in ('PAGE_LIMIT','COMPLETE','NO_READY_DAYS','DEADLINE_MARGIN','BUDGET_EXHAUSTED')
        final_state='FINISHED' if (counts['reserved']==counts['verified_pages']+counts['not_sent'] and reason=='FINISHED'
            and process and process.returncode==0 and normal_stop
            and all(r.get('verified') for r in report['days_readback'].values())) else 'HELD'
        with closing(sqlite3.connect(root/'live-calls.sqlite3',timeout=1)) as conn:
            with conn:
                conn.execute('UPDATE runs SET state=? WHERE id=?',(final_state,run_id))
        report['run_state']=final_state
        report['finished_at_kst']=now().isoformat(timespec='seconds')
        report['elapsed_seconds']=round(time.monotonic()-ENTRY,3)
        report['under_90_seconds']=time.monotonic()-ENTRY<90
        write_json(run_dir/'result.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',type=Path)
    parser.add_argument('--execute-once',action='store_true')
    parser.add_argument('--run-id')
    parser.add_argument('--assumed-remaining',type=int)
    parser.add_argument('--max-pages',type=int,default=8)
    parser.add_argument('--from-date',choices=['2026-01-01'])
    args=parser.parse_args()
    if args.worker:
        worker(args.worker.resolve())
    elif args.execute_once:
        try:
            main(args.run_id,args.assumed_remaining,args.max_pages,args.from_date)
        except Exception as exc:
            print(json.dumps({'outcome':'STOPPED_NO_AUTOMATIC_RETRY','error_type':type(exc).__name__,
                              'safe_code':str(exc) if type(exc) is Stop else ''}))
            raise SystemExit(1)
    else:
        parser.error('Explicit --execute-once is required; no daemon/schedule mode exists.')
