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


def verified_checkpoint(cp):
    m = _meta(cp)
    if not cp or cp.get('status') != 'COMPLETE' or m.get('version') != COLLECTION_VERSION:
        return False
    if m.get('completion_reason') not in ('TOTAL_REACHED', 'EMPTY_PAGE', 'SHORT_PAGE_UNKNOWN_TOTAL'):
        return False
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='vnext_collection_pages'").fetchone()
        if not exists:
            return False
        key = (cp['dataset'], cp['scope_key'], m.get('generation', ''))
        pages = conn.execute('SELECT COUNT(*) n,MIN(page_no) lo,MAX(page_no) hi,SUM(item_count) items '
                             'FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=?', key).fetchone()
        last = conn.execute('SELECT * FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=? ORDER BY page_no DESC LIMIT 1', key).fetchone()
        wrong_sizes = conn.execute('SELECT COUNT(*) n FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=? AND page_size<>?', key + (m.get('page_size'),)).fetchone()['n']
        full_pages = conn.execute('SELECT COUNT(*) n FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=? AND item_count=page_size', key).fetchone()['n']
        missing = conn.execute('''SELECT COUNT(*) n FROM vnext_collection_items i
            LEFT JOIN raw_record_revisions r ON r.dataset=i.dataset AND r.source_key=i.source_key
             AND r.payload_sha256=i.payload_sha256
            WHERE i.dataset=? AND i.scope_key=? AND i.generation=? AND r.id IS NULL''', key).fetchone()['n']
        unique = conn.execute('SELECT COUNT(*) n FROM vnext_collection_items '
                              'WHERE dataset=? AND scope_key=? AND generation=?', key).fetchone()['n']
    short_page_proven = (m.get('completion_reason') != 'SHORT_PAGE_UNKNOWN_TOTAL' or bool(full_pages))
    return (bool(pages['n']) and pages['lo'] == 1 and pages['hi'] == pages['n']
            and pages['items'] == unique == int(cp['fetched_count']) == int(cp['saved_count'])
            and int(cp['page_no']) == pages['hi'] + 1 and not wrong_sizes and not missing
            and last['terminal_reason'] == m['completion_reason'] and short_page_proven
            and (int(cp['source_total']) <= 0 or int(cp['source_total']) == unique))


def collect_pages(*, dataset, scope, range_start, range_end, page_size, max_pages,
                  resume, fetch, identity, source_system, source_operation, source_date,
                  preserve, checkpoint, lookup, relationships=None, validate_row=None):
    size = int(page_size)
    if size < 1 or (max_pages is not None and int(max_pages) < 1):
        raise ValueError('page size and page budget must be positive')
    ensure_foundation()
    with connect() as conn:
        conn.executescript(RECEIPTS)
    observed = lookup(dataset, scope)
    cp = observed if resume else None
    m = _meta(cp)
    fingerprint = hashlib.sha256(json.dumps([dataset, scope, range_start, range_end,
                                           source_operation], ensure_ascii=False).encode()).hexdigest()
    if m.get('version') == COLLECTION_VERSION:
        if m.get('page_size') != size or m.get('query_fingerprint') != fingerprint:
            raise ValueError('resume query/page size changed; replay explicitly with resume=False')
        if verified_checkpoint(cp):
            return _result(cp, resumed=True)
        if cp.get('status') == 'COMPLETE':
            cp = None
    else:
        cp = None
    if cp is None:
        m = {'version': COLLECTION_VERSION, 'generation': uuid.uuid4().hex,
             'page_size': size, 'query_fingerprint': fingerprint, 'completion_reason': ''}
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


_verified_checkpoint_receipt_only = verified_checkpoint


def verified_checkpoint(cp):
    if not _verified_checkpoint_receipt_only(cp):
        return False
    meta = _meta(cp)
    generation = str(meta.get('generation') or '')
    key = (cp['dataset'], cp['scope_key'], generation)
    with connect() as conn:
        pages = [dict(row) for row in conn.execute(
            '''SELECT page_no,response_hash FROM vnext_collection_pages
               WHERE dataset=? AND scope_key=? AND generation=? ORDER BY page_no''',
            key,
        ).fetchall()]
        items = [dict(row) for row in conn.execute(
            '''SELECT source_key,page_no,payload_sha256 FROM vnext_collection_items
               WHERE dataset=? AND scope_key=? AND generation=? ORDER BY page_no,source_key''',
            key,
        ).fetchall()]
        current_mismatch = conn.execute(
            '''SELECT COUNT(*) AS n FROM vnext_collection_items i
               LEFT JOIN raw_records r
                 ON r.dataset=i.dataset AND r.source_key=i.source_key
                AND r.payload_sha256=i.payload_sha256
               WHERE i.dataset=? AND i.scope_key=? AND i.generation=? AND r.id IS NULL''',
            key,
        ).fetchone()['n']
    if current_mismatch:
        return False
    expected = {int(row['page_no']): str(row['response_hash']) for row in pages}
    grouped = {page_no: [] for page_no in expected}
    for row in items:
        page_no = int(row['page_no'])
        if page_no not in grouped:
            return False
        grouped[page_no].append((str(row['source_key']), str(row['payload_sha256'])))
    if set(grouped) != set(expected):
        return False
    for page_no, pairs in grouped.items():
        response_hash = hashlib.sha256(json.dumps(sorted(pairs)).encode()).hexdigest()
        if response_hash != expected[page_no]:
            return False
    return True
