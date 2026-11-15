"""Read-only validation with memory bounded to one receipt page (at most 999 rows)."""
import hashlib
import json


def verify_receipt(conn, cp, *, complete, require_current_raw=True):
    statuses = ('COMPLETE',) if complete else ('RUNNING', 'FAILED', 'INCOMPLETE')
    if not cp or cp.get('status') not in statuses:
        return False
    try:
        meta = json.loads(cp.get('cursor_value') or '{}')
        size, next_page, fetched, saved, source_total = (
            int(cp[k]) for k in ('page_size', 'page_no', 'fetched_count', 'saved_count', 'source_total'))
        generation = meta['generation']
    except (KeyError, ValueError, TypeError):
        return False
    if (meta.get('version') != 2 or not isinstance(generation, str) or not generation
            or not 1 <= size <= 999 or meta.get('page_size') != size or fetched < 0 or saved != fetched):
        return False
    key = (cp['dataset'], cp['scope_key'], generation)
    if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
                    "('vnext_collection_pages','vnext_collection_items')").fetchone()[0] != 2:
        return False
    count_pages = conn.execute('SELECT COUNT(*) FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=?', key).fetchone()[0]
    count_items = conn.execute('SELECT COUNT(*) FROM vnext_collection_items WHERE dataset=? AND scope_key=? AND generation=?', key).fetchone()[0]
    if next_page != count_pages + 1 or fetched != count_items:
        return False
    for table in ('raw_record_revisions', 'raw_records') if require_current_raw else ('raw_record_revisions',):
        covered=(' INDEXED BY ix_raw_records_receipt' if table=='raw_records' and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_raw_records_receipt'").fetchone() else '')
        if conn.execute(f'''SELECT 1 FROM vnext_collection_items i LEFT JOIN {table} r{covered}
                ON r.dataset=i.dataset AND r.source_key=i.source_key AND r.payload_sha256=i.payload_sha256
                WHERE i.dataset=? AND i.scope_key=? AND i.generation=? AND r.id IS NULL LIMIT 1''', key).fetchone():
            return False
    count, total, full_page_seen, reason = 0, -1, False, ''
    pages = conn.execute('SELECT * FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=? ORDER BY page_no', key)
    for number, page in enumerate(pages, 1):
        pairs = [tuple(row) for row in conn.execute(
            'SELECT source_key,payload_sha256 FROM vnext_collection_items WHERE dataset=? AND scope_key=? '
            'AND generation=? AND page_no=? ORDER BY source_key,payload_sha256 LIMIT ?', (*key, page['page_no'], size+1))]
        item_count = len(pairs)
        digest = hashlib.sha256(json.dumps(pairs).encode()).hexdigest()
        if page['page_no'] != number or page['page_size'] != size or page['item_count'] != item_count or item_count > size or digest != page['response_hash']:
            return False
        reported = page['source_total']
        if reported != total and (total > 0 or reported <= 0):
            return False
        total = reported
        count += item_count
        if total > 0 and (count > total or (not item_count and count < total)):
            return False
        done = count == total if total > 0 else (not item_count or (item_count < size and full_page_seen))
        reason = ('TOTAL_REACHED' if done and total > 0 else 'EMPTY_PAGE' if done and not item_count
                  else 'SHORT_PAGE_UNKNOWN_TOTAL' if done else '')
        if page['terminal_reason'] != reason or (done and number != count_pages):
            return False
        full_page_seen = full_page_seen or item_count == size
    return count == fetched and total == source_total and reason == meta.get('completion_reason','') and bool(reason) == complete


def readback(path, day, expected_page=None):
    """Open a new read-only connection after commit; don't call schema initialization."""
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        row = conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',
                           ('shopping_delivery', day+':'+day)).fetchone()
        if row is None:
            return None
        cp = dict(row)
        complete = cp['status'] == 'COMPLETE'
        if not verify_receipt(conn, cp, complete=complete):
            raise ValueError('RECEIPT_INVALID')
        if expected_page is not None and cp['page_no'] != expected_page+1:
            raise ValueError('CHECKPOINT_DID_NOT_ADVANCE_ONCE')
        generation = json.loads(cp['cursor_value'])['generation']
        page = conn.execute('SELECT item_count FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=? AND page_no=?',
                            (cp['dataset'],cp['scope_key'],generation,cp['page_no']-1)).fetchone()
        return {'next_page':cp['page_no'], 'saved':cp['saved_count'], 'page_rows':page[0] if page else 0,
                'complete':complete, 'status':cp['status'], 'verified':True}
