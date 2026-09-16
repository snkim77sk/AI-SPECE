"""Decode reviewed text edits, verify, and publish ONLY an isolated repair branch."""
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

PIN = '1bad48bd3199e322cef62fc64300e0ea35e71ea1'
BUNDLE_SHA = 'fb0626d7b4760e83d9704f98e795c68010505961a2d432c4bef4151b112ac5ca'
TARGET = 'fix/g2b-budget-integrity-20260917'
BASE = Path(os.environ['GITHUB_WORKSPACE'])
WORK = Path(os.environ['RUNNER_TEMP']) / 'g2b-verified-source'
OUT = WORK / 'verification'
ALLOWED = set('''.github/workflows/g2b-vnext-canary.yml
.github/workflows/g2b-vnext.yml
analysis_vnext.py
award_projection.py
award_vnext.py
bid_vnext.py
budget_snapshot_vnext.py
budget_vnext.py
contract_projection.py
contract_vnext.py
docs/G2B_BUDGET_INTEGRITY_REPAIR.md
g2b_vnext_canary.py
g2b_vnext_pipeline.py
historical_vnext.py
lofin_vnext_http.py
projection_store_vnext.py
readiness_vnext.py
scripts/g2b_bounded_canary.py
scripts/g2b_verify.py
shopping_vnext.py
tests/conftest.py
tests/test_analysis_vnext.py
tests/test_award_projection.py
tests/test_award_vnext.py
tests/test_bid_vnext.py
tests/test_boundary_revalidation.py
tests/test_budget_repair_vnext.py
tests/test_budget_vnext.py
tests/test_contract_projection.py
tests/test_contract_vnext.py
tests/test_g2b_vnext_canary.py
tests/test_g2b_vnext_pipeline.py
tests/test_historical_vnext.py
tests/test_repair_integrity_vnext.py
tests/test_shopping_vnext.py
vnext_collection.py
vnext_http.py
vnext_paging.py
vnext_response.py
vnext_schema.py
vnext_store.py'''.splitlines())


def git(*args, cwd=WORK):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


def bundle():
    encoded = ''.join((BASE/'repair_bundle'/f'part{i:02d}.txt').read_text().strip() for i in range(6))
    compressed = base64.b64decode(encoded, validate=True)
    dec = lzma.LZMADecompressor()
    data = dec.decompress(compressed, max_length=1_000_000)
    if not dec.eof or dec.unused_data or hashlib.sha256(data).hexdigest() != BUNDLE_SHA:
        raise RuntimeError('bundle integrity/size mismatch')
    updates = json.loads(data)
    if len(updates) != len(ALLOWED) or {u['path'] for u in updates} != ALLOWED:
        raise RuntimeError('unexpected edit paths')
    return data, updates


def write_json(name, obj):
    (OUT/name).write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def refs():
    return git('ls-remote', 'origin', 'refs/heads/main', 'refs/heads/feature/g2b-vnext-foundation')


def prepare():
    data, updates = bundle()
    if WORK.exists():
        raise RuntimeError('worktree already exists')
    subprocess.run(['git', 'worktree', 'add', '--detach', str(WORK), PIN], cwd=BASE, check=True)
    if git('rev-parse', 'HEAD') != PIN:
        raise RuntimeError('source pin mismatch')
    OUT.mkdir()
    write_json('execution.json', {'source_pin': PIN, 'runner_sha': os.environ['GITHUB_SHA'],
                                'publication_target': TARGET, 'main_or_feature_write': False,
                                'remote_refs_before': refs(), 'run_id': os.environ['GITHUB_RUN_ID']})
    subprocess.run(['git', 'archive', '--format=zip', '-o', str(OUT/'baseline_source.zip'), PIN], cwd=WORK, check=True)
    for update in updates:
        p = WORK/update['path']
        old = p.read_bytes() if p.exists() else b''
        if update['old_sha256'] is None:
            if p.exists():
                raise RuntimeError('new file already exists: '+update['path'])
        elif hashlib.sha256(old).hexdigest() != update['old_sha256']:
            raise RuntimeError('old file digest mismatch: '+update['path'])
        lines = old.decode('utf-8').splitlines(keepends=True)
        previous_start = len(lines)+1
        for start, end, text in reversed(update['edits']):
            if not 0 <= start <= end <= len(lines) or end > previous_start:
                raise RuntimeError('invalid edit range')
            lines[start:end] = text.splitlines(keepends=True)
            previous_start = start
        new = ''.join(lines).encode('utf-8')
        if hashlib.sha256(new).hexdigest() != update['new_sha256']:
            raise RuntimeError('new file digest mismatch: '+update['path'])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(new)
    (OUT/'decoded_updates.json').write_bytes(data)
    verify_files(updates)
    git('add', '--', *sorted(ALLOWED))
    patch = subprocess.check_output(['git','diff','--cached','--binary',PIN], cwd=WORK)
    (OUT/'applied.patch').write_bytes(patch)
    git('diff', '--cached', '--check')
    write_json('source_hashes.json', {u['path']:u['new_sha256'] for u in updates})


