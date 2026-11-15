"""Publish verified Windows pages to the existing private GPT Site, no source API."""
import argparse
from contextlib import closing
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import re
import urllib.error
import urllib.request
from web_sync_client import (canonical, hashed, inventory, source_paths, export_page,
    load_config, load_token, NoRedirect, LocalDbProcessLock, DATASETS)
from collector_status import collect_status

PROTOCOL = 'windows-verified-pages-v1'
VERSION = '5.2.15'
SITE_ORIGIN = 'https://g2b-collection-trial.snkim77-sk.chatgpt.site'
DEFAULT_CONFIG = Path('D:/G2B/gpt-sync-config.json')
PUBLIC_FIELDS = {
    'shopping_delivery': set('IntlCntrctDlvrReqDate brnofceNm cnstwkMtrlDrctPurchsObjYn cntrctChgOrd cntrctCnclsStleNm cntrctCorpBizno cntrctNo corpEntrprsDivNmNm corpNm dlvrReqAmt dlvrReqChgOrd dlvrReqNm dlvrReqNo dlvrReqQty dlvrReqRcptDate dlvrTmlmtDate dminsttBizno dminsttCd dminsttNm dminsttRgnNm dmndInsttDivNm dtilPrdctClsfcNo dtilPrdctClsfcNoNm exclcProdctYn fnlDlvrReqYn incdecAmt incdecQty masYn optnDivCdNm prdctAmt prdctClsfcNo prdctClsfcNoNm prdctIdntNo prdctIdntNoNm prdctQty prdctSno prdctUnit prdctUprc smetprCmptProdctYn modelNm prdctSpecNm'.split()),
    'budget': set('acnt_dv_cd acnt_dv_nm ane_part_cd bdg_cash_amt bdg_ntep capep cpl_amt dbiz_cd dbiz_nm dept_cd dept_nm dpt_cd dpt_nm ep_amt etc_amt exe_ymd fld_cd fld_nm fyr laf_cd laf_hg_nm lup_ord padm_laf_cd part_nm sggep wa_laf_cd wa_laf_hg_nm zon_cd'.split()),
    'budget_appropriation': set('acnt_dv_cd acnt_dv_nm biz_bdg_prsm_amt biz_bdg_tott_amt fin_acv_prsm_amt fin_acv_tott_amt fld_cd fld_nm fyr laf_cd laf_hg_nm padm_oper_exps_tott_amt padm_oper_prsm_exps sect_cd sect_nm wa_laf_cd wa_laf_hg_nm'.split()),
}


def public_source(dataset, key, day, raw):
    return {k:v for k,v in raw.items() if k in PUBLIC_FIELDS[dataset]}


def export_server_page(item):
    page = export_page(item, projector=public_source)
    cp = item['checkpoint']
    observed = datetime.fromisoformat(cp['updated_at'].replace('Z', '+00:00'))
    if observed.tzinfo is None: observed = observed.replace(tzinfo=timezone.utc)
    day = (observed.astimezone(timezone(timedelta(hours=9))).date().isoformat()
           if cp['dataset'] == 'budget_appropriation' else page['scope'].split(':')[-1])
    return {'protocol': PROTOCOL, 'dataset': cp['dataset'], 'scope': page['scope'],
        'generation': item['generation'], 'page_no': page['page_no'], 'revision': page['revision'],
        'snapshot_date': day, 'source_updated_at': observed.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'source_saved_count': cp['saved_count'], 'source_total': cp['source_total'],
        'source_status': cp['status'], 'rows': page['rows']}


def page_id(payload):
    return hashed([payload[k] for k in ('dataset', 'scope', 'generation', 'page_no')])


def validate_receipt(receipt, payload):
    if (not isinstance(receipt, dict) or receipt.get('id') != page_id(payload)
            or receipt.get('revision') != payload['revision']
            or receipt.get('source_rows') != len(payload['rows'])
            or receipt.get('dataset') != payload['dataset']):
        raise ValueError('SITE_RECEIPT_MISMATCH')
    return receipt


class SiteHTTPError(OSError):
    """Only an HTTP status and a public error code; no URL, token or body."""
    def __init__(self,status,code='SITE_HTTP_ERROR'):
        self.http_status=int(status)
        self.safe_code=code if re.fullmatch(r'SYNC_[A-Z_]{1,64}',code) else 'SITE_HTTP_ERROR'
        super().__init__(self.safe_code)


