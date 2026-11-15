"""Build a new reviewed sync runtime without copying credentials or source DBs."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parent
VERSION='5.2.15'
PACKAGE=ROOT/f'gpt-sync-package-v{VERSION}'
DEST=Path('D:/G2B')/f'gpt-site-sync-v{VERSION}'
MODULES=('app_version.py','budget_appropriation_vnext.py','budget_projection_vnext.py','budget_vnext.py',
    'db.py','lofin_vnext_http.py','process_lock.py','receipt_validation.py','runtime_role.py',
    'vnext_collection.py','vnext_live_gate.py','vnext_paging.py','vnext_provenance.py','vnext_response.py',
    'vnext_schema.py','vnext_source_guard.py','vnext_store.py','windows_limits.py')


def verify(root):
    manifest=json.loads((root/'runtime-sha256.json').read_text(encoding='utf-8'))
    if manifest['version']!=VERSION:
        raise ValueError('SYNC_VERSION_MISMATCH')
    for name,expected in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('SYNC_RUNTIME_HASH_MISMATCH')
    return manifest


def prepare():
    PACKAGE.mkdir()
    for name in ('gpt_sync_client.py','gpt_sync_entry.py','web_sync_client.py','collector_status.py'):
        shutil.copy2(ROOT/name,PACKAGE/name)
    (PACKAGE/'lofin_candidate').mkdir()
    for name in MODULES:
        shutil.copy2(ROOT/'lofin_candidate'/name,PACKAGE/'lofin_candidate'/name)
    files={p.relative_to(PACKAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in PACKAGE.rglob('*') if p.is_file()}
    (PACKAGE/'runtime-sha256.json').write_text(json.dumps({'version':VERSION,'files':files},indent=2),encoding='utf-8')
    verify(PACKAGE)


def install():
    if DEST.resolve().parent!=Path('D:/G2B').resolve():
        raise ValueError('SYNC_DEPLOY_PATH_MISMATCH')
    verify(PACKAGE)
    shutil.copytree(PACKAGE,DEST)
    verify(DEST)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--install',action='store_true');args=parser.parse_args()
    install() if args.install else prepare()
    print(json.dumps({'version':VERSION,'path':str(DEST if args.install else PACKAGE),
        'files':22,'credentials_copied':False,'collector_changed':False}))