def verify_files(updates):
    for u in updates:
        if hashlib.sha256((WORK/u['path']).read_bytes()).hexdigest() != u['new_sha256']:
            raise RuntimeError('verified source changed: '+u['path'])


def publish():
    _, updates = bundle()
    verify_files(updates)
    compile_result = json.loads((OUT/'compile.json').read_text())
    suites = ET.parse(OUT/'pytest.xml').getroot()
    if compile_result['errors'] or not compile_result['python'].startswith('3.11.'):
        raise RuntimeError('compile/runtime gate failed')
    cases = list(suites.iter('testcase'))
    if len(cases) != 152 or any(list(c) for c in cases if c.find('failure') is not None or c.find('error') is not None or c.find('skipped') is not None):
        raise RuntimeError('regression gate failed')
    if git('ls-remote','--heads','origin','refs/heads/'+TARGET):
        raise RuntimeError('publication branch exists; refusing overwrite')
    changed = set(git('diff','--cached','--name-only',PIN).splitlines())
    if changed != ALLOWED:
        raise RuntimeError('staged path mismatch')
    git('config','user.name','github-actions[bot]')
    git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    git('commit','-m','fix(g2b): verify budget and collection integrity, retire stale facts')
    tested_sha = git('rev-parse','HEAD')
    if git('rev-parse','HEAD^') != PIN:
        raise RuntimeError('unexpected parent')
    subprocess.run(['git','archive','--format=zip','-o',str(OUT/'repaired_source.zip'),tested_sha],cwd=WORK,check=True)
    result = subprocess.run(['git','push','origin',f'{tested_sha}:refs/heads/{TARGET}'],cwd=WORK,capture_output=True,text=True)
    (OUT/'publication.log').write_text(result.stdout+result.stderr)
    pending_workflows = []
    published_sha = tested_sha
    if result.returncode:
        # Some installations cannot publish workflow edits via GITHUB_TOKEN.
        # Preserve the tested tree in the artifact, publish only source/tests,
        # and leave the two workflow updates to the authorized connector.
        if git('ls-remote','--heads','origin','refs/heads/'+TARGET):
            raise RuntimeError('push failed with existing remote; manual review required')
        pending_workflows = sorted(p for p in ALLOWED if p.startswith('.github/workflows/'))
        git('restore','--source='+PIN,'--staged','--',*pending_workflows)
        tree = git('write-tree')
        published_sha = git('commit-tree',tree,'-p',PIN,'-m','fix(g2b): verified integrity repair; workflow updates pending connector')
        subprocess.run(['git','push','origin',f'{published_sha}:refs/heads/{TARGET}'],cwd=WORK,check=True)
    remote_sha = git('ls-remote','--heads','origin','refs/heads/'+TARGET).split()[0]
    if remote_sha != published_sha:
        raise RuntimeError('remote verification mismatch')
    write_json('publication.json', {'status':'PUBLISHED_ISOLATED_CANDIDATE','branch':TARGET,
                                   'published_sha':published_sha,'tested_tree_sha':tested_sha,
                                   'source_pin':PIN,'tests_passed':len(cases),'tests_failed':0,
                                   'workflow_paths_pending':pending_workflows,'main_or_feature_write':False,
                                   'remote_refs_after':refs(),'integration_hold':True,
                                   'production_and_bulk_hold':True})
    print(json.dumps(json.loads((OUT/'publication.json').read_text()),indent=2))


if __name__ == '__main__':
    {'prepare':prepare,'publish':publish}[sys.argv[1]]()
