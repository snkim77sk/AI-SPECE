"""Transactional page ingestion shared by every independent RAW collector.

RAW, unique-identity page receipts, and the next-page checkpoint commit together.
Legacy checkpoints have no receipts and are replayed from page 1 without deleting RAW.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from db import connect
from vnext_store import ensure_foundation
from vnext_paging import source_page_complete

COLLECTION_VERSION = 2
RECEIPTS = '''
CREATE TABLE IF NOT EXISTS vnext_collection_pages(
    dataset TEXT NOT NULL, scope_key TEXT NOT NULL, generation TEXT NOT NULL,
    page_no INTEGER NOT NULL, page_size INTEGER NOT NULL, response_hash TEXT NOT NULL,
    item_count INTEGER NOT NULL, source_total INTEGER NOT NULL, terminal_reason TEXT NOT NULL,
    PRIMARY KEY(dataset,scope_key,generation,page_no));
CREATE TABLE IF NOT EXISTS vnext_collection_items(
    dataset TEXT NOT NULL, scope_key TEXT NOT NULL, generation TEXT NOT NULL,
    source_key TEXT NOT NULL, page_no INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(dataset,scope_key,generation,source_key));
'''


def _meta(cp):
    try:
        value = json.loads((cp or {}).get('cursor_value') or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _verified_checkpoint(cp, *, complete):
    """Validate either a terminal collection or its committed resume prefix."""
    m = _meta(cp)
    statuses = ('COMPLETE',) if complete else ('RUNNING', 'FAILED', 'INCOMPLETE')
    if not cp or cp.get('status') not in statuses or m.get('version') != COLLECTION_VERSION:
        return False
    generation = m.get('generation')
    try:
        size = int(cp['page_size'])
        next_page = int(cp['page_no'])
        fetched = int(cp['fetched_count'])
        saved = int(cp['saved_count'])
        source_total = int(cp['source_total'])
    except (KeyError, TypeError, ValueError):
        return False
    if (not isinstance(generation, str) or not generation or size < 1
            or size != m.get('page_size') or fetched < 0 or saved != fetched):
        return False
    with connect() as conn:
        tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('vnext_collection_pages','vnext_collection_items')"
        ).fetchone()[0]
        if tables != 2:
            return False
        key = (cp['dataset'], cp['scope_key'], generation)
        pages = conn.execute(
            'SELECT * FROM vnext_collection_pages WHERE dataset=? AND scope_key=? '
            'AND generation=? ORDER BY page_no', key,
        ).fetchall()
        items = conn.execute(
            'SELECT source_key,page_no,payload_sha256 FROM vnext_collection_items '
            'WHERE dataset=? AND scope_key=? AND generation=?', key,
        ).fetchall()
        missing = conn.execute('''SELECT COUNT(*) n FROM vnext_collection_items i
            LEFT JOIN raw_record_revisions r ON r.dataset=i.dataset AND r.source_key=i.source_key
             AND r.payload_sha256=i.payload_sha256
            LEFT JOIN raw_records current ON current.dataset=i.dataset AND current.source_key=i.source_key
             AND current.payload_sha256=i.payload_sha256
            WHERE i.dataset=? AND i.scope_key=? AND i.generation=?
             AND (r.id IS NULL OR current.id IS NULL)''', key).fetchone()['n']
    if missing or next_page != len(pages) + 1 or fetched != len(items):
        return False
    grouped = {page['page_no']: [] for page in pages}
    for item in items:
        if item['page_no'] not in grouped:
            return False
        grouped[item['page_no']].append((item['source_key'], item['payload_sha256']))
    count, total, full_page_seen, reason = 0, -1, False, ''
    for number, page in enumerate(pages, 1):
        pairs = grouped[page['page_no']]
        item_count = len(pairs)
        if (page['page_no'] != number or page['page_size'] != size
                or page['item_count'] != item_count or item_count > size
                or hashlib.sha256(json.dumps(sorted(pairs)).encode()).hexdigest() != page['response_hash']):
            return False
        reported = page['source_total']
        # Receipts keep the last known positive total even if later responses omit it.
        if reported != total and (total > 0 or reported <= 0):
            return False
        total = reported
        count += item_count
        if total > 0 and (count > total or (not item_count and count < total)):
            return False
        done = count == total if total > 0 else (
            not item_count or (item_count < size and full_page_seen)
        )
        reason = ('TOTAL_REACHED' if done and total > 0 else
                  'EMPTY_PAGE' if done and not item_count else
                  'SHORT_PAGE_UNKNOWN_TOTAL' if done else '')
        if page['terminal_reason'] != reason or (done and number != len(pages)):
            return False
        full_page_seen = full_page_seen or item_count == size
    return (count == fetched and total == source_total
            and reason == m.get('completion_reason', '') and bool(reason) == complete)


def verified_checkpoint(cp):
    return _verified_checkpoint(cp, complete=True)


def collect_pages(*, dataset, scope, range_start, range_end, page_size, max_pages,
                  resume, fetch, identity, source_system, source_operation, source_date,
                  preserve, checkpoint, lookup, relationships=None, validate_row=None,
                  checkpoint_contract=""):
    size = int(page_size)
    if size < 1 or (max_pages is not None and int(max_pages) < 1):
        raise ValueError('page size and page budget must be positive')
    ensure_foundation()
    with connect() as conn:
        conn.executescript(RECEIPTS)
    observed = lookup(dataset, scope)
    cp = observed if resume else None
    m = _meta(cp)
    contract = str(checkpoint_contract or "").strip()
    fingerprint_parts = [dataset, scope, range_start, range_end, source_operation]
    if contract:
        fingerprint_parts.append(contract)
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_parts, ensure_ascii=False).encode()
    ).hexdigest()
    if m.get('version') == COLLECTION_VERSION:
        if str(m.get('checkpoint_contract') or '') != contract:
            raise ValueError(
                'resume collection contract changed; replay explicitly with resume=False'
            )
        if m.get('page_size') != size or m.get('query_fingerprint') != fingerprint:
            raise ValueError('resume query/page size changed; replay explicitly with resume=False')
        if verified_checkpoint(cp):
            return _result(cp, resumed=True)
        if cp.get('status') == 'COMPLETE' or not _verified_checkpoint(cp, complete=False):
            # A partial run can lose its current-RAW binding too, for example when
            # an overlapping anomalous page preserves a newer payload revision.
            # Keep all RAW/receipts, but never continue using unproven counters.
            cp = None
    else:
        cp = None
    if cp is None:
        m = {'version': COLLECTION_VERSION, 'generation': uuid.uuid4().hex,
             'page_size': size, 'query_fingerprint': fingerprint,
             'checkpoint_contract': contract, 'completion_reason': ''}
        cp = {'dataset': dataset, 'scope_key': scope, 'page_no': 1,
              'source_total': -1, 'fetched_count': 0, 'saved_count': 0}
    values = dict(range_start=str(range_start), range_end=str(range_end),
                  cursor_value=json.dumps(m, sort_keys=True), page_no=int(cp['page_no']),
                  page_size=size, last_page_fingerprint='',
                  source_total=int(cp['source_total']), fetched_count=int(cp['fetched_count']),
                  saved_count=int(cp['saved_count']), status='RUNNING', last_error='')
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',
                               (dataset, scope)).fetchone()
        fields = ('cursor_value', 'page_no', 'fetched_count', 'saved_count', 'status')
        if bool(current) != bool(observed) or (current and any(current[k] != observed[k] for k in fields)):
            raise RuntimeError('CONCURRENT_CHECKPOINT_CHANGED')
        checkpoint(dataset, scope, _conn=conn, **values)
    committed = dict(values)
    generation = m['generation']
    with connect() as conn:
        full_page_seen = bool(conn.execute(
            'SELECT 1 FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=? '
            'AND item_count=page_size LIMIT 1', (dataset, scope, generation)).fetchone())
    for _ in range(int(max_pages) if max_pages is not None else 1000000):
        page = committed['page_no']
        try:
            from vnext_source_guard import current_source_request_context, require_attested_transport_result
            before_transport = current_source_request_context()
            source_result = fetch(page, size)
            source_result = require_attested_transport_result(
                before_transport, source_result,
                error_code='SOURCE_COLLECTION_TRANSPORT_NOT_ATTESTED',
            )
            items, reported = source_result
            if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
                raise ValueError('invalid source row shape')
            if reported is not None and (isinstance(reported, bool) or int(reported) < 0):
                raise ValueError('invalid source total')
            known = int(reported) if reported is not None and int(reported) > 0 else -1
            total = committed['source_total']
            problem = ''
            if known > 0:
                if total > 0 and total != known:
                    problem = 'SOURCE_TOTAL_CHANGED'
                total = known
            if validate_row:
                problems = [validate_row(row) for row in items]
                problem = next((value for value in problems if value), problem)
            rowkeys = [str(identity(row)) for row in items]
            digests = [hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                                separators=(',', ':')).encode()).hexdigest() for row in items]
            if len(set(rowkeys)) != len(rowkeys):
                problem = 'DUPLICATE_OR_COLLIDING_SOURCE_ID'
            if len(items) > size:
                problem = 'OVERSIZED_PAGE'
            fetched = committed['fetched_count'] + len(items)
            if total > 0 and fetched > total:
                problem = 'SOURCE_TOTAL_UNDERRUN'
            if not items and total > 0 and fetched < total:
                problem = 'PREMATURE_EMPTY_PAGE'
            with connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',
                                       (dataset, scope)).fetchone()
                if (not current or current['cursor_value'] != committed['cursor_value']
                        or current['page_no'] != page or current['fetched_count'] != committed['fetched_count']
                        or current['status'] != 'RUNNING'):
                    raise RuntimeError('CONCURRENT_CHECKPOINT_CHANGED')
                for key in rowkeys:
                    if conn.execute('SELECT 1 FROM vnext_collection_items WHERE dataset=? AND scope_key=? '
                                    'AND generation=? AND source_key=?', (dataset, scope, generation, key)).fetchone():
                        problem = 'REPEATED_OR_OVERLAPPING_PAGE'
                for row, key in zip(items, rowkeys):
                    preserve(dataset, key, row, source_system=source_system,
                             source_operation=source_operation, source_date=source_date(row), _conn=conn)
                if problem:
                    stopped = dict(committed, status='INCOMPLETE', last_error=problem)
                    checkpoint(dataset, scope, _conn=conn, **stopped)
                else:
                    if relationships:
                        for row, key in zip(items, rowkeys):
                            for relation in relationships(row, key):
                                conn.execute("""INSERT INTO lifecycle_links(from_type,from_key,to_type,to_key,link_type,confidence,reason)
                                    VALUES(?,?,?,?,?,1,'exact source notice identity')
                                    ON CONFLICT(from_type,from_key,to_type,to_key,link_type)
                                    DO UPDATE SET confidence=1,reason=excluded.reason""", relation)
                    if total > 0:
                        done = fetched == total
                    elif not items:
                        done = True
                    else:
                        done = len(items) < size and full_page_seen
                    reason = ('TOTAL_REACHED' if total > 0 and done else
                              'EMPTY_PAGE' if done and not items else
                              'SHORT_PAGE_UNKNOWN_TOTAL' if done else '')
                    terminal = dict(m, completion_reason=reason)
                    conn.execute('INSERT INTO vnext_collection_pages VALUES(?,?,?,?,?,?,?,?,?)',
                                 (dataset, scope, generation, page, size,
                                  hashlib.sha256(json.dumps(sorted(zip(rowkeys, digests))).encode()).hexdigest(),
                                  len(items), total, reason))
                    conn.executemany('INSERT INTO vnext_collection_items VALUES(?,?,?,?,?,?)',
                                     [(dataset, scope, generation, key, page, digest)
                                      for key, digest in zip(rowkeys, digests)])
                    next_values = dict(committed, cursor_value=json.dumps(terminal, sort_keys=True),
                                       page_no=page + 1, source_total=total,
                                       fetched_count=fetched, saved_count=fetched,
                                       status='COMPLETE' if done else 'RUNNING', last_error='')
                    checkpoint(dataset, scope, _conn=conn, **next_values)
            if problem:
                return _result(dict(stopped, dataset=dataset, scope_key=scope))
            committed = next_values
            m = terminal
            if len(items) == size:
                full_page_seen = True
            if done:
                return _result(dict(committed, dataset=dataset, scope_key=scope))
        except Exception as exc:
            with connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',
                                       (dataset, scope)).fetchone()
                if (current and current['cursor_value'] == committed['cursor_value']
                        and current['page_no'] == committed['page_no']
                        and current['fetched_count'] == committed['fetched_count']):
                    checkpoint(dataset, scope, _conn=conn,
                               **dict(committed, status='FAILED', last_error=type(exc).__name__))
            raise
    return _result(dict(committed, dataset=dataset, scope_key=scope))


def _result(cp, resumed=False):
    return {'dataset': cp['dataset'], 'scope': cp['scope_key'],
            'fetched': int(cp['fetched_count']), 'saved': int(cp['saved_count']),
            'source_total': None if int(cp['source_total']) < 0 else int(cp['source_total']),
            'complete': cp.get('status') == 'COMPLETE', 'resumed': resumed,
            'status': cp.get('status'), 'reason': cp.get('last_error', ''),
            'completion_reason': _meta(cp).get('completion_reason', '')}
