"""Reconcile a stopped collector without making or repeating a source request.

Caller must own the live-ledger process lock. OS locks, never stale RUNNING text,
establish exclusivity. A transport attempt is fsynced before urllib is invoked.
"""
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path


CALLS_SCHEMA = '''CREATE TABLE calls(
    id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,source_day TEXT NOT NULL,
    page INTEGER NOT NULL,budget_day TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'RESERVED',
    attempted INTEGER NOT NULL DEFAULT 0,response_received INTEGER NOT NULL DEFAULT 0,
    response_confirmed INTEGER NOT NULL DEFAULT 0,stored INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 0,verified_rows INTEGER NOT NULL DEFAULT 0)'''
RESOLVED_STATES = ('VERIFIED', 'NOT_SENT', 'RECHECK_SUPERSEDED', 'RETRY_SUPERSEDED')


def ensure_schema(conn):
    """One transactional migration; keep the complete original call table."""
    conn.execute('PRAGMA synchronous=FULL')
    conn.execute('BEGIN IMMEDIATE')
    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,day TEXT NOT NULL,state TEXT NOT NULL)')
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='calls'").fetchone()
        if row is None:
            conn.execute(CALLS_SCHEMA)
        elif 'UNIQUE(SOURCE_DAY,PAGE)' in ''.join(row[0].upper().split()):
            conn.execute('ALTER TABLE calls RENAME TO calls_before_restart_v2')
            conn.execute(CALLS_SCHEMA)
            conn.execute('INSERT INTO calls SELECT * FROM calls_before_restart_v2')
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS calls_one_sent_page ON calls(source_day,page) WHERE state!='NOT_SENT'")
        conn.execute('''CREATE TABLE IF NOT EXISTS restart_reconciliations(
            id INTEGER PRIMARY KEY,call_id INTEGER NOT NULL,observed_at_utc TEXT NOT NULL,
            before_json TEXT NOT NULL,outcome TEXT NOT NULL,readback_json TEXT NOT NULL)''')
    from delayed_retry import ensure_schema as retry_schema
    retry_schema(conn, 'g2b')


def unresolved(conn, source_day=None):
    validate_manual_rechecks(conn)
    from delayed_retry import validate_archives
    validate_archives(conn)
    sql="SELECT 1 FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RECHECK_SUPERSEDED','RETRY_SUPERSEDED')"
    params=()
    if source_day is not None:
        sql+=' AND source_day=?';params=(source_day,)
    return conn.execute(sql+' LIMIT 1',params).fetchone() is not None


def unresolved_days(conn):
    validate_manual_rechecks(conn)
    from delayed_retry import validate_archives
    validate_archives(conn)
    return [row[0] for row in conn.execute("SELECT DISTINCT source_day FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RECHECK_SUPERSEDED','RETRY_SUPERSEDED') ORDER BY source_day DESC")]


def validate_manual_rechecks(conn):
    """A superseded attempt is resolved only by a linked verified manual call.

    Keep the original attempt/response flags unchanged for honest quota/accounting.
    Only the user's explicit Nov 14 page 1 recheck can use this resolution path.
    """
    rows = conn.execute("SELECT * FROM calls WHERE state='RECHECK_SUPERSEDED'").fetchall()
    for row in rows:
        original = dict(row)
        try:
            record = conn.execute('SELECT * FROM manual_rechecks WHERE original_id=?', (row['id'],)).fetchone()
            later = conn.execute('SELECT * FROM calls WHERE id=?', (record['recheck_id'],)).fetchone()
            before = json.loads(record['original_json'])
            receipt = json.loads(record['readback_json'])
            valid = (record['authorization'] == 'USER_20261115_SCHEMA_RECHECK'
                and record['state'] == 'VERIFIED' and later['state'] == 'VERIFIED'
                and row['source_day'] == later['source_day'] == '2026-11-14'
                and row['page'] == later['page'] == 1
                and all(later[k] == 1 for k in ('attempted','response_received','response_confirmed','stored','verified'))
                and all(original[k] == v for k, v in before.items() if k != 'state')
                and before['state'] == 'UNCERTAIN' and receipt['verified']
                and receipt['next_page'] == 2 and receipt['page_rows'] == later['verified_rows'])
        except (TypeError, KeyError, ValueError, sqlite3.Error):
            valid = False
        if not valid:
            raise ValueError('MANUAL_RECHECK_RESOLUTION_INVALID')


def recover_local_journal(path):
    """Caller owns this new daily DB's lock; never call for an original DB."""
    if Path(str(path)+'-journal').exists() or Path(str(path)+'-wal').exists():
        # Let SQLite recover its own interrupted transaction under the day lock.
        # Never remove journal files, rebuild a DB, or touch the original DB.
        with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True,timeout=1)) as recovery:
            # Schema access triggers SQLite's own journal recovery. Validate
            # the exact checkpoint, receipts and RAW links immediately after;
            # scanning every unrelated payload here can exhaust a whole run.
            recovery.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()


