"""One bounded invocation; delayed replays are persisted between task runs."""
import hashlib
import json
import os
import sys
import time
import sqlite3
from contextlib import closing
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DEPLOY_ROOT=Path('D:/G2B/automatic-backfill-v5.2.16')
STATE_ROOT=Path('D:/G2B/live-bounded-20261114')
TASK_NAME='G2B-Bounded-Backfill-20260101'
KST=timezone(timedelta(hours=9))
sys.path.insert(0,str(ROOT/'g2b_recovery_candidate'))


def verify_package(root):
    manifest=json.loads((root/'runtime-manifest.json').read_text(encoding='utf-8'))
    if manifest.get('task_name')!=TASK_NAME:
        raise ValueError('MANIFEST_TASK_MISMATCH')
    for name,digest in manifest['files'].items():
        target=(root/name).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError('INVALID_PACKAGE_PATH')
        with target.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:
                raise ValueError('RUNTIME_FILE_CHANGED')
    return manifest


def write_status(status):
    status.update(task_name=TASK_NAME,observed_at_kst=datetime.now(KST).isoformat(timespec='seconds'),
                  pid=os.getpid(),parent_pid=os.getppid())
    path=STATE_ROOT/'automatic-status.json'
    temp=path.with_suffix('.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(status,stream,ensure_ascii=False,indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def run_verified(result):
    normal=('PAGE_LIMIT','COMPLETE','NO_READY_DAYS','DEADLINE_MARGIN','BUDGET_EXHAUSTED')
    return (result['reserved']==result['verified_pages']+result.get('not_sent',0) and result['run_state']=='FINISHED'
        and result['under_90_seconds'] and result['worker_exited'] and result['worker_exit_code']==0
        and result.get('stop_reason') in normal
        and all(r.get('verified') for r in result['days_readback'].values()))


def safe_reason(exc):
    return str(exc) if type(exc).__name__ in ('Stop','ValueError') and str(exc).replace('_','').isalnum() else type(exc).__name__


def controller(run_id,started):
    if ROOT.resolve()!=DEPLOY_ROOT.resolve() or sys.stdin.buffer.readline()!=b'GO\n':
        raise RuntimeError('DEPLOYED_CONTROLLER_HANDSHAKE_REQUIRED')
    import live_collection_once as live
    try:
        manifest=verify_package(ROOT)
        if hashlib.sha256(live.HOLD.read_bytes()).hexdigest()!=manifest['historical_hold_sha256']:
            raise ValueError('HISTORICAL_HOLD_CHANGED')
        live.ENTRY=started
        live.main(run_id,900,8,'2026-01-01',scheduled=True)
        return 0
    except Exception as exc:
        live.write_json(STATE_ROOT/(run_id+'-entry-error.json'),{'safe_reason':safe_reason(exc)})
        return 1


def interrupted_result(run_id,started,outcome):
    """After the whole process tree stops, record counts without slow DB scans.

    Uncertain requests are never labelled unsent here. The next scheduled run
    reconciles their committed receipts before permitting a delayed replacement.
    """
    import live_collection_once as live
    import backfill_queue
    from schedule_policy import day_policy
    root=STATE_ROOT/run_id
    root.mkdir(exist_ok=True)
    clock=datetime.now(KST)
    plan=json.loads((root/'plan.json').read_text(encoding='utf-8')) if (root/'plan.json').exists() else {}
    policy=day_policy(datetime.fromisoformat(plan.get('started_at_kst',clock.isoformat())))
    progress=json.loads((root/'progress.json').read_text(encoding='utf-8')) if (root/'progress.json').exists() else {}
    worker_progress=json.loads((root/'worker-progress.json').read_text(encoding='utf-8')) if (root/'worker-progress.json').exists() else {}
    error=STATE_ROOT/(run_id+'-entry-error.json')
    reason=(json.loads(error.read_text(encoding='utf-8'))['safe_reason'] if error.exists() else
        'CYCLE_DEADLINE' if outcome.get('timed_out') else 'CONTROLLER_INTERRUPTED')
    counts=dict.fromkeys(('reserved','attempted','response_received','response_confirmed','stored_pages','verified_pages','verified_rows','not_sent'),0)
    used=actual=0
    range_summary=None
    held=[]
    ledger=STATE_ROOT/'live-calls.sqlite3'
    if ledger.exists():
        with closing(sqlite3.connect(ledger,timeout=1)) as conn:
            conn.row_factory=sqlite3.Row
            counts=dict(conn.execute('''SELECT COUNT(*) reserved,COALESCE(SUM(attempted),0) attempted,
                COALESCE(SUM(response_received),0) response_received,COALESCE(SUM(response_confirmed),0) response_confirmed,
                COALESCE(SUM(stored),0) stored_pages,COALESCE(SUM(verified),0) verified_pages,
                COALESCE(SUM(verified_rows),0) verified_rows,COALESCE(SUM(state='NOT_SENT'),0) not_sent
                FROM calls WHERE run_id=?''',(run_id,)).fetchone())
            used,actual=conn.execute("SELECT COALESCE(SUM(state!='NOT_SENT'),0),COALESCE(SUM(attempted),0) FROM calls WHERE budget_day=?",(policy['budget_day'],)).fetchone()
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='backfill_days'").fetchone():
                range_summary=backfill_queue.summary(conn,'2026-01-01',plan.get('range_end',(clock.date()-timedelta(days=1)).isoformat()))
            held=[r[0] for r in conn.execute("SELECT DISTINCT source_day FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RECHECK_SUPERSEDED','RETRY_SUPERSEDED')")]
            with conn:
                conn.execute("INSERT INTO runs VALUES(?,?,'INTERRUPTED_HELD') ON CONFLICT(id) DO UPDATE SET state='INTERRUPTED_HELD'",(run_id,policy['budget_day']))
    result={**counts,**outcome,**policy,'run_state':'INTERRUPTED_HELD','stop_reason':reason,
        'last_phase':progress.get('phase','VERIFYING_PACKAGE'),'scheduled_execution':True,
        'last_worker_stage':worker_progress.get('stage'),
        'worker_exited':outcome.get('process_tree_stopped',False),'worker_exit_code':outcome.get('controller_exit_code'),
        'elapsed_seconds':round(time.monotonic()-started,3),'under_90_seconds':time.monotonic()-started<90,
        'actual_calls_today':actual,'local_reserved_today':used,'remaining_to_configured_budget':max(0,policy['effective_day_budget']-used),
        'official_remaining':None,'collection_range':range_summary,'held_days':held,'days_readback':{},
        'started_at_kst':plan.get('started_at_kst'),'finished_at_kst':clock.isoformat(timespec='seconds'),
        'other_dates_continue':True,'resume_on_next_schedule':True}
    live.write_json(root/'result.json',result)
    return result


def main():
    if ROOT.resolve()!=DEPLOY_ROOT.resolve():
        raise RuntimeError('DEPLOYED_TASK_ENTRY_REQUIRED')
    started=time.monotonic()
    logs=ROOT/'logs'
    logs.mkdir(exist_ok=True)
    stamp=datetime.now(KST).strftime('%Y%m%dT%H%M%S')
    with (logs/f'entry-{stamp}.log').open('w',encoding='utf-8') as log,redirect_stdout(log),redirect_stderr(log):
        try:
            import live_collection_once as live
            from process_lock import LocalDbProcessLock
            from schedule_policy import slot_id
            from bounded_cycle import run_bounded
            # Shared across runtime versions as well as the scheduler's IgnoreNew.
            with LocalDbProcessLock(STATE_ROOT/'task-entry'):
                run_id=slot_id(live.now())
                result_path=STATE_ROOT/run_id/'result.json'
                marker=STATE_ROOT/(run_id+'.entry')
                if result_path.parent.exists() or marker.exists():
                    write_status({'state':'SKIPPED_DUPLICATE_SLOT','run_id':run_id,'new_source_calls':0})
                    return 0
                with marker.open('x',encoding='utf-8') as entry:
                    entry.write(datetime.now(KST).isoformat());entry.flush();os.fsync(entry.fileno())
                write_status({'state':'RUNNING','run_id':run_id,'invocation':'WINDOWS_TASK_ENTRY'})
                outcome=run_bounded([sys.executable,'-B',str(ROOT/'scheduled_entry.py'),'--controller',run_id,str(started)],
                    cwd=ROOT,log=log,started=started)
                result=(json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists()
                    else interrupted_result(run_id,started,outcome))
                result.update(outcome,runtime_version='5.2.16',whole_cycle_elapsed_seconds=round(time.monotonic()-started,3))
                result['under_90_seconds']=result['under_90_seconds'] and time.monotonic()-started<90
                live.write_json(result_path,result)
                verified=run_verified(result)
                continuing=(result.get('run_state')!='INTERRUPTED_HELD' and not verified and bool(result.get('held_days')) and result.get('under_90_seconds')
                    and result.get('worker_exited') and result.get('worker_exit_code')==0)
                waiting=result.get('stop_reason') in ('DAILY_QUOTA_WAIT_FOR_KST_RESET','MIDNIGHT_BOUNDARY_WAIT')
                write_status({'state':'WAIT_FOR_KST_RESET' if waiting else 'VERIFIED' if verified else 'CONTINUING_WITH_SCOPE_HOLD' if continuing else 'ERROR_RECHECK_NEXT_SCHEDULE','run_id':run_id,
                    'stop_reason':result.get('stop_reason'),
                    'last_phase':result.get('last_phase','FINISHED'),'resume_on_next_schedule':True,
                    'actual_attempts':result['attempted'],'verified_rows':result['verified_rows'],
                    'local_calls_today':result['actual_calls_today'],'local_reserved_today':result['local_reserved_today'],
                    'not_sent':result.get('not_sent',0),'remaining_to_configured_budget':result['remaining_to_configured_budget'],
                    'official_remaining':None,'quota_reset_at_kst':result['quota_reset_at_kst'],
                    'next':result.get('collection_range'),'held_days':result.get('held_days',[]),
                    'other_dates_continue':True,'result_path':str(result_path)})
                return 0 if verified or continuing or waiting else 1
        except Exception as exc:
            safe=safe_reason(exc)
            state='WAIT_FOR_KST_RESET' if safe in ('DAILY_QUOTA_WAIT_FOR_KST_RESET','MIDNIGHT_BOUNDARY_WAIT') else 'HOLD'
            write_status({'state':state,'safe_reason':safe,'source_retry_performed':False})
            return 0 if state=='WAIT_FOR_KST_RESET' else 1


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--controller':
        raise SystemExit(controller(sys.argv[2],float(sys.argv[3])))
    raise SystemExit(main())
