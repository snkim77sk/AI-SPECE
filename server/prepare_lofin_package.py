"""Copy a fixed whitelist to a new isolated deployment, preserving all prior files."""
import argparse
import hashlib
import json
import shutil
import tzdata
from pathlib import Path
from lofin_scheduled_entry import verify_package,TASK,DEPLOY
ROOT=Path(__file__).resolve().parent
PACKAGE=ROOT/'lofin-budget-package-v3'


def prepare():
    PACKAGE.mkdir()
    for name in ('lofin_collection.py','lofin_receipts.py','lofin_analysis.py','lofin_viewer.py','lofin_scheduled_entry.py','OPEN_BUDGET_ANALYSIS.cmd'):
        shutil.copy2(ROOT/name,PACKAGE/name)
    shutil.copytree(ROOT/'lofin_candidate',PACKAGE/'lofin_candidate',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(Path(tzdata.__file__).parent,PACKAGE/'lofin_candidate/tzdata',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    files={str(p.relative_to(PACKAGE)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in PACKAGE.rglob('*') if p.is_file()}
    manifest={'task_name':TASK,'files':files,'source':'LOFIN','datasets':['budget','budget_appropriation'],
              'range_start':'2026-01-01','max_pages':8,'max_seconds':90,'worker_memory_mb':256,
              'daily_limit':100,'quota_timezone':'Asia/Seoul','credentials_copied':False}
    (PACKAGE/'runtime-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    verify_package(PACKAGE)
    print(json.dumps({'prepared':str(PACKAGE),'files':len(files),'api_calls':0}))


def install():
    verify_package(PACKAGE)
    shutil.copytree(PACKAGE,DEPLOY)
    manifest=verify_package(DEPLOY)
    result={'installed':str(DEPLOY),'files':len(manifest['files']),'original_program_changed':False,
            'g2b_task_changed':False,'credentials_copied':False,'api_calls':0,'scheduled':False}
    (ROOT/'evidence/lofin-deployment-v3.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--install',action='store_true')
    install() if parser.parse_args().install else prepare()
