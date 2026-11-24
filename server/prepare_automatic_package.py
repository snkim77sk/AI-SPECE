"""Prepare or install a reviewed isolated Windows-task package; never overwrite source."""
import argparse
import hashlib
import json
import shutil
import tzdata
from pathlib import Path
import scheduled_entry

ROOT=Path(__file__).resolve().parent
PACKAGE=ROOT/'automatic-package-v5.2.16'
DEST=Path('D:/G2B/automatic-backfill-v5.2.16')
HOLD=Path('D:/G2B/isolated-prep-20261113/AUTO_COLLECTION_HOLD.json')
MODULES=('db.py','vnext_schema.py','vnext_store.py','vnext_paging.py','vnext_provenance.py','shopping_vnext.py',
    'vnext_source_guard.py','vnext_response.py','vnext_live_gate.py','runtime_role.py','app_version.py','VERSION.txt',
    'vnext_collection.py','vnext_http.py','receipt_validation.py','process_lock.py','windows_limits.py')


def prepare():
    PACKAGE.mkdir()  # fail if a prior package exists; don't overwrite it
    for name in ('live_collection_once.py','delivery_recheck.py','backfill_queue.py','schedule_policy.py','scheduled_entry.py','restart_recovery.py','response_evidence.py','delayed_retry.py','historical_resume.py','bounded_cycle.py'):
        shutil.copy2(ROOT/name,PACKAGE/name)
    target=PACKAGE/'g2b_recovery_candidate'
    target.mkdir()
    for name in MODULES:
        shutil.copy2(ROOT/'g2b_recovery_candidate'/name,target/name)
    previous=Path('D:/G2B/automatic-backfill-v5')
    scheduled_entry.verify_package(previous)
    shutil.copytree(previous/'g2b_recovery_candidate/tzdata',target/'tzdata',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    files={str(path.relative_to(PACKAGE)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest()
           for path in sorted(PACKAGE.rglob('*')) if path.is_file()}
    manifest={'task_name':scheduled_entry.TASK_NAME,'version':'5.2.16','files':files,
        'historical_hold_sha256':hashlib.sha256(HOLD.read_bytes()).hexdigest(),
        'schedule':{'interval_minutes':2,'max_pages':8,'max_seconds':90,'memory_mb':256,
                    'daily_ceiling':950,'reset_timezone':'Asia/Seoul','initial_day':'2026-11-14','initial_budget':900},
        'range_start':'2026-01-01','credential_storage':'existing current-user DPAPI, not copied',
        'credential_values_in_package':False}
    (PACKAGE/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    scheduled_entry.verify_package(PACKAGE)
    print(json.dumps({'package':str(PACKAGE),'file_count':len(files),'verified':True}))


def install():
    if DEST.resolve().parent!=Path('D:/G2B').resolve():
        raise ValueError('UNEXPECTED_DEPLOY_TARGET')
    manifest=scheduled_entry.verify_package(PACKAGE)
    if hashlib.sha256(HOLD.read_bytes()).hexdigest()!=manifest['historical_hold_sha256']:
        raise ValueError('HOLD_CHANGED_BEFORE_INSTALL')
    shutil.copytree(PACKAGE,DEST)  # existing target is an error, never replaced
    scheduled_entry.verify_package(DEST)
    evidence={'installed_path':str(DEST),'package_verified':True,'files':len(manifest['files']),
              'original_program_modified':False,'credentials_copied':False,'task_registered':False}
    (ROOT/'evidence').mkdir(exist_ok=True)
    (ROOT/'evidence/g2b-v5.2.16-deployment.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--install',action='store_true')
    args=parser.parse_args()
    install() if args.install else prepare()
