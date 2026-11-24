"""Read-only, page-bounded receipt and RAW verification for both budget layers."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing


def scope_for(service,snapshot):
    if service=='QWGJK':
        return 'budget','2026:'+snapshot
    if service=='AIDFA' and snapshot=='2026':
        return 'budget_appropriation','2026:ALL'
    raise ValueError('INVALID_LOFIN_SCOPE')


PROOF_FILE = 'verified-budget-prefix.json'


def _file_identity(path):
    # A prefix may be reused only for the same clean DB, under the collector lock.
    if any(path.with_name(path.name+suffix).exists() for suffix in ('-wal','-journal')):
        return None
    stat=path.stat()
    return [str(path.resolve()),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns]


def _read_prefix(path,identity,cp):
    if identity is None:
        return False
    try:
        value=json.loads((path.parent/PROOF_FILE).read_text(encoding='utf-8'))
        return value=={'version':1,'file':identity,'checkpoint':cp}
    except (OSError,ValueError):
        return False


def _write_prefix(path,identity,cp):
    if identity is None:
        return
    target=path.parent/PROOF_FILE
    temp=target.with_name(target.name+'.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump({'version':1,'file':identity,'checkpoint':cp},stream,ensure_ascii=False,sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(target)


def _tail(conn,cp,key):
    page_no=cp['page_no']-1
    if page_no==0:
        return None
    page=conn.execute('''SELECT * FROM vnext_collection_pages
        WHERE dataset=? AND scope_key=? AND generation=? AND page_no=?''',(*key,page_no)).fetchone()
    if page is None or page['page_size']!=cp['page_size'] or page['source_total']!=cp['source_total']:
        raise ValueError('LOFIN_PAGE_RECEIPT_MISSING')
    pairs=[]
    for item in conn.execute('''SELECT i.source_key,i.payload_sha256,r.payload_json,
        EXISTS(SELECT 1 FROM raw_record_revisions h WHERE h.dataset=i.dataset AND h.source_key=i.source_key AND h.payload_sha256=i.payload_sha256)
        FROM vnext_collection_items i LEFT JOIN raw_records r
          ON r.dataset=i.dataset AND r.source_key=i.source_key AND r.payload_sha256=i.payload_sha256
        WHERE i.dataset=? AND i.scope_key=? AND i.generation=? AND i.page_no=?
        ORDER BY i.source_key,i.payload_sha256 LIMIT ?''',(*key,page_no,cp['page_size']+1)):
        if not item[3] or item[2] is None or hashlib.sha256(item[2].encode('utf-8')).hexdigest()!=item[1]:
            raise ValueError('LOFIN_RAW_HASH_MISMATCH')
        pairs.append((item[0],item[1]))
    if len(pairs)!=page['item_count'] or len(pairs)>cp['page_size'] or hashlib.sha256(json.dumps(pairs).encode()).hexdigest()!=page['response_hash']:
        raise ValueError('LOFIN_PAGE_RECEIPT_INVALID')
    return page


def _appends_prefix(conn,cp,meta,key,page,prior):
    # The caller holds the collector lock. collect_pages checked this exact
    # prefix before its transaction and forbade replacing previously stored RAW.
    if not page or not prior or cp['status'] not in ('RUNNING','COMPLETE'):
        return False
    old=prior['_checkpoint']
    old_meta=json.loads(old['cursor_value'])
    if (cp['dataset'],cp['scope_key'])!=(old['dataset'],old['scope_key']) or cp['page_size']!=old['page_size']:
        return False
    if {k:v for k,v in meta.items() if k!='completion_reason'}!={k:v for k,v in old_meta.items() if k!='completion_reason'}:
        return False
    if cp['page_no']!=old['page_no']+1 or cp['saved_count']!=old['saved_count']+page['item_count'] or cp['fetched_count']!=cp['saved_count']:
        return False
    total=cp['source_total']
    if old['source_total']>0 and old['source_total']!=total:
        return False
    if total>0:
        if cp['saved_count']>total or not page['item_count'] and cp['saved_count']<total:
            return False
        reason='TOTAL_REACHED' if cp['saved_count']==total else ''
    elif not page['item_count']:
        reason='EMPTY_PAGE'
    elif page['item_count']<cp['page_size'] and conn.execute('''SELECT 1 FROM vnext_collection_pages
        WHERE dataset=? AND scope_key=? AND generation=? AND page_no<? AND item_count=page_size LIMIT 1''',(*key,page['page_no'])).fetchone():
        reason='SHORT_PAGE_UNKNOWN_TOTAL'
    else:
        reason=''
    return reason==page['terminal_reason']==meta.get('completion_reason','') and bool(reason)==(cp['status']=='COMPLETE')


def readback(path,service,snapshot,expected_page=None,*,reuse_prefix=False,prior=None,include_checkpoint=False,
             verified_tail=None,deadline=None):
    from receipt_validation import verify_receipt
    if not path.exists():
        return None
    dataset,scope=scope_for(service,snapshot)
    identity=_file_identity(path) if reuse_prefix else None
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        if deadline is not None:
            conn.set_progress_handler(lambda:int(time.monotonic()>=deadline),10000)
        row=conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',(dataset,scope)).fetchone()
        if row is None:
            return None
        cp=dict(row)
        complete=cp['status']=='COMPLETE'
        if expected_page is not None and cp['page_no']!=expected_page+1:
            raise ValueError('LOFIN_CHECKPOINT_DID_NOT_ADVANCE_ONCE')
        meta=json.loads(cp['cursor_value'])
        key=(dataset,scope,meta['generation'])
        page=_tail(conn,cp,key)
        result={'dataset':dataset,'scope':scope,'next_page':cp['page_no'],'page_rows':page['item_count'] if page else 0,
                'saved':cp['saved_count'],'source_total':cp['source_total'],'status':cp['status'],
                'complete':complete,'response_hash':page['response_hash'] if page else None,'verified':True}
        if verified_tail is not None:
            # The supervised worker has verified the entire prefix and the ledger
            # confirms this page. Reopen the DB and verify the same RAW tail.
            if not page or any(verified_tail.get(field)!=value for field,value in result.items()):
                raise ValueError('LOFIN_FINAL_READBACK_MISMATCH')
            return result
        cached=reuse_prefix and _read_prefix(path,identity,cp)
        added=prior is not None and _appends_prefix(conn,cp,meta,key,page,prior)
        if not cached and not added and not verify_receipt(conn,cp,complete=complete):
            raise ValueError('LOFIN_RECEIPT_INVALID')
    if reuse_prefix and identity is not None and cp['status'] in ('RUNNING','COMPLETE'):
        if _file_identity(path)!=identity:
            raise ValueError('LOFIN_DATABASE_CHANGED_DURING_READBACK')
        if not cached:
            _write_prefix(path,identity,cp)
    if include_checkpoint:
        result['_checkpoint']=cp
    return result
