"""Compile repository Python and run every test, without source-network access."""
import datetime
import json
from pathlib import Path
import py_compile
import subprocess
import os
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'verification'
OUT.mkdir(exist_ok=True)
files = sorted([*ROOT.glob('*.py'), *(ROOT/'tests').rglob('*.py'), *(ROOT/'scripts').glob('g2b_*.py')])
errors = []
for path in files:
    try:
        py_compile.compile(str(path), doraise=True)
    except py_compile.PyCompileError as exc:
        errors.append({'path': str(path.relative_to(ROOT)), 'error': str(exc)})
compile_result = {'python': sys.version, 'files': len(files), 'errors': errors,
                  'generated_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
(OUT/'compile.json').write_text(json.dumps(compile_result, indent=2, ensure_ascii=False)+'\n')
with tempfile.TemporaryDirectory(prefix='g2b-regression-') as temp, (OUT/'pytest.log').open('w', encoding='utf-8') as log:
    env = dict(os.environ, G2B_DB_PATH=str(Path(temp)/'collection.sqlite3'), G2B_SERVICE_KEY='', LOFIN_API_KEY='', G2B_AUTO_SYNC='0')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '--tb=short',
                             '--junitxml='+str(OUT/'pytest.xml'), 'tests'], cwd=ROOT,
                            stdout=log, stderr=subprocess.STDOUT, env=env)
print((OUT/'pytest.log').read_text())
print(json.dumps(compile_result, ensure_ascii=False))
raise SystemExit(1 if errors or result.returncode else 0)
