"""Transactional page ingestion for G2B source collectors.

Persisted target records, page receipts, and the next-page checkpoint commit together.
Production 4.1 may use normalized backing tables instead of source JSON storage.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from db import connect
from vnext_store import ensure_foundation
from vnext_paging import source_page_complete

COLLECTION_VERSION = 3
SHOPPING_COMPACT_MARKER_VERSION = "shopping-complete-v1"
RECEIPTS = '''
CREATE TABLE IF NOT EXISTS vnext_collection_pages(
    dataset TEXT NOT NULL, scope_key TEXT NOT NULL, generation TEXT NOT NULL,
    page_no INTEGER NOT NULL, page_size INTEGER NOT NULL, response_hash TEXT NOT NULL,
    item_count INTEGER NOT NULL, source_total INTEGER NOT NULL, terminal_reason TEXT NOT NULL,
    PRIMARY KEY(dataset,scope_key,generation,page_no));
CREATE TABLE IF NOT EXISTS vnext_collection_items(
    dataset TEXT NOT NULL, scope_key TEXT NOT NULL, generation TEXT NOT NULL,
    source_key TEXT NOT NULL, page_no INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
    stored INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(dataset,scope_key,generation,source_key));
'''


def _ensure_receipt_schema(conn):
    conn.executescript(RECEIPTS)
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(vnext_collection_items)").fetchall()
    }
    if "stored" not in columns:
        conn.execute(
            "ALTER TABLE vnext_collection_items ADD COLUMN stored INTEGER NOT NULL DEFAULT 1"
        )


def ensure_collection_storage():
    """Install foundation + page receipt schema once before a multi-scope run."""
    ensure_foundation()
    with connect() as conn:
        _ensure_receipt_schema(conn)


def _meta(cp):
    try:
        value = json.loads((cp or {}).get('cursor_value') or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _receipt_digest(page_rows):
    payload = [
        [
            int(row["page_no"]),
            int(row["page_size"]),
            str(row["response_hash"]),
            int(row["item_count"]),
            int(row["source_total"]),
            str(row["terminal_reason"] or ""),
        ]
        for row in page_rows
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _compact_marker(meta, cp, page_rows):
    return {
        "version": SHOPPING_COMPACT_MARKER_VERSION,
        "generation": str(meta.get("generation") or ""),
        "page_count": max(0, int(cp.get("page_no") or 1) - 1),
        "page_size": int(cp.get("page_size") or 0),
        "source_total": int(cp.get("source_total") or -1),
        "fetched_count": int(cp.get("fetched_count") or 0),
        "saved_count": int(cp.get("saved_count") or 0),
        "completion_reason": str(meta.get("completion_reason") or ""),
        "checkpoint_contract": str(meta.get("checkpoint_contract") or ""),
        "query_fingerprint": str(meta.get("query_fingerprint") or ""),
        "receipt_digest": _receipt_digest(page_rows),
    }


def verified_compact_completion(cp):
    """Validate a durable shopping COMPLETE marker without loading page/item receipts."""
    meta = _meta(cp)
    marker = meta.get("compact_completion")
    if (
        not cp
        or str(cp.get("dataset") or "") != "shopping_delivery"
        or str(cp.get("status") or "") != "COMPLETE"
        or meta.get("version") != COLLECTION_VERSION
        or not isinstance(marker, dict)
        or str(marker.get("version") or "") != SHOPPING_COMPACT_MARKER_VERSION
    ):
        return False
    generation = str(meta.get("generation") or "")
    reason = str(meta.get("completion_reason") or "")
    try:
        page_no = int(cp.get("page_no") or 0)
        page_size = int(cp.get("page_size") or 0)
        source_total = int(cp.get("source_total") or -1)
        fetched = int(cp.get("fetched_count") or 0)
        saved = int(cp.get("saved_count") or 0)
        marker_page_count = int(marker.get("page_count") or 0)
        marker_page_size = int(marker.get("page_size") or 0)
        marker_source_total = int(marker.get("source_total") or -1)
        marker_fetched = int(marker.get("fetched_count") or 0)
        marker_saved = int(marker.get("saved_count") or 0)
    except (TypeError, ValueError):
        return False
    digest = str(marker.get("receipt_digest") or "")
    if (
        not generation
        or page_no < 2
        or page_size < 1
        or fetched < 0
        or saved < 0
        or saved > fetched
        or marker_page_count != page_no - 1
        or str(marker.get("generation") or "") != generation
        or marker_page_size != page_size
        or marker_source_total != source_total
        or marker_fetched != fetched
        or marker_saved != saved
        or str(marker.get("completion_reason") or "") != reason
        or str(marker.get("checkpoint_contract") or "")
        != str(meta.get("checkpoint_contract") or "")
        or str(marker.get("query_fingerprint") or "")
        != str(meta.get("query_fingerprint") or "")
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest.lower())
    ):
        return False
    if reason == "TOTAL_REACHED":
        return source_total > 0 and fetched == source_total
    if reason == "EMPTY_PAGE":
        return source_total < 0
    if reason == "SHORT_PAGE_UNKNOWN_TOTAL":
        return source_total < 0 and fetched > 0
    return False


def _verified_checkpoint(cp, *, complete, require_current_raw=True, schema_prepared=False):
    """Validate a collection receipt against its persisted backing record."""
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
            or size != m.get('page_size') or fetched < 0 or saved < 0 or saved > fetched):
        return False
    with connect() as conn:
        if not schema_prepared:
            tables = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name IN ('vnext_collection_pages','vnext_collection_items')"
            ).fetchone()[0]
            if tables != 2:
                return False
            _ensure_receipt_schema(conn)
        key = (cp['dataset'], cp['scope_key'], generation)
        pages = conn.execute(
            'SELECT * FROM vnext_collection_pages WHERE dataset=? AND scope_key=? '
            'AND generation=? ORDER BY page_no', key,
        ).fetchall()
        items = conn.execute(
            'SELECT source_key,page_no,payload_sha256,stored FROM vnext_collection_items '
            'WHERE dataset=? AND scope_key=? AND generation=?', key,
        ).fetchall()
        normalized_shopping = (
            str(cp.get("dataset") or "") == "shopping_delivery"
            and int(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='shopping_records'"
            ).fetchone()[0] or 0) == 1
        )
        if normalized_shopping:
            revision_missing = int(conn.execute(
                """SELECT COUNT(*) n FROM vnext_collection_items i
                   LEFT JOIN shopping_records r
                     ON r.source_key=i.source_key
                    AND r.payload_sha256=i.payload_sha256
                   WHERE i.dataset=? AND i.scope_key=? AND i.generation=?
                     AND i.stored=1 AND r.source_key IS NULL""",
                key,
            ).fetchone()["n"] or 0)
            current_missing = revision_missing if require_current_raw else 0
        else:
            revision_missing = conn.execute('''SELECT COUNT(*) n FROM vnext_collection_items i
                LEFT JOIN raw_record_revisions r ON r.dataset=i.dataset AND r.source_key=i.source_key
                 AND r.payload_sha256=i.payload_sha256
                WHERE i.dataset=? AND i.scope_key=? AND i.generation=? AND i.stored=1
                 AND r.id IS NULL''', key).fetchone()['n']
            current_missing = 0
            if require_current_raw:
                current_missing = conn.execute('''SELECT COUNT(*) n FROM vnext_collection_items i
                    LEFT JOIN raw_records current ON current.dataset=i.dataset AND current.source_key=i.source_key
                     AND current.payload_sha256=i.payload_sha256
                    WHERE i.dataset=? AND i.scope_key=? AND i.generation=? AND i.stored=1
                     AND current.id IS NULL''', key).fetchone()['n']
    stored_count = sum(1 for item in items if int(item["stored"] or 0) == 1)
    if (revision_missing or current_missing or next_page != len(pages) + 1
            or fetched != len(items) or saved != stored_count):
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


def verified_checkpoint(cp, *, schema_prepared=False):
    """Terminal receipt whose payloads still bind to the current RAW projection."""
    return _verified_checkpoint(
        cp,
        complete=True,
        require_current_raw=True,
        schema_prepared=schema_prepared,
    )


def verified_terminal_receipt(cp, *, schema_prepared=False):
    """Terminal receipt backed by immutable RAW revisions, even after later updates."""
    return _verified_checkpoint(
        cp,
        complete=True,
        require_current_raw=False,
        schema_prepared=schema_prepared,
    )


def compact_verified_terminal_receipt(
    cp, *, schema_prepared=False, receipt_verified=False
):
    """Promote one verified shopping COMPLETE receipt set to a compact checkpoint marker."""
    if str((cp or {}).get("dataset") or "") != "shopping_delivery":
        return False
    if verified_compact_completion(cp):
        return True
    if not receipt_verified and not verified_terminal_receipt(
        cp, schema_prepared=schema_prepared
    ):
        return False

    meta = _meta(cp)
    generation = str(meta.get("generation") or "")
    if not generation:
        return False

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?",
            (cp["dataset"], cp["scope_key"]),
        ).fetchone()
        if (
            not current
            or str(current["status"] or "") != "COMPLETE"
            or str(current["cursor_value"] or "") != str(cp.get("cursor_value") or "")
        ):
            return False

        page_rows = conn.execute(
            """SELECT page_no,page_size,response_hash,item_count,source_total,terminal_reason
               FROM vnext_collection_pages
               WHERE dataset=? AND scope_key=? AND generation=?
               ORDER BY page_no""",
            (cp["dataset"], cp["scope_key"], generation),
        ).fetchall()
        if len(page_rows) != max(0, int(cp.get("page_no") or 1) - 1):
            return False

        next_meta = dict(meta)
        next_meta["compact_completion"] = _compact_marker(meta, cp, page_rows)
        next_cursor = json.dumps(next_meta, sort_keys=True)
        update_result = conn.execute(
            """UPDATE collection_checkpoints
               SET cursor_value=?,updated_at=CURRENT_TIMESTAMP
               WHERE dataset=? AND scope_key=? AND status='COMPLETE'
                 AND cursor_value=?""",
            (
                next_cursor,
                cp["dataset"],
                cp["scope_key"],
                str(cp.get("cursor_value") or ""),
            ),
        )
        if int(update_result.rowcount or 0) != 1:
            return False
        conn.execute(
            """DELETE FROM vnext_collection_items
               WHERE dataset=? AND scope_key=? AND generation=?""",
            (cp["dataset"], cp["scope_key"], generation),
        )
        conn.execute(
            """DELETE FROM vnext_collection_pages
               WHERE dataset=? AND scope_key=? AND generation=?""",
            (cp["dataset"], cp["scope_key"], generation),
        )
    return True


def _safe_error_label(exc):
    """Keep operator-useful diagnostics without exposing SQL, URLs, or credentials."""
    name = type(exc).__name__
    code = str(getattr(exc, "code", "") or "").strip()
    original = getattr(exc, "orig", None)
    sqlstate = str(
        getattr(original, "sqlstate", "")
        or getattr(original, "pgcode", "")
        or ""
    ).strip()
    message = " ".join(str(getattr(exc, "message", "") or "").split())[:180]
    parts = [name]
    if code:
        parts.append(code)
    if sqlstate:
        parts.append("SQLSTATE_" + sqlstate)
    if message:
        parts.append(message)
    return ":".join(parts)


def _notify_progress(progress, event, **details):
    """Best-effort operator progress callback; never affect collection semantics."""
    if progress is None:
        return
    try:
        progress({"event": str(event), **details})
    except Exception:
        # Display/logging failures must never break source persistence.
        return


def collect_pages(*, dataset, scope, range_start, range_end, page_size, max_pages,
                  resume, fetch, identity, source_system, source_operation, source_date,
                  preserve, checkpoint, lookup, relationships=None, validate_row=None,
                  checkpoint_contract="", progress=None, preserve_filter=None,
                  storage_prepared=False, compact_complete=False):
    size = int(page_size)
    if size < 1 or (max_pages is not None and int(max_pages) < 1):
        raise ValueError('page size and page budget must be positive')
    if not storage_prepared:
        ensure_collection_storage()
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
        if verified_compact_completion(cp):
            _notify_progress(
                progress, "scope_complete", scope=scope,
                page=max(0, int(cp.get("page_no") or 1) - 1),
                fetched=int(cp.get("fetched_count") or 0),
                saved=int(cp.get("saved_count") or 0),
                source_total=(None if int(cp.get("source_total") or -1) < 0 else int(cp.get("source_total"))),
                resumed=True,
            )
            return _result(cp, resumed=True)
        if verified_checkpoint(cp, schema_prepared=storage_prepared):
            if compact_complete and str(cp.get("dataset") or "") == "shopping_delivery":
                compact_verified_terminal_receipt(
                    cp,
                    schema_prepared=storage_prepared,
                    receipt_verified=True,
                )
                cp = lookup(dataset, scope) or cp
            _notify_progress(
                progress, "scope_complete", scope=scope,
                page=max(0, int(cp.get("page_no") or 1) - 1),
                fetched=int(cp.get("fetched_count") or 0),
                saved=int(cp.get("saved_count") or 0),
                source_total=(None if int(cp.get("source_total") or -1) < 0 else int(cp.get("source_total"))),
                resumed=True,
            )
            return _result(cp, resumed=True)
        if cp.get('status') == 'COMPLETE' or not _verified_checkpoint(
                cp, complete=False, schema_prepared=storage_prepared
        ):
            # A partial run can lose its current-RAW binding too, for example when
            # an overlapping anomalous page preserves a newer payload revision.
            # Keep receipts, but never continue using unproven counters.
            cp = None
    else:
        cp = None

    # This flag describes an actual continuation from a verified partial
    # checkpoint. Merely finding an invalid/stale checkpoint must not be reported
    # as a resume because that path intentionally starts a new generation.
    resumed_run = bool(cp is not None and observed is not None and resume)

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
    _notify_progress(
        progress, "scope_start", scope=scope,
        page=max(1, int(committed["page_no"])),
        fetched=int(committed["fetched_count"]),
        saved=int(committed["saved_count"]),
        source_total=(None if int(committed["source_total"]) < 0 else int(committed["source_total"])),
        resumed=resumed_run,
    )
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
            store_flags = [
                bool(preserve_filter(row)) if preserve_filter is not None else True
                for row in items
            ]
            if len(set(rowkeys)) != len(rowkeys):
                problem = 'DUPLICATE_OR_COLLIDING_SOURCE_ID'
            if len(items) > size:
                problem = 'OVERSIZED_PAGE'
            fetched = committed['fetched_count'] + len(items)
            saved = committed['saved_count'] + sum(1 for flag in store_flags if flag)
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
                for row, key, stored in zip(items, rowkeys, store_flags):
                    if stored:
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
                    conn.executemany(
                        'INSERT INTO vnext_collection_items(dataset,scope_key,generation,source_key,page_no,payload_sha256,stored) '
                        'VALUES(?,?,?,?,?,?,?)',
                        [(dataset, scope, generation, key, page, digest, 1 if stored else 0)
                         for key, digest, stored in zip(rowkeys, digests, store_flags)]
                    )
                    next_values = dict(committed, cursor_value=json.dumps(terminal, sort_keys=True),
                                       page_no=page + 1, source_total=total,
                                       fetched_count=fetched, saved_count=saved,
                                       status='COMPLETE' if done else 'RUNNING', last_error='')
                    if (
                        done
                        and compact_complete
                        and str(dataset) == "shopping_delivery"
                    ):
                        page_rows = conn.execute(
                            """SELECT page_no,page_size,response_hash,item_count,source_total,terminal_reason
                               FROM vnext_collection_pages
                               WHERE dataset=? AND scope_key=? AND generation=?
                               ORDER BY page_no""",
                            (dataset, scope, generation),
                        ).fetchall()
                        proof_cp = dict(
                            next_values,
                            dataset=dataset,
                            scope_key=scope,
                        )
                        terminal["compact_completion"] = _compact_marker(
                            terminal,
                            proof_cp,
                            page_rows,
                        )
                        next_values["cursor_value"] = json.dumps(
                            terminal, sort_keys=True
                        )
                    checkpoint(dataset, scope, _conn=conn, **next_values)
                    if (
                        done
                        and compact_complete
                        and str(dataset) == "shopping_delivery"
                    ):
                        conn.execute(
                            """DELETE FROM vnext_collection_items
                               WHERE dataset=? AND scope_key=? AND generation=?""",
                            (dataset, scope, generation),
                        )
                        conn.execute(
                            """DELETE FROM vnext_collection_pages
                               WHERE dataset=? AND scope_key=? AND generation=?""",
                            (dataset, scope, generation),
                        )
            if problem:
                _notify_progress(
                    progress, "page_stopped", scope=scope, page=page,
                    fetched=int(stopped["fetched_count"]), saved=int(stopped["saved_count"]),
                    source_total=(None if int(stopped["source_total"]) < 0 else int(stopped["source_total"])),
                    status="INCOMPLETE", error_code=str(problem),
                )
                return _result(
                    dict(stopped, dataset=dataset, scope_key=scope),
                    resumed=resumed_run,
                )
            committed = next_values
            m = terminal
            if len(items) == size:
                full_page_seen = True
            total_pages = ((int(total) + size - 1) // size) if int(total) > 0 else None
            _notify_progress(
                progress, "page_complete", scope=scope, page=page,
                total_pages=total_pages, page_items=len(items),
                fetched=int(committed["fetched_count"]), saved=int(committed["saved_count"]),
                source_total=(None if int(committed["source_total"]) < 0 else int(committed["source_total"])),
                status=str(committed["status"]), done=bool(done),
            )
            if done:
                _notify_progress(
                    progress, "scope_complete", scope=scope, page=page,
                    total_pages=total_pages, fetched=int(committed["fetched_count"]),
                    saved=int(committed["saved_count"]),
                    source_total=(None if int(committed["source_total"]) < 0 else int(committed["source_total"])),
                    resumed=resumed_run,
                )
                return _result(
                    dict(committed, dataset=dataset, scope_key=scope),
                    resumed=resumed_run,
                )
        except Exception as exc:
            _notify_progress(
                progress, "page_failed", scope=scope, page=page,
                fetched=int(committed["fetched_count"]), saved=int(committed["saved_count"]),
                source_total=(None if int(committed["source_total"]) < 0 else int(committed["source_total"])),
                error_type=type(exc).__name__,
            )
            with connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',
                                       (dataset, scope)).fetchone()
                if (current and current['cursor_value'] == committed['cursor_value']
                        and current['page_no'] == committed['page_no']
                        and current['fetched_count'] == committed['fetched_count']):
                    checkpoint(dataset, scope, _conn=conn,
                               **dict(committed, status='FAILED', last_error=_safe_error_label(exc)))
            raise
    return _result(
        dict(committed, dataset=dataset, scope_key=scope),
        resumed=resumed_run,
    )


def _result(cp, resumed=False):
    return {'dataset': cp['dataset'], 'scope': cp['scope_key'],
            'fetched': int(cp['fetched_count']), 'saved': int(cp['saved_count']),
            'source_total': None if int(cp['source_total']) < 0 else int(cp['source_total']),
            'complete': cp.get('status') == 'COMPLETE', 'resumed': resumed,
            'status': cp.get('status'), 'reason': cp.get('last_error', ''),
            'completion_reason': _meta(cp).get('completion_reason', '')}
