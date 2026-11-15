"""Page-sized authenticated ingestion; browser reads never access source APIs."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

DATASETS = {'shopping_delivery', 'budget', 'budget_appropriation'}
SECTIONS = {'shopping': 'shopping_delivery', 'vendors': 'shopping_delivery',
            'budget_details': 'budget', 'budget_structure': 'budget_appropriation'}
FIELDS = set('source_key source_date fetched_at item_name vendor_name demand_org amount primary_category product_number contract_number quantity unit_price fiscal_year org_name region_name department_name project_name account_name budget_amount executed_amount remaining_amount administration_budget financial_activity_budget source_layer unit_verified'.split())
SCHEMA = '''
CREATE TABLE query_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE query_pages(dataset TEXT,scope TEXT,page INTEGER,revision TEXT NOT NULL,
    digest TEXT NOT NULL,row_count INTEGER NOT NULL,source_saved INTEGER NOT NULL,
    source_total INTEGER NOT NULL,source_state TEXT NOT NULL,generated_at TEXT NOT NULL,
    received_at TEXT NOT NULL,PRIMARY KEY(dataset,scope,page));
CREATE TABLE query_rows(dataset TEXT,scope TEXT,page INTEGER,source_key TEXT,
    source_date TEXT,category TEXT,organization TEXT,vendor TEXT,search TEXT,payload TEXT,
    PRIMARY KEY(dataset,scope,source_key));
CREATE INDEX query_page_rows ON query_rows(dataset,scope,page);
CREATE INDEX query_date ON query_rows(dataset,source_date DESC,scope,source_key);
CREATE INDEX query_category ON query_rows(dataset,category,source_date DESC);
CREATE INDEX query_vendor ON query_rows(dataset,vendor);
CREATE INDEX query_source_key ON query_rows(dataset,source_key);
CREATE INDEX query_search ON query_rows(dataset,scope,search,source_date,source_key,category,vendor);
'''


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def path():
    return Path(os.environ.get('G2B_RESULT_DATA_DIR', '/app/user_data/g2b-result-view')) / 'g2b-query-v2.sqlite3'


def connect(write=False):
    target = path()
    if not write and not target.is_file():
        return None
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target) if write else target.resolve().as_uri() + '?mode=ro', uri=not write, timeout=2)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA cache_size=-2048')
    if write:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names and names != {'query_meta', 'query_pages', 'query_rows'}:
            conn.close(); raise ValueError('UNEXPECTED_QUERY_DATABASE')
        if not names:
            conn.executescript(SCHEMA)
    else:
        conn.execute('PRAGMA query_only=ON')
    return conn


def validate(p):
    if not isinstance(p, dict) or p.get('schema_version') != 2 or type(p.get('schema_version')) is not int:
        raise ValueError('SCHEMA_2_REQUIRED')
    dataset = p.get('dataset'); scope = p.get('scope')
    allowed = {'schema_version','dataset','scope','page_no','revision','rows','source_saved_count','source_total','source_status','generated_at_utc'}
    if set(p) != allowed: raise ValueError('INVALID_PAGE_FIELDS')
    if not isinstance(dataset, str) or dataset not in DATASETS or not isinstance(scope, str) or len(scope) > 32:
        raise ValueError('INVALID_SCOPE')
    if dataset == 'shopping_delivery':
        dt.date.fromisoformat(scope)
    elif dataset == 'budget':
        year, day = scope.split(':'); stamp = dt.date.fromisoformat(day)
        if year != str(stamp.year): raise ValueError('INVALID_FISCAL_YEAR')
    elif not re.fullmatch(r'20\d\d:ALL', scope):
        raise ValueError('INVALID_APPROPRIATION_SCOPE')
    for name in ('page_no', 'source_saved_count', 'source_total'):
        if type(p.get(name)) is not int or not 0 <= p[name] <= 10_000_000:
            raise ValueError('INVALID_COUNTS')
    if not 1 <= p['page_no'] <= 100_000 or p['source_total'] and p['source_saved_count'] > p['source_total']:
        raise ValueError('INVALID_PAGE')
    if p.get('source_status') not in ('COMPLETE', 'RUNNING', 'FAILED', 'INCOMPLETE'):
        raise ValueError('INVALID_SOURCE_STATUS')
    if not re.fullmatch('[a-f0-9]{64}', str(p.get('revision', ''))):
        raise ValueError('INVALID_REVISION')
    if not isinstance(p['generated_at_utc'], str): raise ValueError('INVALID_TIMESTAMP')
    stamp = dt.datetime.fromisoformat(p['generated_at_utc'])
    if stamp.tzinfo is None or stamp > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise ValueError('INVALID_TIMESTAMP')
    rows = p.get('rows')
    if not isinstance(rows, list) or len(rows) > 999 or len(rows) > p['source_saved_count']:
        raise ValueError('ROW_LIMIT')
    keys = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - FIELDS or not re.fullmatch('[a-f0-9]{64}', str(row.get('source_key', ''))):
            raise ValueError('INVALID_PUBLIC_ROW')
        if row['source_key'] in keys: raise ValueError('DUPLICATE_SOURCE_KEY')
        keys.add(row['source_key'])
        if any(v is not None and type(v) not in (str, int, float, bool) for v in row.values()) or len(canonical(row).encode()) > 16_384:
            raise ValueError('INVALID_FIELD')
        if str(row.get('source_date', '')) != (scope if dataset == 'shopping_delivery' else scope.split(':')[-1] if dataset == 'budget' else scope[:4]):
            raise ValueError('ROW_SCOPE_MISMATCH')
    return stamp.astimezone(dt.timezone.utc).isoformat()


def install_page(payload):
    stamp = validate(payload)
    digest = hashlib.sha256(canonical(payload).encode()).hexdigest()
    key = (payload['dataset'], payload['scope'], payload['page_no'])
    conn = connect(True)
    try:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT * FROM query_pages WHERE dataset=? AND scope=? AND page=?', key).fetchone()
        if old and old['digest'] == digest:
            return {'ok': True, 'unchanged': True, 'digest': digest, 'revision': old['revision'], 'stored_rows': old['row_count']}
        if old and dt.datetime.fromisoformat(stamp) <= dt.datetime.fromisoformat(old['generated_at']):
            raise ValueError('STALE_PAGE')
        conn.execute('DELETE FROM query_rows WHERE dataset=? AND scope=? AND page=?', key)
        for row in payload['rows']:
            # A key already owned by another page is an upstream overlap, not an update.
            if conn.execute('SELECT 1 FROM query_rows WHERE dataset=? AND scope=? AND source_key=?', (*key[:2], row['source_key'])).fetchone():
                raise ValueError('OVERLAPPING_PAGE')
            text = ' '.join(str(value) for value in row.values() if value is not None).casefold()
            conn.execute('INSERT INTO query_rows VALUES(?,?,?,?,?,?,?,?,?,?)', (*key, row['source_key'], row['source_date'],
                         row.get('primary_category', ''), row.get('org_name', row.get('demand_org', '')),
                         row.get('vendor_name', ''), text, canonical(row)))
        received = dt.datetime.now(dt.timezone.utc).isoformat()
        conn.execute('INSERT OR REPLACE INTO query_pages VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (*key, payload['revision'], digest, len(payload['rows']), payload['source_saved_count'],
                      payload['source_total'], payload['source_status'], stamp, received))
        conn.execute("INSERT OR REPLACE INTO query_meta VALUES('revision',?)", (received,))
        conn.commit()
        return {'ok': True, 'unchanged': False, 'digest': digest, 'revision': payload['revision'], 'stored_rows': len(payload['rows'])}
    except BaseException:
        conn.rollback(); raise
    finally:
        conn.close()


def revision():
    conn = connect()
    if conn is None: return ''
    try:
        row = conn.execute("SELECT value FROM query_meta WHERE key='revision'").fetchone()
        return row[0] if row else ''
    finally: conn.close()


def read_data(query):
    section = query.get('section', ['shopping'])[0]
    dataset = SECTIONS.get(section)
    if dataset is None: return None
    conn = connect()
    if conn is None: return None
    try:
        conn.execute('BEGIN')
        started = time.monotonic()
        conn.set_progress_handler(lambda: int(time.monotonic() - started > 3), 10_000)
        if not conn.execute('SELECT 1 FROM query_pages WHERE dataset=? LIMIT 1', (dataset,)).fetchone(): return None
        scope = None
        if dataset != 'shopping_delivery':
            scope = conn.execute('SELECT MAX(scope) FROM query_pages WHERE dataset=?', (dataset,)).fetchone()[0]
        q = query.get('q', [''])[0][:150].casefold()
        category = query.get('category', [''])[0][:40]
        page = max(1, min(200_000, int(query.get('page', ['1'])[0])))
        where = 'dataset=?'; args = [dataset]
        if scope is not None: where += ' AND scope=?'; args.append(scope)
        source = 'query_rows'
        if re.fullmatch('[a-f0-9]{64}', q):
            where += ' AND source_key=?'; args.append(q)
        elif q:
            # Scan a covering text index, then fetch only this page's payloads.
            # Source JSON never has to be scanned/deserialized for a search.
            source += ' INDEXED BY query_search'
            where += ' AND instr(search,?)>0'; args.append(q)
        if category: where += ' AND category=?'; args.append(category)
        count = conn.execute('SELECT COUNT(*) FROM ' + source + ' WHERE ' + where, args).fetchone()[0]
        selected = 'SELECT rowid,source_date,scope,source_key FROM ' + source + ' WHERE ' + where + ' ORDER BY source_date DESC,scope,source_key LIMIT 50 OFFSET ?'
        rows = [json.loads(row[0]) for row in conn.execute('SELECT r.payload FROM (' + selected + ') page JOIN query_rows r ON r.rowid=page.rowid ORDER BY page.source_date DESC,page.scope,page.source_key', [*args, (page - 1) * 50])]
        if section == 'vendors':
            # Preserve a bounded, public vendor lookup; no misleading amount totals.
            count = conn.execute('SELECT COUNT(DISTINCT vendor) FROM ' + source + ' WHERE ' + where, args).fetchone()[0]
            rows = [{'vendor_name': row[0], 'item_name': '납품자료 ' + str(row[1]) + '건'} for row in conn.execute('SELECT vendor,COUNT(*) FROM ' + source + ' WHERE ' + where + ' GROUP BY vendor ORDER BY vendor LIMIT 50 OFFSET ?', [*args, (page - 1) * 50])]
        status_where = 'dataset=?' + (' AND scope=?' if scope else '')
        status_args = [dataset, scope] if scope else [dataset]
        scopes = []
        for source in conn.execute('SELECT scope,MAX(generated_at) stamp,MAX(received_at) received FROM query_pages WHERE ' + status_where + ' GROUP BY scope', status_args):
            latest = conn.execute('SELECT source_saved,source_total,source_state FROM query_pages WHERE dataset=? AND scope=? ORDER BY generated_at DESC LIMIT 1', (dataset, source['scope'])).fetchone()
            synced = conn.execute('SELECT COUNT(*) FROM query_rows WHERE dataset=? AND scope=?', (dataset, source['scope'])).fetchone()[0]
            scopes.append({'scope': source['scope'], 'state': latest['source_state'], 'saved_count': latest['source_saved'], 'source_total': latest['source_total'], 'web_rows': synced, 'received_at_utc': source['received']})
        total = sum(s['web_rows'] for s in scopes)
        complete = all(s['state'] == 'COMPLETE' and s['web_rows'] == s['saved_count'] for s in scopes)
        last = max(s['received_at_utc'] for s in scopes)
        rev = conn.execute("SELECT value FROM query_meta WHERE key='revision'").fetchone()[0]
        stage = {'dataset': dataset, 'state': 'COMPLETE' if complete else 'PARTIAL',
                 'saved_count': sum(s['saved_count'] for s in scopes), 'source_total': sum(s['source_total'] for s in scopes)}
        manifest = {'received_at_utc': last, 'generated_at_utc': last, 'row_counts': {section: total},
                    'collection_status': {'stages': [stage]}, 'scopes': scopes,
                    'latest_source_date': scope[:4] if dataset == 'budget_appropriation' else scope.split(':')[-1] if scope else max(s['scope'] for s in scopes),
                    'web_rows': total, 'revision': rev}
        return {'manifest': manifest, 'rows': rows, 'count': count, 'page': page, 'section': section,
                'collection_controls_enabled': False, 'source_api_calls': 0, 'unit_verified': False}
    finally:
        conn.close()
