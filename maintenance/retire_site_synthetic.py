"""Bounded owner-authorized retirement; credentials remain DPAPI protected."""
import gzip
import hashlib
import json
import os
import http.client
import io
from pathlib import Path
import sys
import ssl
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, 'D:/G2B/gpt-site-sync-v5.2.0')
from gpt_sync_client import load_config, load_token, DEFAULT_CONFIG, SITE_ORIGIN, NoRedirect

ARCHIVE = Path('D:/G2B/maintenance/synthetic-retirement-5.2.5')
ENDPOINT = '/api/maintenance/synthetic'
MAX_BYTES = 1048576

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def durable(path, content):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('wb') as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)

def run():
    config = load_config(DEFAULT_CONFIG)
    if config['server_url'].rstrip('/') != SITE_ORIGIN:
        raise ValueError('EXACT_PRIVATE_SITE_REQUIRED')
    token = load_token(config)
    connection = http.client.HTTPSConnection(urllib.parse.urlsplit(SITE_ORIGIN).hostname,
                                             timeout=40, context=ssl.create_default_context())
    counts = {'get': 0, 'post': 0, 'source_api_calls': 0}
    def request(path, body=None):
        headers = {'OAI-Sites-Authorization': 'Bearer ' + token}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        for attempt in range(3 if body is None else 1):
            counts['get' if body is None else 'post'] += 1
            try:
                connection.request('GET' if body is None else 'POST', path,
                                   body=canonical(body) if body is not None else None, headers=headers)
                response = connection.getresponse()
                payload = response.read(MAX_BYTES + 1)
                if response.status != 200:
                    raise urllib.error.HTTPError(SITE_ORIGIN + path, response.status, 'REQUEST_FAILED', {}, io.BytesIO(payload))
                break
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError, http.client.HTTPException):
                connection.close()
                if body is not None or attempt == 2:
                    raise
                time.sleep(0.5)
        if len(payload) > MAX_BYTES:
            raise ValueError('RESPONSE_TOO_LARGE')
        return json.loads(payload)

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    before = request(ENDPOINT)
    if before['version'] != '5.2.1':
        raise ValueError('VERSION_MISMATCH')
    initial = ARCHIVE / 'before.json'
    if not initial.exists():
        durable(initial, canonical(before))
    total = 0
    for table in before['tables']:
        name = table['table']
        last_ids = set()
        deleted = 0
        while True:
            batch = request(ENDPOINT + '?' + urllib.parse.urlencode({'table': name}))
            ids = [r['_rowid'] for r in batch['rows']]
            if set(ids) & last_ids:
                raise ValueError('DELETE_READBACK_FAILED')
            if not ids:
                break
            backup_value = {k: batch[k] for k in ('version', 'table', 'rows')}
            if digest(backup_value) != batch['hash']:
                raise ValueError('BACKUP_HASH_MISMATCH')
            path = ARCHIVE / (name + '-' + batch['hash'] + '.json.gz')
            if not path.exists():
                durable(path, gzip.compress(canonical(batch), mtime=0))
            backed_up = json.loads(gzip.decompress(path.read_bytes()))
            if digest({k: backed_up[k] for k in ('version', 'table', 'rows')}) != batch['hash']:
                raise ValueError('DURABLE_BACKUP_MISMATCH')
            mutation = {'version': batch['version'], 'table': name, 'ids': ids, 'hash': batch['hash']}
            pending = ARCHIVE / 'pending.json'
            durable(pending, canonical(mutation))
            try:
                result = request(ENDPOINT, mutation)
            except urllib.error.HTTPError:
                raise
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError, http.client.HTTPException):
                # Resolve the uncertain write before considering any retry.
                probe = request(ENDPOINT + '?' + urllib.parse.urlencode({'table': name, 'after': min(ids)-1}))
                remaining = set(ids) & {r['_rowid'] for r in probe['rows']}
                if not remaining:
                    result = {'ok': True, 'deleted': len(ids), 'resolved_by_read': True}
                elif remaining == set(ids) and probe['hash'] == batch['hash']:
                    result = request(ENDPOINT, mutation)
                else:
                    raise ValueError('UNCERTAIN_WRITE_REVIEW_REQUIRED') from None
            if result.get('ok') is not True or result.get('deleted') != len(ids):
                raise ValueError('DELETE_ACK_MISMATCH')
            deleted += len(ids)
            total += len(ids)
            last_ids = set(ids)
            durable(ARCHIVE / 'progress.json', canonical({'table': name, 'deleted_this_run': total, 'last_hash': batch['hash'], 'requests': counts}))
        print(json.dumps({'table': name, 'deleted': deleted, 'remaining': 0}), flush=True)
    after = request(ENDPOINT)
    if after['total'] != 0:
        raise ValueError('SYNTHETIC_ROWS_REMAIN')
    backed_up_counts = {}
    identities = {}
    for path in ARCHIVE.glob('*.json.gz'):
        batch = json.loads(gzip.decompress(path.read_bytes()))
        if digest({k: batch[k] for k in ('version', 'table', 'rows')}) != batch['hash']:
            raise ValueError('FINAL_BACKUP_HASH_MISMATCH')
        for row in batch['rows']:
            key = (batch['table'], row['_rowid'])
            row_hash = digest(row)
            if key in identities:
                if identities[key] != row_hash:
                    raise ValueError('BACKUP_IDENTITY_CONFLICT')
                continue
            identities[key] = row_hash
            backed_up_counts[batch['table']] = backed_up_counts.get(batch['table'], 0) + 1
    connection.close()
    result = {'version': '5.2.6', 'backup_protocol_version': '5.2.1', 'finished_at_utc': datetime.now(timezone.utc).isoformat(), 'deleted_this_run': total,
              'deleted_backed_up_rows': sum(backed_up_counts.values()), 'backed_up_counts': backed_up_counts,
              'before': json.loads(initial.read_bytes()), 'run_before': before, 'after': after, 'requests': counts, 'backup_files': len(list(ARCHIVE.glob('*.json.gz'))),
              'backup_bytes': sum(p.stat().st_size for p in ARCHIVE.glob('*.json.gz')),
              'backup_directory': str(ARCHIVE), 'provider_calls': 0}
    durable(ARCHIVE / 'result.json', canonical(result))
    durable(Path(__file__).parent / 'evidence' / 'synthetic-retirement-result.json', canonical(result))
    print(json.dumps({k: v for k, v in result.items() if k not in ('before', 'after', 'run_before', 'backed_up_counts')}), flush=True)

if __name__ == '__main__':
    try:
        run()
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read(4096)).get('error', 'UNKNOWN')
        print(json.dumps({'stopped': True, 'http_status': error.code, 'error': detail if str(detail).startswith('RETIREMENT_') else 'HTTP_ERROR', 'source_api_calls': 0}), flush=True)
        raise SystemExit(1) from None
    except Exception as error:
        print(json.dumps({'stopped': True, 'reason': type(error).__name__, 'source_api_calls': 0}), flush=True)
        raise SystemExit(1) from None
