"""One invocation of the Windows task; no daemon, retries, or schedule changes."""
import hashlib
import json
import os
import sys
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DEPLOY_ROOT=Path('D:/G2B/automatic-backfill-v5')
STATE_ROOT=Path('D:/G2B/live-bounded-20261114')
TASK_NAME='G2B-Bounded-Backfill-20260101'
KST=timezone(timedelta(hours=9))


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


def main():
    if ROOT.resolve()!=DEPLOY_ROOT.resolve():
        raise RuntimeError('DEPLOYED_TASK_ENTRY_REQUIRED')
    logs=ROOT/'logs'
    logs.mkdir(exist_ok=True)
    stamp=datetime.now(KST).strftime('%Y%m%dT%H%M%S')
    with (logs/f'entry-{stamp}.log').open('w',encoding='utf-8') as log,redirect_stdout(log),redirect_stderr(log):
        try:
            manifest=verify_package(ROOT)
            import live_collection_once as live
            from process_lock import LocalDbProcessLock
            from schedule_policy import slot_id
            if hashlib.sha256(live.HOLD.read_bytes()).hexdigest()!=manifest['historical_hold_sha256']:
                raise ValueError('HISTORICAL_HOLD_CHANGED')
            with LocalDbProcessLock(ROOT/'task-entry'):
                run_id=slot_id(live.now())
                result_path=STATE_ROOT/run_id/'result.json'
                if result_path.parent.exists():
                    write_status({'state':'SKIPPED_DUPLICATE_SLOT','run_id':run_id,'new_source_calls':0})
                    return 0
                write_status({'state':'RUNNING','run_id':run_id,'invocation':'WINDOWS_TASK_ENTRY'})
                live.main(run_id,900,8,'2026-01-01',scheduled=True)
                result=json.loads(result_path.read_text(encoding='utf-8'))
                verified=run_verified(result)
                continuing=(not verified and bool(result.get('held_days')) and result.get('under_90_seconds')
                    and result.get('worker_exited') and result.get('worker_exit_code')==0)
                write_status({'state':'VERIFIED' if verified else 'CONTINUING_WITH_SCOPE_HOLD' if continuing else 'ERROR_RECHECK_NEXT_SCHEDULE','run_id':run_id,
                    'stop_reason':result.get('stop_reason'),
                    'actual_attempts':result['attempted'],'verified_rows':result['verified_rows'],
                    'local_calls_today':result['actual_calls_today'],'local_reserved_today':result['local_reserved_today'],
                    'not_sent':result.get('not_sent',0),'remaining_to_configured_budget':result['remaining_to_configured_budget'],
                    'official_remaining':None,'quota_reset_at_kst':result['quota_reset_at_kst'],
                    'next':result.get('collection_range'),'held_days':result.get('held_days',[]),
                    'other_dates_continue':True,'result_path':str(result_path)})
                return 0 if verified or continuing else 1
        except Exception as exc:
            safe=str(exc) if type(exc).__name__ in ('Stop','ValueError') and str(exc).replace('_','').isalnum() else type(exc).__name__
            state='WAIT_FOR_KST_RESET' if safe in ('DAILY_QUOTA_WAIT_FOR_KST_RESET','MIDNIGHT_BOUNDARY_WAIT') else 'HOLD'
            write_status({'state':state,'safe_reason':safe,'source_retry_performed':False})
            return 0 if state=='WAIT_FOR_KST_RESET' else 1


if __name__=='__main__':
    raise SystemExit(main())
