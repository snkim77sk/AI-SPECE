"""Read-only collector/quota telemetry. Never imports or invokes a source client."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess

PROTOCOL = 'windows-collector-status-v1'
KST = timezone(timedelta(hours=9))
SOURCES = {
    'g2b': ('live-bounded-20261114', 'live-calls.sqlite3', 'automatic-status.json'),
    'lofin': ('lofin-collection', 'lofin-calls.sqlite3', 'lofin-status.json'),
}
CODES = set('VERIFIED FINISHED PAGE_LIMIT DEADLINE_MARGIN NO_READY_SCOPES LOCAL_QUOTA_WAIT DAILY_QUOTA_WAIT_FOR_KST_RESET BUDGET_EXHAUSTED TIMEOUT_NO_RETRY MEMORY_LIMIT_REACHED RUN_ENDED_WITHOUT_RESULT RUN_RESULT_PENDING STATUS_UNAVAILABLE ERROR_RECHECK_NEXT_SCHEDULE CONTINUING_WITH_SCOPE_HOLD ORIGINAL_UNCERTAIN_PAGE_HOLD UNCERTAIN_SCOPE_NO_AUTOMATIC_RETRY UNCERTAIN_NO_RETRY REPEATED_OR_OVERLAPPING_PAGE SOURCE_SCHEMA_ERROR SOURCE_NETWORK_OR_TIMEOUT'.split())
CODES.add('SOURCE_RESPONSE_ERROR')


def stamp(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except (ValueError, TypeError, AttributeError):
        return None


def read_json(path):
    try:
        if path.stat().st_size > 262144:
            return {}
        result = json.loads(path.read_text(encoding='utf-8-sig'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def code(value):
    if value == 'VNextResponseError':
        return 'SOURCE_RESPONSE_ERROR'
    return value if isinstance(value, str) and value in CODES else 'STATUS_UNAVAILABLE'


def read_windows_tasks():
    if os.name != 'nt':
        return {}
    # Only fixed task names and non-secret result/timing fields leave PowerShell.
    script = r"""
