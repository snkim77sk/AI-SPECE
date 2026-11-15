"""Incrementally publish verified server pages. No source API calls or RAW writes."""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'lofin_candidate'))
from budget_projection_vnext import project_payload
from process_lock import LocalDbProcessLock

DATASETS = ('shopping_delivery', 'budget', 'budget_appropriation')
DEFAULT_CONFIG = Path('D:/G2B/web-sync-config.json')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def hashed(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def ro(path):
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)
    conn.row_factory = sqlite3.Row; conn.execute('PRAGMA query_only=ON'); conn.execute('PRAGMA cache_size=-2048')
    read_deadline(conn)
    return conn


def read_deadline(conn):
    deadline = time.monotonic() + 1.5
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)


def source_paths(base):
    paths = [base / 'data/g2b-local.sqlite3', base / 'isolated-prep-20261113/data/g2b-new-20261112.sqlite3']
    paths += sorted((base / 'live-bounded-20261114').glob('g2b-real-*.sqlite3'))
    paths += [base / 'lofin-collection/budget-2026.sqlite3']
    return [p for p in paths if p.is_file()]


def inventory(paths):
    result = []; errors = []
    for path in paths:
        try:
            with closing(ro(path)) as conn:
                for raw_cp in conn.execute('SELECT * FROM collection_checkpoints'):
                    cp = dict(raw_cp)
                    if cp['dataset'] not in DATASETS: continue
                    meta = json.loads(cp['cursor_value'] or '{}')
                    if meta.get('version') != 2 or not meta.get('generation'): continue
                    key = (cp['dataset'], cp['scope_key'], meta['generation'])
                    for raw_page in conn.execute('SELECT page_no,response_hash,item_count FROM vnext_collection_pages WHERE dataset=? AND scope_key=? AND generation=? ORDER BY page_no', key):
                        page = dict(raw_page)
                        revision = hashed(['public-projection-v1', *key, page])
                        metadata = hashed([cp['saved_count'], cp['source_total'], cp['status']])
                        result.append({'path': path, 'checkpoint': cp, 'generation': meta['generation'], 'page': page,
                                       'revision': revision, 'metadata': metadata,
                                       'key': canonical([*key[:2], page['page_no']]),
                                       'latest_page': page['page_no'] == cp['page_no'] - 1})
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            errors.append({'file': path.name, 'reason': type(exc).__name__})
    return result, errors


def money(value):
    if value is None or isinstance(value, bool) or not str(value).strip(): return None
    try:
        number = Decimal(str(value).replace(',', ''))
        return str(number) if number.is_finite() else None
    except InvalidOperation: return None


def project(dataset, key, day, payload):
    result = {'source_key': hashed([dataset, key]), 'source_date': day}
    if dataset == 'shopping_delivery':
        mapping = {'item_name': 'prdctIdntNoNm', 'vendor_name': 'corpNm', 'demand_org': 'dminsttNm',
                   'product_number': 'prdctIdntNo', 'contract_number': 'cntrctNo', 'quantity': 'prdctQty', 'unit_price': 'prdctUprc'}
        result.update({name: payload.get(source) for name, source in mapping.items()})
        result['amount'] = money(payload.get('prdctAmt'))
        # Do not guess a lighting classification or turn an unknown amount into zero.
        result['primary_category'] = ''
    else:
        fact = project_payload(dataset, payload, source_date=day)
        result.update({name: fact.get(source) for name, source in {
            'fiscal_year': 'fiscal_year', 'org_name': 'org_name', 'region_name': 'region_name',
            'department_name': 'dept_name', 'project_name': 'project_name', 'account_name': 'account_name'}.items()})
        budget = money(payload.get('bdg_cash_amt' if dataset == 'budget' else 'biz_bdg_tott_amt'))
        executed = money(payload.get('ep_amt')) if dataset == 'budget' else None
        result.update(budget_amount=budget, executed_amount=executed,
                      remaining_amount=str(Decimal(budget)-Decimal(executed)) if budget is not None and executed is not None else None,
                      administration_budget=money(payload.get('padm_oper_exps_tott_amt')),
                      financial_activity_budget=money(payload.get('fin_acv_tott_amt')),
                      source_layer='QWGJK' if dataset == 'budget' else 'AIDFA', unit_verified=False)
    return result


def export_page(item, projector=None):
    projector = projector or project
    cp = item['checkpoint']; page = item['page']; dataset = cp['dataset']
    scope = cp['scope_key'].split(':')[0] if dataset == 'shopping_delivery' else cp['scope_key']
    day = scope if dataset == 'shopping_delivery' else scope.split(':')[-1] if dataset == 'budget' else scope[:4]
    key = (dataset, cp['scope_key'], item['generation'], page['page_no'])
    with closing(ro(item['path'])) as conn:
        pairs = [tuple(row) for row in conn.execute('SELECT source_key,payload_sha256 FROM vnext_collection_items WHERE dataset=? AND scope_key=? AND generation=? AND page_no=? ORDER BY source_key,payload_sha256 LIMIT 1000', key)]
        if len(pairs) != page['item_count'] or len(pairs) > 999 or hashlib.sha256(json.dumps(pairs).encode()).hexdigest() != page['response_hash']:
            raise ValueError('INVALID_PAGE_RECEIPT')
        rows = []
        for offset in range(0, len(pairs), 64):
            chunk = pairs[offset:offset+64]
            # Each exhausted SELECT releases its read lock. Validate each raw
            # hash against the fixed receipt; no long page-wide transaction.
            read_deadline(conn)
            batch = conn.execute('SELECT source_key,payload_sha256,payload_json FROM raw_records WHERE dataset=? AND source_key IN (' + ','.join('?' for _ in chunk) + ')', [dataset, *[p[0] for p in chunk]]).fetchall()
            raw = {row[0]: (row[1], row[2]) for row in batch}
            for source_key, digest in chunk:
                found = raw.get(source_key)
                if found is None or found[0] != digest: raise ValueError('CURRENT_RAW_CHANGED')
                body = found[1]
                if hashlib.sha256(body.encode()).hexdigest() != digest: raise ValueError('RAW_HASH_MISMATCH')
                rows.append(projector(dataset, source_key, day, json.loads(body)))
    return {'schema_version': 2, 'dataset': dataset, 'scope': scope, 'page_no': page['page_no'], 'revision': item['revision'],
            'rows': rows, 'source_saved_count': cp['saved_count'], 'source_total': cp['source_total'], 'source_status': cp['status'],
            'generated_at_utc': datetime.now(timezone.utc).isoformat()}


