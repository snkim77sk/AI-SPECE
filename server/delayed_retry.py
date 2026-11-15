"""User-authorized delayed retries of read-only source GETs, with durable history.

No network or credentials here. Callers own the ledger and checkpoint OS locks.
Every replay gets a NEW counted call; old attempt/response/store flags survive.
"""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

AUTHORIZATION = 'USER_20261115_DELAYED_RESTART'
POLICY = {'authorization': AUTHORIZATION, 'delays_minutes': [15, 30, 60],
          'max_retries_per_run': 1}
ARCHIVED = 'RETRY_SUPERSEDED'
KST = timezone(timedelta(hours=9))


def utc(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('RETRY_TIMEZONE_REQUIRED')
    return value.astimezone(timezone.utc)


def stamp(value):
    return utc(value).isoformat(timespec='seconds')


def enabled(plan):
    return plan.get('delayed_retry') == POLICY


def ensure_schema(conn, source):
    index, fields, excluded = (
        ('calls_one_sent_page', 'source_day,page', "'NOT_SENT','RECHECK_PENDING','RECHECK_SUPERSEDED','RETRY_SUPERSEDED'")
        if source == 'g2b' else ('one_sent_page', 'service,snapshot,page', "'NOT_SENT','RETRY_SUPERSEDED'"))
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS delayed_retries(
            call_id INTEGER PRIMARY KEY, failure_count INTEGER NOT NULL,
            failed_at_utc TEXT NOT NULL, next_retry_at_utc TEXT NOT NULL,
            reason TEXT NOT NULL, state TEXT NOT NULL,
            replacement_id INTEGER UNIQUE, authorization TEXT NOT NULL,
            original_json TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS delayed_checkpoint_restarts(
            call_id INTEGER PRIMARY KEY, before_json TEXT NOT NULL, observed_at_utc TEXT NOT NULL)''')
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index,)).fetchone()
        if not row or 'RETRY_SUPERSEDED' not in row[0]:
            conn.execute('DROP INDEX IF EXISTS ' + index)
            conn.execute(f'CREATE UNIQUE INDEX {index} ON calls({fields}) WHERE state NOT IN ({excluded})')


def validate_archives(conn):
    rows = conn.execute("SELECT * FROM calls WHERE state='RETRY_SUPERSEDED'").fetchall()
    for row in rows:
        original = dict(row)
        try:
            entry = conn.execute('SELECT * FROM delayed_retries WHERE call_id=?', (row['id'],)).fetchone()
            later = conn.execute('SELECT * FROM calls WHERE id=?', (entry['replacement_id'],)).fetchone()
            before = json.loads(entry['original_json'])
            keys = ('source_day', 'page') if 'source_day' in original else ('service', 'snapshot', 'page')
            valid = (entry['authorization'] == AUTHORIZATION and entry['state'] == 'REPLACED'
                     and later['id'] > row['id'] and later['run_id'] != row['run_id']
                     and all(original[k] == later[k] for k in keys)
                     and all(original[k] == v for k, v in before.items() if k != 'state')
                     and before['state'] in ('UNCERTAIN', 'RESERVED'))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError('RETRY_HISTORY_INVALID')


def safe_reason(reason):
    return reason if isinstance(reason, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', reason) else 'SOURCE_ERROR'


def enqueue(conn, call_id, *, failed_at=None, reason='SOURCE_ERROR', clock=None):
    clock = utc(clock)
    call = conn.execute('SELECT * FROM calls WHERE id=?', (call_id,)).fetchone()
    if not call or call['state'] not in ('UNCERTAIN', 'RESERVED') or call['attempted'] != 1:
        return
    if conn.execute('SELECT 1 FROM delayed_retries WHERE call_id=?', (call_id,)).fetchone():
        return
    prior = conn.execute('SELECT failure_count FROM delayed_retries WHERE replacement_id=?', (call_id,)).fetchone()
    count = prior[0] + 1 if prior else 1
    failed = min(utc(failed_at) if failed_at else clock, clock)
    retry_at = failed + timedelta(minutes=(15, 30, 60)[min(count - 1, 2)])
    reason = safe_reason(reason)
    blocked = reason in ('HTTP_401', 'HTTP_403', 'HTTP_302', '20', '30', '31', '32', '33')
    if reason in ('HTTP_429', '22', 'LOCAL_QUOTA_WAIT', 'BUDGET_EXHAUSTED'):
        reset = (clock.astimezone(KST) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        retry_at = max(retry_at, utc(reset))
    with conn:
        conn.execute('INSERT INTO delayed_retries VALUES(?,?,?,?,?,?,NULL,?,?)',
                     (call_id, count, stamp(failed), stamp(retry_at), reason,
                      'ACCESS_REVIEW' if blocked else 'WAITING', AUTHORIZATION,
                      json.dumps(dict(call), sort_keys=True)))


def arm_pending(conn, root, *, clock=None):
    """Import existing operational failures, never resetting their elapsed delay."""
    clock = utc(clock)
    for row in conn.execute("SELECT * FROM calls WHERE state IN ('UNCERTAIN','RESERVED') AND attempted=1").fetchall():
        run_id = row['run_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
            continue
        run = Path(root) / run_id
        failed, reason = clock, 'INTERRUPTED_REQUEST'
        for name in ('plan.json', 'result.json', 'worker-result.json'):
            try:
                path = run / name
                if path.stat().st_size > 256 * 1024:
                    continue
                value = json.loads(path.read_text(encoding='utf-8'))
                when = value.get('finished_at_kst') or value.get('started_at_kst')
                if when:
                    failed = utc(when) + (timedelta(seconds=90) if name == 'plan.json' else timedelta())
                reason = safe_reason(value.get('safe_error_code') or value.get('stop_reason') or reason)
            except (OSError, ValueError, TypeError):
                continue
        enqueue(conn, row['id'], failed_at=failed, reason=reason, clock=clock)
    with conn:
        conn.execute("UPDATE delayed_retries SET state='RESOLVED' WHERE state='WAITING' AND call_id IN (SELECT id FROM calls WHERE state IN ('VERIFIED','NOT_SENT'))")


def next_due(conn, clock=None):
    validate_archives(conn)
    row = conn.execute('''SELECT c.* FROM delayed_retries r JOIN calls c ON c.id=r.call_id
        WHERE r.state='WAITING' AND r.authorization=? AND r.next_retry_at_utc<=?
          AND c.state IN ('UNCERTAIN','RESERVED') AND c.attempted=1
        ORDER BY r.next_retry_at_utc,c.id LIMIT 1''', (AUTHORIZATION, stamp(clock or utc()))).fetchone()
    return dict(row) if row else None


def reserve_replacement(conn, original_id, *, run_id, budget_day, fields, clock=None):
    """Inside caller's quota-checked transaction; checkpoint already verified."""
    original = conn.execute('SELECT * FROM calls WHERE id=?', (original_id,)).fetchone()
    entry = conn.execute('SELECT * FROM delayed_retries WHERE call_id=?', (original_id,)).fetchone()
    if (not original or not entry or entry['state'] != 'WAITING'
            or entry['authorization'] != AUTHORIZATION or utc(entry['next_retry_at_utc']) > utc(clock)
            or original['run_id'] == run_id or original['state'] not in ('UNCERTAIN', 'RESERVED')
            or any(original[k] != v for k, v in fields.items())):
        raise ValueError('RETRY_NOT_DUE_OR_SCOPE_CHANGED')
    if conn.execute('SELECT 1 FROM delayed_retries r JOIN calls c ON c.id=r.replacement_id WHERE c.run_id=?', (run_id,)).fetchone():
        raise ValueError('ONE_DELAYED_RETRY_PER_RUN')
    if any(dict(original)[k] != v for k, v in json.loads(entry['original_json']).items()):
        raise ValueError('RETRY_ORIGINAL_CHANGED')
    conn.execute("UPDATE calls SET state='RETRY_SUPERSEDED' WHERE id=?", (original_id,))
    names = ['run_id', 'budget_day', *fields]
    new_id = conn.execute('INSERT INTO calls(' + ','.join(names) + ') VALUES(' + ','.join('?' for _ in names) + ')',
                          (run_id, budget_day, *fields.values())).lastrowid
    conn.execute("UPDATE delayed_retries SET state='REPLACED',replacement_id=? WHERE call_id=?", (new_id, original_id))
    return new_id


def checkpoint_matches(receipt, page):
    return bool(receipt and receipt['verified'] and not receipt['complete']
                and receipt['status'] in ('RUNNING', 'FAILED', 'INCOMPLETE') and receipt['next_page'] == page)


def restore_failed_checkpoint(conn, call_id, path, dataset, scope, receipt):
    """Preserve the failed checkpoint in the ledger before changing status only."""
    if receipt['status'] not in ('FAILED', 'INCOMPLETE'):
        return
    with closing(sqlite3.connect(Path(path).as_uri()+'?mode=rw', uri=True, timeout=1)) as local:
        local.row_factory = sqlite3.Row
        before = local.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?', (dataset, scope)).fetchone()
        if (not before or before['status'] != receipt['status'] or before['page_no'] != receipt['next_page']
                or before['saved_count'] != receipt['saved'] or before['fetched_count'] != receipt['saved']):
            raise ValueError('RETRY_CHECKPOINT_CHANGED')
        with conn:
            conn.execute('INSERT INTO delayed_checkpoint_restarts VALUES(?,?,?)',
                         (call_id, json.dumps(dict(before), sort_keys=True), stamp(utc())))
        with local:
            changed = local.execute("UPDATE collection_checkpoints SET status='RUNNING',last_error='' WHERE dataset=? AND scope_key=? AND status=? AND page_no=? AND saved_count=? AND cursor_value=?",
                (dataset, scope, before['status'], before['page_no'], before['saved_count'], before['cursor_value'])).rowcount
            if changed != 1:
                raise ValueError('RETRY_CHECKPOINT_CHANGED')


def defer_local_validation(conn, call_id, clock=None):
    with conn:
        conn.execute("UPDATE delayed_retries SET next_retry_at_utc=?,reason='LOCAL_VALIDATION_WAIT' WHERE call_id=? AND state='WAITING'",
                     (stamp(utc(clock)+timedelta(minutes=60)), call_id))
