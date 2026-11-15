"""Independent Windows entry: package integrity and both-source first-response proof."""
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing,redirect_stdout,redirect_stderr
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DEPLOY=Path('D:/G2B/lofin-budget-v3')
PREVIOUS=Path('D:/G2B/lofin-budget-v2')
TASK='G2B-Lofin365-Budget-2026'


def verify_package(root):
    manifest=json.loads((root/'runtime-manifest.json').read_text(encoding='utf-8'))
    if manifest['task_name']!=TASK: raise ValueError('WRONG_TASK_PACKAGE')
    for name,digest in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()): raise ValueError('INVALID_PACKAGE_PATH')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest: raise ValueError('PACKAGE_CHANGED')
    return manifest


def require_live_proof(state):
    with closing(sqlite3.connect((state/'lofin-calls.sqlite3').as_uri()+'?mode=ro',uri=True)) as conn:
        found={r[0] for r in conn.execute("SELECT service FROM calls WHERE state='VERIFIED' AND attempted=1 AND response_confirmed=1 AND stored=1 AND verified=1 AND verified_rows>0 GROUP BY service")}
        if found!={'AIDFA','QWGJK'}: raise ValueError('BOTH_BUDGET_SOURCES_MUST_BE_VERIFIED')
        for service in found:
            row=conn.execute("SELECT run_id FROM calls WHERE service=? AND state='VERIFIED' AND verified_rows>0 ORDER BY id DESC LIMIT 1",(service,)).fetchone()
            path=(state/row[0]/'plan.json').resolve()
            if not path.is_relative_to(state.resolve()): raise ValueError('INVALID_PROOF_PATH')
            plan=json.loads(path.read_text(encoding='utf-8'))
            if not compatible_proof(plan.get('runtime_manifest_sha256')):
                raise ValueError('FIRST_RESPONSE_RUNTIME_MISMATCH')


def compatible_proof(proof_hash):
    try: current=(ROOT/'runtime-manifest.json').read_bytes()
    except OSError: return False
    if proof_hash==hashlib.sha256(current).hexdigest(): return True
    # Completed AIDFA is not fetched again just to update orchestration code.
    # Accept v2 proof only when every transport/parser/storage module is identical.
    try: previous=(PREVIOUS/'runtime-manifest.json').read_bytes()
    except OSError: return False
    if proof_hash!=hashlib.sha256(previous).hexdigest(): return False
    core=lambda raw:{k:v for k,v in json.loads(raw).get('files',{}).items() if k.startswith('lofin_candidate/')}
    return bool(core(current)) and core(current)==core(previous)


def main():
    if ROOT.resolve()!=DEPLOY.resolve(): raise RuntimeError('DEPLOYED_ENTRY_REQUIRED')
    verify_package(ROOT)
    import lofin_collection as live
    from process_lock import LocalDbProcessLock
    logs=ROOT/'logs';logs.mkdir(exist_ok=True)
    clock=live.now();stamp=clock.strftime('%Y%m%dT%H%M%S')
    with (logs/f'entry-{stamp}.log').open('w',encoding='utf-8') as log,redirect_stdout(log),redirect_stderr(log):
        try:
            require_live_proof(live.STATE)
            with LocalDbProcessLock(ROOT/'task-entry'):
                slot=clock.replace(minute=clock.minute//15*15,second=0,microsecond=0).strftime('%Y%m%dT%H%M')
                run_id='scheduled-'+slot
                if (live.STATE/run_id).exists(): return 0
                result=live.main(run_id,8,scheduled=True)
                return 0 if result['state'] in ('VERIFIED','CONTINUING_WITH_SCOPE_HOLD') else 1
        except Exception as exc:
            reason=live.safe_error(exc)
            if type(exc) is ValueError and str(exc).replace('_','').isalnum(): reason=str(exc)
            state='WAIT' if reason in ('LOCAL_QUOTA_WAIT','MIDNIGHT_BOUNDARY_WAIT','OFFICIAL_MAINTENANCE_WAIT') else 'HOLD'
            live.atomic_json(live.STATE/'lofin-status.json',{'state':state,'safe_reason':reason,'new_source_calls':0,
                'observed_at_kst':live.now().isoformat(timespec='seconds'),'pid':os.getpid(),'parent_pid':os.getppid()})
            return 0 if state=='WAIT' else 1


if __name__=='__main__': raise SystemExit(main())