def load_config(path):
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    parsed = urllib.parse.urlsplit(value['server_url'])
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('HTTPS_ORIGIN_REQUIRED')
    if value.get('enabled') is not True: raise ValueError('WEB_SYNC_NOT_ENABLED')
    return value


def load_token(config):
    helper = Path('D:/G2B/isolated-prep-20261113/program/secure_key_handoff.py')
    spec = importlib.util.spec_from_file_location('existing_dpapi_for_web_sync', helper)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    container = json.loads(Path(config['token_file']).read_text(encoding='utf-8-sig'))
    if container.get('format') != 'windows-dpapi-current-user-v1': raise ValueError('TOKEN_CONTAINER_INVALID')
    value = module.dpapi(base64.b64decode(container['ciphertext'], validate=True), False).decode('ascii')
    if not 32 <= len(value) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in value): raise ValueError('SYNC_TOKEN_INVALID')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('SYNC_REDIRECT_REFUSED')


def send_page(origin, token, payload, timeout=8):
    body = gzip.compress(canonical(payload).encode())
    if len(body) > 4*1024*1024: raise ValueError('SYNC_PAGE_TOO_LARGE')
    request = urllib.request.Request(origin.rstrip('/') + '/api/sync-page', data=body, method='POST',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        ack = json.loads(response.read(8193))
    if (not isinstance(ack, dict) or ack.get('ok') is not True
            or ack.get('digest') != hashed(payload) or ack.get('revision') != payload['revision']
            or type(ack.get('stored_rows')) is not int or ack['stored_rows'] != len(payload['rows'])):
        raise ValueError('SYNC_RECEIPT_MISMATCH')
    return ack


def run(config, sender=send_page, token_loader=load_token):
    started = time.monotonic(); root = Path(config['state_directory']); root.mkdir(parents=True, exist_ok=True)
    report = {'source_api_calls': 0, 'attempted_pages': 0, 'confirmed_pages': 0, 'confirmed_rows': 0, 'errors': [], 'state': 'RUNNING'}
    with LocalDbProcessLock(root / 'sync'):
        with closing(sqlite3.connect(root / 'web-sync.sqlite3')) as ledger:
            ledger.execute('CREATE TABLE IF NOT EXISTS receipts(key TEXT PRIMARY KEY,revision TEXT,metadata TEXT,digest TEXT,confirmed_at TEXT)')
            items, report['errors'] = inventory(source_paths(Path(config.get('source_root', 'D:/G2B'))))
            queues = {d: [] for d in DATASETS}
            for item in sorted(items, key=lambda i: (i['checkpoint']['scope_key'], -i['page']['page_no']), reverse=True):
                previous = ledger.execute('SELECT revision,metadata FROM receipts WHERE key=?', (item['key'],)).fetchone()
                if previous and previous[0] == item['revision'] and (not item['latest_page'] or previous[1] == item['metadata']): continue
                queues[item['checkpoint']['dataset']].append(item)
            token = token_loader(config); blocked = set()
            while report['attempted_pages'] < 8 and time.monotonic()-started < 75 and any(queues.values()):
                for dataset in DATASETS:
                    if report['attempted_pages'] >= 8 or time.monotonic()-started >= 75: break
                    if not queues[dataset] or dataset in blocked: continue
                    item = queues[dataset].pop(0)
                    try:
                        payload = export_page(item)
                        report['attempted_pages'] += 1
                        ack = sender(config['server_url'], token, payload)
                        with ledger:
                            ledger.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?)', (item['key'], item['revision'], item['metadata'], ack['digest'], datetime.now(timezone.utc).isoformat()))
                        report['confirmed_pages'] += 1; report['confirmed_rows'] += len(payload['rows'])
                    except (ValueError, sqlite3.Error, OSError, urllib.error.URLError) as exc:
                        report['errors'].append({'dataset': dataset, 'reason': type(exc).__name__})
                        blocked.add(dataset); queues[dataset].clear()
                if not any(queues.values()) or all(d in blocked or not queues[d] for d in DATASETS): break
    report.update(state='VERIFIED' if not report['errors'] else 'CONTINUING_WITH_SYNC_ERROR',
                  finished_at_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic()-started, 3))
    temp = root / 'status.tmp'; temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8'); temp.replace(root / 'status.json')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--controlled', action='store_true')
    args = parser.parse_args()
    try:
        if args.controlled and sys.stdin.buffer.readline() != b'GO\n': raise ValueError('PARENT_HANDSHAKE_REQUIRED')
        result = run(load_config(args.config)); print(json.dumps(result))
    except Exception as exc:
        # Never print URLs, credentials, exception text or source records.
        print(json.dumps({'state': 'SYNC_NOT_STARTED', 'reason': type(exc).__name__, 'source_api_calls': 0}))
        raise SystemExit(1) from None