def _checkpoint(path, day):
    from receipt_validation import readback
    if not path.exists():
        return None
    recover_local_journal(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as source:
        source.execute('PRAGMA query_only=ON')
        if not source.execute("SELECT 1 FROM sqlite_master WHERE name='collection_checkpoints'").fetchone():
            # Creation may have stopped before the collector initialized its schema.
            return None
    return readback(path, day)


def reconcile(conn, root, *, held_day, finalize_runs=True, reuse_unchanged=False):
    """Recover committed pages, or release reservations proven never attempted.

    Responses lacking a verified committed receipt stay held, including after
    midnight. No function in this module reads credentials or opens the network.
    """
    from process_lock import LocalDbProcessLock
    root = Path(root).resolve()
    validate_manual_rechecks(conn)
    pending = [dict(row) for row in conn.execute(
        "SELECT * FROM calls WHERE state NOT IN ('VERIFIED','NOT_SENT','RECHECK_SUPERSEDED','RETRY_SUPERSEDED') ORDER BY id")]
    outcomes = []
    for call in pending:
        day = call['source_day']
        outcome, receipt = 'HOLD_UNCERTAIN_NO_RETRY', None
        try:
            if date.fromisoformat(day).isoformat() != day or day < '2026-01-01' or day == held_day:
                raise ValueError('HELD_OR_INVALID_DAY')
            path = root/f'g2b-real-{day}.sqlite3'
            with LocalDbProcessLock(path):
                if reuse_unchanged and path.exists():
                    from backfill_queue import validated_snapshot
                    recover_local_journal(path)
                    receipt=validated_snapshot(conn,path,lambda:_checkpoint(path,day),reuse_unchanged=True)
                else:
                    receipt = _checkpoint(path, day)
                flags = tuple(call[name] for name in ('attempted','response_received','response_confirmed','stored','verified'))
                if (call['state'] in ('RESERVED','UNCERTAIN') and flags == (0,0,0,0,0)
                        and ((receipt is None and call['page'] == 1)
                            or (receipt and receipt['verified'] and receipt['status'] in ('RUNNING','FAILED') and receipt['next_page'] == call['page']))):
                    if receipt and receipt['status']=='FAILED':
                        with closing(sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=1)) as local:
                            before=local.execute('SELECT status,last_error FROM collection_checkpoints WHERE dataset=? AND scope_key=?',
                                ('shopping_delivery',day+':'+day)).fetchone()
                            receipt['checkpoint_before']={'status':before[0],'last_error':before[1]}
                            with local:
                                changed=local.execute("UPDATE collection_checkpoints SET status='RUNNING',last_error='' WHERE dataset=? AND scope_key=? AND page_no=? AND saved_count=? AND status='FAILED'",
                                    ('shopping_delivery',day+':'+day,call['page'],receipt['saved'])).rowcount
                                if changed!=1:raise ValueError('UNSENT_CHECKPOINT_CHANGED')
                        receipt['status']='RUNNING'
                    outcome = 'NOT_SENT'
                elif (call['state'] != 'RECHECK_PENDING' and flags[:3] == (1,1,1) and receipt and receipt['verified']
                        and receipt['status'] in ('RUNNING','COMPLETE')
                        and receipt['next_page'] == call['page']+1):
                    outcome = 'VERIFIED_FROM_COMMIT'
                with conn:
                    if outcome == 'NOT_SENT':
                        conn.execute("UPDATE calls SET state='NOT_SENT' WHERE id=?", (call['id'],))
                    elif outcome == 'VERIFIED_FROM_COMMIT':
                        conn.execute("UPDATE calls SET state='VERIFIED',stored=1,verified=1,verified_rows=? WHERE id=?",
                                     (receipt['page_rows'],call['id']))
                    _record(conn, call, outcome, receipt)
        except (ValueError, sqlite3.Error, OSError, RuntimeError) as exc:
            # A corrupt DB or an active per-day writer is never a reason to retry.
            outcome = 'HOLD_'+type(exc).__name__.upper()
            with conn:
                _record(conn, call, outcome, None)
        outcomes.append({'call_id':call['id'],'source_day':day,'page':call['page'],'outcome':outcome})
    if finalize_runs:
        with conn:
            for run in conn.execute("SELECT id FROM runs WHERE state='RUNNING'").fetchall():
                held = conn.execute("SELECT 1 FROM calls WHERE run_id=? AND state NOT IN ('VERIFIED','NOT_SENT','RECHECK_SUPERSEDED','RETRY_SUPERSEDED')", (run[0],)).fetchone()
                conn.execute('UPDATE runs SET state=? WHERE id=?',
                             ('INTERRUPTED_HELD' if held else 'INTERRUPTED_RECONCILED',run[0]))
    return {'network_calls':0,'outcomes':outcomes,'unresolved':unresolved(conn),'held_days':unresolved_days(conn)}


def _record(conn, call, outcome, receipt):
    conn.execute('INSERT INTO restart_reconciliations(call_id,observed_at_utc,before_json,outcome,readback_json) VALUES(?,?,?,?,?)',
                 (call['id'],datetime.now(timezone.utc).isoformat(),json.dumps(call,sort_keys=True),outcome,json.dumps(receipt)))
