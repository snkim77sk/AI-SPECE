"""Additive, bounded smartLED goods-contract queue and current snapshot store.

Schema creation is bootstrap-only. Reads never run DDL or external source calls.
Callers must use the existing single admitted source worker and pass the queue
revision they fetched when committing a refresh. Queue changes invalidate stale
refreshes instead of silently clearing a newer change/deletion notification.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from db import backend_name, connect
from smartled_contract_vnext import MAX_SNAPSHOT_BYTES, START_DATE

MAX_PENDING = 200
REASONS = frozenset({'NEW', 'CHANGE', 'DELETE', 'RECHECK'})
KINDS = {'FIXTURE': 'has_fixture', 'LAMP': 'has_lamp', 'CONTROL': 'has_control'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS smartled_contract_control (
    name TEXT PRIMARY KEY, value BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS smartled_contract_queue (
    unified_no TEXT PRIMARY KEY,
    revision BIGINT NOT NULL DEFAULT 1,
    reasons TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    queued_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_smartled_contract_queue_age
    ON smartled_contract_queue(queued_at,unified_no);
CREATE TABLE IF NOT EXISTS smartled_contract_snapshots (
    unified_no TEXT PRIMARY KEY,
    decided_no TEXT NOT NULL DEFAULT '',
    contract_date TEXT NOT NULL DEFAULT '',
    contract_name TEXT NOT NULL DEFAULT '',
    source_state TEXT NOT NULL,
    source_deleted_at TEXT NOT NULL DEFAULT '',
    contract_amount TEXT,
    fixture_amount TEXT,
    lamp_amount TEXT,
    control_amount TEXT,
    has_fixture INTEGER NOT NULL DEFAULT 0,
    has_lamp INTEGER NOT NULL DEFAULT 0,
    has_control INTEGER NOT NULL DEFAULT 0,
    vendors_json TEXT NOT NULL DEFAULT '[]',
    agencies_json TEXT NOT NULL DEFAULT '[]',
    source_payload_sha256 TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    items_summary_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_smartled_contract_current_date
    ON smartled_contract_snapshots(source_state,contract_date,unified_no);
'''


class QueueFullError(RuntimeError):
    pass


class StaleRefreshError(RuntimeError):
    pass


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _key(value):
    value = str(value or '').strip()
    if not value or len(value) > 128:
        raise ValueError('SMARTLED_CONTRACT_KEY_INVALID')
    return value


def ensure_schema():
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO smartled_contract_control(name,value) VALUES('queue',0) "
                     "ON CONFLICT(name) DO NOTHING")


@contextmanager
def _write():
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        # SQLite's writer transaction / PostgreSQL's row lock serializes cap
        # checks, queue revisions and current-snapshot replacement atomically.
        conn.execute("UPDATE smartled_contract_control SET value=value WHERE name='queue'")
        yield conn


def enqueue(unified_no, reason='NEW', *, _conn=None):
    if isinstance(unified_no, (list, tuple)):
        if len(unified_no) > 100:
            raise ValueError('SMARTLED_CONTRACT_SCAN_PAGE_TOO_LARGE')
        if _conn is not None:
            return {key: enqueue(key, reason, _conn=_conn) for key in dict.fromkeys(unified_no)}
        with _write() as conn:
            return {key: enqueue(key, reason, _conn=conn) for key in dict.fromkeys(unified_no)}
    key = _key(unified_no)
    if reason not in REASONS:
        raise ValueError('SMARTLED_CONTRACT_REASON_INVALID')
    def perform(conn):
        row = conn.execute('SELECT revision,reasons,attempts,updated_at FROM smartled_contract_queue WHERE unified_no=?', (key,)).fetchone()
        now = _now()
        conn.execute("UPDATE smartled_contract_control SET value=value+1 WHERE name='queue'")
        revision = int(conn.execute("SELECT value FROM smartled_contract_control WHERE name='queue'").fetchone()['value'])
        if row:
            reasons = set(str(row['reasons']).split(',')) | {reason}
            retry = (int(row['attempts']) >= 3 and
                     dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(row['updated_at']) >= dt.timedelta(hours=24))
            # Preserve failure time on repeated same-day observations so the
            # bounded daily retry cannot be postponed indefinitely by scanning.
            updated = row['updated_at'] if int(row['attempts']) >= 3 and not retry else now
            conn.execute('UPDATE smartled_contract_queue SET revision=?,reasons=?,updated_at=?,attempts=? WHERE unified_no=?',
                         (revision, ','.join(sorted(reasons)), updated, 0 if retry else row['attempts'], key))
        else:
            count = conn.execute('SELECT COUNT(*) AS n FROM smartled_contract_queue').fetchone()['n']
            if int(count) >= MAX_PENDING:
                raise QueueFullError('SMARTLED_CONTRACT_QUEUE_FULL')
            conn.execute('INSERT INTO smartled_contract_queue(unified_no,revision,reasons,queued_at,updated_at) VALUES(?,?,?,?,?)',
                         (key, revision, reason, now, now))
        return revision
    if _conn is not None:
        # For atomic ingestion-page + cursor transactions owned by the caller.
        _conn.execute("UPDATE smartled_contract_control SET value=value WHERE name='queue'")
        return perform(_conn)
    with _write() as conn:
        return perform(conn)


def enqueue_page_and_cursor(unified_nos, setting_key, cursor_json, *, reason='NEW'):
    """Queue a scanner page and advance its cursor in the same transaction.

    If the 200-job cap is reached, the whole page/cursor transaction rolls back.
    Retries are therefore safe and never drop the remaining IDs from a page.
    """
    keys = list(unified_nos)
    if len(keys) > 100:
        raise ValueError('SMARTLED_CONTRACT_SCAN_PAGE_TOO_LARGE')
    if not str(setting_key).startswith('smartled_contract_'):
        raise ValueError('SMARTLED_CONTRACT_CURSOR_KEY_INVALID')
    if not isinstance(cursor_json, str) or len(cursor_json.encode('utf-8')) > 16384:
        raise ValueError('SMARTLED_CONTRACT_CURSOR_BYTES_EXCEEDED')
    json.loads(cursor_json)
    with _write() as conn:
        revisions = {key: enqueue(key, reason, _conn=conn) for key in dict.fromkeys(keys)}
        conn.execute('INSERT INTO app_settings(key,value) VALUES(?,?) '
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (setting_key, cursor_json))
    return revisions


def next_pending():
    """Read one item without removing it; failures leave the exact job resumable."""
    with connect() as conn:
        row = conn.execute('SELECT unified_no,revision,reasons,queued_at,attempts,last_error FROM smartled_contract_queue '
                           'WHERE attempts<3 ORDER BY queued_at,unified_no LIMIT 1').fetchone()
        return dict(row) if row else None


def contains(unified_no):
    key = _key(unified_no)
    with connect() as conn:
        return bool(conn.execute('SELECT unified_no FROM smartled_contract_snapshots WHERE unified_no=?', (key,)).fetchone()
                    or conn.execute('SELECT unified_no FROM smartled_contract_queue WHERE unified_no=?', (key,)).fetchone())


def mark_failed(unified_no, error_type, *, queue_revision=None):
    """Preserve blocked jobs after three failures; never silently discard work.

    Re-enqueuing the same source ID within 24 hours does not reset attempts.
    A later source observation permits one bounded retry cycle after 24 hours.
    """
    key = _key(unified_no)
    error = str(error_type or 'UNKNOWN')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,80}', error):
        error = 'UNCLASSIFIED_ERROR'
    with _write() as conn:
        _assert_revision(conn, key, queue_revision)
        conn.execute('UPDATE smartled_contract_queue SET attempts=CASE WHEN attempts<3 THEN attempts+1 ELSE attempts END,'
                     'last_error=?,updated_at=? WHERE unified_no=?', (error, _now(), key))


def queue_status():
    with connect() as conn:
        row = conn.execute('SELECT COUNT(*) AS pending, '
            'COALESCE(SUM(CASE WHEN attempts>=3 THEN 1 ELSE 0 END),0) AS blocked FROM smartled_contract_queue').fetchone()
        return dict(row)


def _assert_revision(conn, key, revision):
    if revision is None:
        return
    row = conn.execute('SELECT revision FROM smartled_contract_queue WHERE unified_no=?', (key,)).fetchone()
    if not row or int(row['revision']) != int(revision):
        raise StaleRefreshError('SMARTLED_CONTRACT_REFRESH_STALE')


def _finish(conn, key, revision):
    if revision is not None:
        conn.execute('DELETE FROM smartled_contract_queue WHERE unified_no=? AND revision=?', (key, int(revision)))


def _json(value, limit=MAX_SNAPSHOT_BYTES):
    # Incremental encoding bounds the additional serialized allocation.
    chunks, size = [], 0
    for piece in json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':'), allow_nan=False).iterencode(value):
        size += len(piece.encode('utf-8'))
        if size > limit:
            raise ValueError('SMARTLED_CONTRACT_STORE_BYTES_EXCEEDED')
        chunks.append(piece)
    return ''.join(chunks)


def _amount(value):
    if value is None:
        return None
    try:
        raw = str(value)
        if len(raw) > 128:
            raise InvalidOperation
        number = Decimal(raw)
        if (not number.is_finite() or abs(number.adjusted()) > 44
                or not -12 <= number.as_tuple().exponent <= 34):
            raise InvalidOperation
        return format(number, 'f')
    except (InvalidOperation, ValueError):
        raise ValueError('SMARTLED_CONTRACT_STORE_AMOUNT_INVALID') from None


def save_snapshot(snapshot, *, queue_revision=None):
    """Atomically replace current contract, including an explicit no-match result.

    Only values from normalize_snapshot/refresh_snapshot are accepted. A refresh
    failure must never call this function with partially accumulated details.
    """
    if not isinstance(snapshot, dict) or snapshot.get('source_state') != 'CURRENT':
        raise ValueError('SMARTLED_CONTRACT_CURRENT_SNAPSHOT_REQUIRED')
    key = _key(snapshot.get('unified_contract_no'))
    day = dt.date.fromisoformat(str(snapshot.get('contract_date') or ''))
    if day < START_DATE:
        raise ValueError('SMARTLED_CONTRACT_DATE_OUTSIDE_SCOPE')
    items = snapshot.get('items')
    count = snapshot.get('source_detail_count')
    if (not isinstance(items, list) or len(items) > 1000
            or isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 1000
            or len(items) > count or snapshot.get('smartled_match') is not bool(items)):
        raise ValueError('SMARTLED_CONTRACT_SNAPSHOT_INCOMPLETE')
    digest = snapshot.get('source_payload_sha256', '')
    if not re.fullmatch('[0-9a-f]{64}', str(digest)):
        raise ValueError('SMARTLED_CONTRACT_PROVENANCE_REQUIRED')
    kinds = set()
    for row in items:
        if not isinstance(row, dict) or row.get('kind') not in KINDS:
            raise ValueError('SMARTLED_CONTRACT_ITEM_INVALID')
        kinds.add(row['kind'])
    totals = snapshot.get('item_subtotals')
    if not isinstance(totals, dict) or set(totals) != set(KINDS):
        raise ValueError('SMARTLED_CONTRACT_TOTALS_REQUIRED')
    encoded = _json(snapshot)
    parties = []
    for field in ('vendors', 'agencies'):
        value = snapshot.get(field)
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError('SMARTLED_CONTRACT_PARTIES_INVALID')
        parties.append(_json(value, 32768))
    summary = {}
    for kind in KINDS:
        relevant = [item for item in items if item['kind'] == kind]
        summary[kind] = {'count': len(relevant), 'items': [
            {'name': str(item.get('item_name', ''))[:63] + ('…' if len(str(item.get('item_name', ''))) > 63 else ''),
             'classification': str(item.get('classification_no', ''))[:8],
             'amount': item.get('item_amount') if len(str(item.get('item_amount') or '')) <= 64 else None}
            for item in relevant[:3]]}
    summary_json = _json(summary, 4096)
    values = (key, str(snapshot.get('decided_contract_no') or ''), day.isoformat(),
              str(snapshot.get('contract_name') or ''), 'CURRENT', '',
              _amount(snapshot.get('contract_amount')),
              _amount(totals['FIXTURE']), _amount(totals['LAMP']), _amount(totals['CONTROL']),
              int('FIXTURE' in kinds), int('LAMP' in kinds), int('CONTROL' in kinds),
              parties[0], parties[1], digest, encoded, summary_json, _now())
    with _write() as conn:
        _assert_revision(conn, key, queue_revision)
        conn.execute('''INSERT INTO smartled_contract_snapshots(
            unified_no,decided_no,contract_date,contract_name,source_state,source_deleted_at,
            contract_amount,fixture_amount,lamp_amount,control_amount,has_fixture,has_lamp,has_control,
            vendors_json,agencies_json,source_payload_sha256,snapshot_json,items_summary_json,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(unified_no) DO UPDATE SET
            decided_no=excluded.decided_no,contract_date=excluded.contract_date,
            contract_name=excluded.contract_name,source_state=excluded.source_state,
            source_deleted_at=excluded.source_deleted_at,contract_amount=excluded.contract_amount,
            fixture_amount=excluded.fixture_amount,lamp_amount=excluded.lamp_amount,
            control_amount=excluded.control_amount,has_fixture=excluded.has_fixture,
            has_lamp=excluded.has_lamp,has_control=excluded.has_control,
            vendors_json=excluded.vendors_json,agencies_json=excluded.agencies_json,
            source_payload_sha256=excluded.source_payload_sha256,snapshot_json=excluded.snapshot_json,
            items_summary_json=excluded.items_summary_json,
            updated_at=excluded.updated_at''', values)
        _finish(conn, key, queue_revision)
    return key


def mark_source_deleted(event, *, queue_revision=None):
    if (not isinstance(event, dict) or event.get('source_state') != 'SOURCE_DELETED'
            or event.get('legal_cancellation_confirmed') is not False
            or not event.get('source_deleted_at')):
        raise ValueError('SMARTLED_SOURCE_DELETION_EVENT_REQUIRED')
    key = _key(event.get('unified_contract_no'))
    with _write() as conn:
        _assert_revision(conn, key, queue_revision)
        known = (conn.execute('SELECT unified_no FROM smartled_contract_snapshots WHERE unified_no=?', (key,)).fetchone()
                 or conn.execute('SELECT unified_no FROM smartled_contract_queue WHERE unified_no=?', (key,)).fetchone())
        if not known:
            return False
        # Keep the last snapshot as historical evidence, but never show it in
        # current smart-contract reads. A deletion can precede first discovery.
        conn.execute('''INSERT INTO smartled_contract_snapshots(
            unified_no,source_state,source_deleted_at,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(unified_no) DO UPDATE SET source_state=excluded.source_state,
            source_deleted_at=excluded.source_deleted_at,updated_at=excluded.updated_at''',
            (key, 'SOURCE_DELETED', str(event['source_deleted_at']), _now()))
        # Invalidate any in-flight worker's revision as well as queued refreshes.
        # A later save using that revision fails closed rather than resurrecting.
        conn.execute('DELETE FROM smartled_contract_queue WHERE unified_no=?', (key,))


# Compatibility name for the collector; both retain source-deletion semantics.
mark_deleted = mark_source_deleted


def get_snapshot(unified_no):
    with connect() as conn:
        row = conn.execute('SELECT snapshot_json,source_state,source_deleted_at FROM smartled_contract_snapshots WHERE unified_no=?', (_key(unified_no),)).fetchone()
    if not row:
        return None
    return {'snapshot': json.loads(row['snapshot_json']),
            'source_state': row['source_state'], 'source_deleted_at': row['source_deleted_at']}


def list_current(*, start_date='2026-10-01', end_date='', kind='FIXTURE', limit=100, offset=0, q=''):
    """Summary-only page: never materialize 100 potentially 2MiB detail snapshots."""
    begin = dt.date.fromisoformat(str(start_date))
    if begin < START_DATE or kind not in KINDS:
        raise ValueError('SMARTLED_CONTRACT_READ_FILTER_INVALID')
    where = ['source_state=?', 'contract_date>=?', KINDS[kind] + '=1']
    params = ['CURRENT', begin.isoformat()]
    if end_date:
        end = dt.date.fromisoformat(str(end_date))
        if end < begin:
            raise ValueError('SMARTLED_CONTRACT_READ_DATE_INVALID')
        where.append('contract_date<=?'); params.append(end.isoformat())
    offset = int(offset)
    if not 0 <= offset <= 50000:
        raise ValueError('SMARTLED_CONTRACT_READ_PAGE_INVALID')
    if not isinstance(q, str) or len(q) > 100:
        raise ValueError('SMARTLED_CONTRACT_SEARCH_INVALID')
    if q.strip():
        pattern = '%' + q.strip().replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        fields = ('unified_no', 'decided_no', 'contract_name', 'vendors_json', 'agencies_json')
        where.append('(' + ' OR '.join(field + " LIKE ? ESCAPE '!'" for field in fields) + ')')
        params.extend([pattern] * len(fields))
    params.extend([max(1, min(int(limit), 100)), offset])
    with connect() as conn:
        if backend_name() == 'POSTGRESQL':
            conn.execute("SELECT set_config('statement_timeout', ?, true), set_config('work_mem', '4MB', true)", ('3500ms',))
        rows = conn.execute('''SELECT unified_no,decided_no,contract_date,contract_name,
                source_state,contract_amount,fixture_amount,lamp_amount,control_amount,
                has_fixture,has_lamp,has_control,vendors_json,agencies_json,items_summary_json,updated_at
            FROM smartled_contract_snapshots WHERE ''' + ' AND '.join(where)
            + ' ORDER BY contract_date DESC,unified_no LIMIT ? OFFSET ?', tuple(params)).fetchall()
    result = []
    for row in rows:
        value = dict(row)
        value['items_summary'] = json.loads(value.pop('items_summary_json'))
        value['vendors'] = json.loads(value.pop('vendors_json'))
        value['agencies'] = json.loads(value.pop('agencies_json'))
        result.append(value)
    return result


def mark_out_of_scope(unified_no, *, queue_revision):
    """Retire a confirmed contract-date correction; retain historical evidence."""
    key = _key(unified_no)
    with _write() as conn:
        _assert_revision(conn, key, queue_revision)
        conn.execute("UPDATE smartled_contract_snapshots SET source_state='OUT_OF_SCOPE',updated_at=? WHERE unified_no=?", (_now(), key))
        _finish(conn, key, queue_revision)
