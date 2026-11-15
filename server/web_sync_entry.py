"""One bounded Windows scheduled transfer, independent of both collectors."""
import json
import subprocess
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'lofin_candidate'))
from process_lock import LocalDbProcessLock
from windows_limits import WindowsJob


def main():
    started = time.monotonic()
    from web_sync_client import load_config, DEFAULT_CONFIG
    config = load_config(DEFAULT_CONFIG)
    state = Path(config['state_directory']); state.mkdir(parents=True, exist_ok=True)
    with LocalDbProcessLock(state / 'entry'):
        with (state / 'last-entry.log').open('w', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, '-B', str(ROOT / 'web_sync_client.py'), '--controlled'],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                with WindowsJob(child, memory_mb=256):
                    child.stdin.write(b'GO\n'); child.stdin.flush(); child.stdin.close()
                    return child.wait(timeout=max(1, 88-(time.monotonic()-started)))
            except BaseException:
                if child.poll() is None: child.kill()
                child.wait(timeout=2)
                raise


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'state': 'SYNC_ENTRY_FAILED', 'reason': type(exc).__name__}))
        raise SystemExit(1) from None