def utc_now():
    return datetime.now(timezone.utc)


def defer_rejected_page(db,ident,revision,clock,reason):
    row=db.execute('SELECT failures FROM sync_page_failures WHERE id=? AND revision=?',(ident,revision)).fetchone()
    failures=(row[0] if row else 0)+1
    retry_at=(clock+timedelta(minutes=(15,30,60)[min(failures-1,2)])).isoformat()
    with db:
        db.execute('''INSERT INTO sync_page_failures VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            revision=excluded.revision,failures=excluded.failures,next_retry_at=excluded.next_retry_at,reason=excluded.reason''',
            (ident,revision,failures,retry_at,reason))
    return retry_at


class SiteClient:
    def __init__(self, origin, token, timeout=8):
        if origin.rstrip('/') != SITE_ORIGIN: raise ValueError('EXACT_PRIVATE_SITE_REQUIRED')
        self.origin, self.token, self.timeout = origin.rstrip('/'), token, timeout
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, path, payload=None):
        body = canonical(payload).encode() if payload is not None else None
        if body is not None and len(body) > 12*1024*1024: raise ValueError('SITE_PAGE_TOO_LARGE')
        compressed = gzip.compress(body) if body is not None else None
        if compressed is not None and len(compressed) > 4*1024*1024: raise ValueError('SITE_PAGE_TOO_LARGE')
        headers = {'OAI-Sites-Authorization': 'Bearer ' + self.token}
        if body is not None: headers.update({'Content-Type': 'application/json', 'Content-Encoding': 'gzip'})
        req = urllib.request.Request(self.origin + path, data=compressed, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read(65537)
        except urllib.error.HTTPError as exc:
            code='SITE_HTTP_ERROR'
            try:
                body=exc.read(65537)
                value=json.loads(body) if len(body)<=65536 else {}
                if isinstance(value,dict) and isinstance(value.get('error'),str):code=value['error']
            except (OSError,ValueError):
                pass
            raise SiteHTTPError(exc.code,code) from None
        if len(raw) > 65536: raise ValueError('SITE_RESPONSE_TOO_LARGE')
        value = json.loads(raw)
        if body is not None and (value.get('ok') is not True or value.get('request_digest') != hashlib.sha256(body).hexdigest()):
            raise ValueError('SITE_RESPONSE_MISMATCH')
        return value

    def receipt(self, payload):
        value = self.request('/api/server-sync?id=' + page_id(payload)).get('receipt')
        return validate_receipt(value, payload) if value is not None else None

    def upload(self, payload):
        result = self.request('/api/server-sync', payload)
        validate_receipt(result.get('receipt'), payload)
        # Separate read-only request confirms the committed page after POST.
        receipt = self.receipt(payload)
        if receipt is None: raise ValueError('SITE_READBACK_MISSING')
        return receipt

    def upload_status(self, payload):
        expected = hashed(payload)
        try:
            self.request('/api/server-status', payload)
        except (ValueError, OSError):
            # Resolve a lost response by reading, never by blindly reposting.
            result = self.request('/api/server-status').get('snapshot')
            if not result or result.get('digest') != expected:
                raise ValueError('STATUS_WRITE_UNCONFIRMED') from None
            return result
        result = self.request('/api/server-status').get('snapshot')
        if not result or result.get('digest') != expected:
            raise ValueError('STATUS_READBACK_MISMATCH')
        return result


def run(config, client=None):
    start = time.monotonic(); state = Path(config['state_directory']); state.mkdir(parents=True, exist_ok=True)
    client = client or SiteClient(config['server_url'], load_token(config))
    report = dict(version=VERSION, source_api_calls=0, attempted_pages=0, confirmed_pages=0,
                  readback_pages=0, source_rows=0, matched_rows=0, errors=[], state='RUNNING')
    with LocalDbProcessLock(state / 'sync'), closing(sqlite3.connect(state / 'gpt-sync.sqlite3')) as db:
        db.execute('CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,revision TEXT,source_rows INTEGER,matched_rows INTEGER,confirmed_at TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS sync_page_failures(id TEXT PRIMARY KEY,revision TEXT NOT NULL,failures INTEGER NOT NULL,next_retry_at TEXT NOT NULL,reason TEXT NOT NULL)')
        try:
            snapshot = collect_status(Path(config.get('source_root', 'D:/G2B')))
            confirmed_status = client.upload_status(snapshot)
            report['collector_status'] = {'state':'VERIFIED', 'digest':confirmed_status['digest']}
        except (ValueError, OSError, sqlite3.Error) as exc:
            # Status reporting and page transfer have independent error paths.
            report['collector_status'] = {'state':'UNCONFIRMED','reason':type(exc).__name__}
        items, report['errors'] = inventory(source_paths(Path(config.get('source_root', 'D:/G2B'))))
        queues = {d: [] for d in DATASETS}
        pending=set();deferred={};clock=utc_now()
        for item in sorted(items, key=lambda i: (i['checkpoint']['scope_key'], -i['page']['page_no']), reverse=True):
            ident = hashed([item['checkpoint']['dataset'], item['checkpoint']['scope_key'].split(':')[0] if item['checkpoint']['dataset']=='shopping_delivery' else item['checkpoint']['scope_key'], item['generation'], item['page']['page_no']])
            row = db.execute('SELECT revision FROM receipts WHERE id=?', (ident,)).fetchone()
            if row and row[0] == item['revision']: continue
            pending.add(ident)
            failure=db.execute('SELECT next_retry_at FROM sync_page_failures WHERE id=? AND revision=?',(ident,item['revision'])).fetchone()
            if failure and datetime.fromisoformat(failure[0])>clock:
                deferred[ident]=failure[0]
                continue
            queues[item['checkpoint']['dataset']].append((ident,item))
        while report['attempted_pages'] < 8 and time.monotonic()-start < 60 and any(queues.values()):
            for dataset in DATASETS:
                if report['attempted_pages'] >= 8 or time.monotonic()-start >= 60: break
                if not queues[dataset]: continue
                ident,item = queues[dataset].pop(0); report['attempted_pages'] += 1
                try:
                    payload = export_server_page(item)
                    # Resolve a previous lost response before any new mutation.
                    receipt = client.receipt(payload)
                    if receipt is None: receipt = client.upload(payload)
                    with db:
                        db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?)',
                            (page_id(payload), payload['revision'], receipt['source_rows'], receipt['matched_rows'], utc_now().isoformat()))
                        db.execute('DELETE FROM sync_page_failures WHERE id=?',(ident,))
                    pending.discard(ident);deferred.pop(ident,None)
                    report['confirmed_pages'] += 1; report['readback_pages'] += 1
                    report['source_rows'] += receipt['source_rows']; report['matched_rows'] += receipt['matched_rows']
                except (ValueError, sqlite3.Error, OSError) as exc:
                    # A rejected page cannot starve the entire delivery backlog.
                    # Network/access failures still stop this dataset for this run;
                    # a lost response is resolved by GET before a future POST.
                    reason=getattr(exc,'safe_code',type(exc).__name__)
                    error={'dataset':dataset,'scope':item['checkpoint']['scope_key'],'page_no':item['page']['page_no'],'reason':reason}
                    status=getattr(exc,'http_status',None)
                    if status is not None:error['http_status']=status
                    if status==422:
                        deferred[ident]=defer_rejected_page(db,ident,item['revision'],utc_now(),reason)
                        error['next_retry_at_utc']=deferred[ident]
                    else:
                        queues[dataset].clear()
                    report['errors'].append(error)
        report['pending_pages'] = len(pending)
        report['deferred_pages'] = len(deferred)
        report['next_page_retry_at_utc'] = min(deferred.values()) if deferred else None
    report.update(state='VERIFIED' if not report['errors'] and not report['deferred_pages'] and report['collector_status']['state']=='VERIFIED' else 'CONTINUING_WITH_SYNC_ERROR',
                  elapsed_seconds=round(time.monotonic()-start,3), finished_at_utc=datetime.now(timezone.utc).isoformat())
    tmp=state/'status.tmp';tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(state/'status.json')
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG);parser.add_argument('--controlled',action='store_true');args=parser.parse_args()
    try:
        if args.controlled and sys.stdin.buffer.readline()!=b'GO\n':raise ValueError('PARENT_HANDSHAKE_REQUIRED')
        print(json.dumps(run(load_config(args.config))))
    except Exception as exc:
        print(json.dumps({'state':'SYNC_NOT_STARTED','reason':type(exc).__name__,'source_api_calls':0}));raise SystemExit(1) from None