$ErrorActionPreference='Stop'
@('G2B-Bounded-Backfill-20260101','G2B-Lofin365-Budget-2026') | ForEach-Object {
  $task=Get-ScheduledTask -TaskName $_
  $info=Get-ScheduledTaskInfo -TaskName $_
  [PSCustomObject]@{
    source=if($_ -eq 'G2B-Bounded-Backfill-20260101'){'g2b'}else{'lofin'}
    state=$task.State.ToString()
    enabled=[bool]$task.Settings.Enabled
    last_run_at=$info.LastRunTime.ToUniversalTime().ToString('o')
    next_run_at=$info.NextRunTime.ToUniversalTime().ToString('o')
    last_result=[long]$info.LastTaskResult
  }
} | ConvertTo-Json -Compress
"""
    try:
        exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        run = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command', script],
                             capture_output=True, timeout=5, check=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if len(run.stdout) > 16384:
            return {}
        rows = json.loads(run.stdout.decode('utf-8-sig'))
        return {r['source']: {k: stamp(r[k]) if k.endswith('_at') else r[k]
                             for k in ['state', 'enabled', 'last_run_at', 'next_run_at', 'last_result']}
                for r in rows if r.get('source') in SOURCES}
    except (OSError, ValueError, subprocess.SubprocessError, KeyError):
        return {}


def source_status(root, source, now, task):
    directory, ledger_name, status_name = SOURCES[source]
    folder = root / directory
    status = read_json(folder / status_name)
    run_id = status.get('run_id', '')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        run_id = None
    run_dir = folder / run_id if run_id else None
    plan = read_json(run_dir / 'plan.json') if run_dir else {}
    result = read_json(run_dir / 'result.json') if run_dir else {}
    observed = stamp(status.get('observed_at_kst') or status.get('finished_at_kst') or status.get('started_at_kst'))
    limit = (plan.get('effective_day_budget') if plan.get('budget_day') == now.date().isoformat()
             else plan.get('configured_daily_ceiling')) if source == 'g2b' else plan.get('daily_limit')
    if type(limit) is not int or not 1 <= limit <= 10000:
        limit = None
    item = dict(source=source, available=False, limit=limit, counted=None, attempted=None,
                pending=None, confirmed=None, stored=None, verified=None, verified_rows=None,
                official_remaining=None, task=task, last_run=None, held=[], progress=[])
    try:
        with closing(sqlite3.connect((folder / ledger_name).resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.execute('PRAGMA query_only=ON')
            db.row_factory = sqlite3.Row
            row = db.execute("""SELECT
                COALESCE(SUM(state!='NOT_SENT'),0) counted,
                COALESCE(SUM(attempted),0) attempted,
                COALESCE(SUM(response_confirmed),0) confirmed,
                COALESCE(SUM(stored),0) stored,
                COALESCE(SUM(verified),0) verified,
                COALESCE(SUM(verified_rows),0) verified_rows
                FROM calls WHERE budget_day=?""", (now.date().isoformat(),)).fetchone()
            item.update(dict(row), available=True, pending=max(0, row['counted']-row['attempted']))
            counts = db.execute("""SELECT COALESCE(SUM(attempted),0) attempted,
                COALESCE(SUM(stored),0) stored,COALESCE(SUM(verified),0) verified
                FROM calls WHERE run_id=?""", (run_id,)).fetchone()
            why = code(result.get('stop_reason') or result.get('supervisor_outcome') or result.get('run_state') or status.get('state'))
            worker = read_json(run_dir / 'worker-result.json') if run_dir else {}
            if result.get('stop_reason') == 'VNextResponseError' and worker.get('safe_error_code') == 'SCHEMA':
                why = 'SOURCE_SCHEMA_ERROR'
            if not result:
                # A saved RUNNING string is never proof that a process is alive.
                why = ('RUN_RESULT_PENDING' if task and task.get('state') == 'Running'
                       else 'RUN_ENDED_WITHOUT_RESULT' if run_id and task and task.get('state') in ('Ready','Disabled')
                       else 'STATUS_UNAVAILABLE')
            item['last_run'] = dict(run_id=run_id, reported_at=observed,
                                    finished_at=stamp(result.get('finished_at_kst')),
                                    reason=why, result_complete=bool(result), **dict(counts))
            if source == 'g2b':
                for row in db.execute("SELECT day,state,next_page,saved,reason FROM backfill_days WHERE state='HOLD' ORDER BY day DESC LIMIT 12"):
                    item['held'].append(dict(scope=row['day'], next_page=row['next_page'], saved=row['saved'], reason=code(row['reason'])))
                for row in db.execute("SELECT day,next_page,saved FROM backfill_days WHERE state='READY' ORDER BY day DESC LIMIT 1"):
                    item['progress'].append(dict(scope=row['day'], next_page=row['next_page'], saved=row['saved']))
            else:
                for row in db.execute("SELECT service,snapshot,MIN(page) page FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT') GROUP BY service,snapshot ORDER BY snapshot DESC LIMIT 12"):
                    item['held'].append(dict(scope=row['service']+':'+row['snapshot'], next_page=row['page'], saved=None, reason='UNCERTAIN_NO_RETRY'))
                for service in ['QWGJK', 'AIDFA']:
                    row = db.execute('SELECT service,snapshot,next_page,saved FROM jobs WHERE service=? ORDER BY snapshot DESC LIMIT 1', (service,)).fetchone()
                    if row:
                        item['progress'].append(dict(scope=row['service']+':'+row['snapshot'], next_page=row['next_page'], saved=row['saved']))
    except (OSError, sqlite3.Error):
        # Missing/unreadable ledgers are unknown, not zero usage.
        item.update(available=False, counted=None, attempted=None, pending=None,
                    confirmed=None, stored=None, verified=None, verified_rows=None,
                    last_run=None, held=[], progress=[])
    return item


def collect_status(root=Path('D:/G2B'), now=None, task_reader=read_windows_tasks):
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        raise ValueError('AWARE_CLOCK_REQUIRED')
    now = now.astimezone(KST)
    tasks = task_reader()
    return dict(protocol=PROTOCOL, version='5.2.8',
                observed_at=stamp(now.isoformat()), day=now.date().isoformat(),
                reset_at=stamp((now+timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()),
                sources=[source_status(Path(root), source, now, tasks.get(source)) for source in SOURCES])
