"""Read-only, page-bounded receipt and RAW verification for both budget layers."""
import hashlib
import json
import sqlite3
from contextlib import closing


def scope_for(service,snapshot):
    if service=='QWGJK':
        return 'budget','2026:'+snapshot
    if service=='AIDFA' and snapshot=='2026':
        return 'budget_appropriation','2026:ALL'
    raise ValueError('INVALID_LOFIN_SCOPE')


def readback(path,service,snapshot,expected_page=None):
    from receipt_validation import verify_receipt
    if not path.exists():
        return None
    dataset,scope=scope_for(service,snapshot)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        row=conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',(dataset,scope)).fetchone()
        if row is None:
            return None
        cp=dict(row)
        complete=cp['status']=='COMPLETE'
        if not verify_receipt(conn,cp,complete=complete):
            raise ValueError('LOFIN_RECEIPT_INVALID')
        if expected_page is not None and cp['page_no']!=expected_page+1:
            raise ValueError('LOFIN_CHECKPOINT_DID_NOT_ADVANCE_ONCE')
        generation=json.loads(cp['cursor_value'])['generation']
        page=cp['page_no']-1
        count=0
        for item in conn.execute('''SELECT r.payload_json,i.payload_sha256 FROM vnext_collection_items i
            JOIN raw_records r ON r.dataset=i.dataset AND r.source_key=i.source_key
            WHERE i.dataset=? AND i.scope_key=? AND i.generation=? AND i.page_no=?''',(dataset,scope,generation,page)):
            if hashlib.sha256(item[0].encode('utf-8')).hexdigest()!=item[1]:
                raise ValueError('LOFIN_RAW_HASH_MISMATCH')
            count+=1
        return {'dataset':dataset,'scope':scope,'next_page':cp['page_no'],'page_rows':count,
                'saved':cp['saved_count'],'source_total':cp['source_total'],'status':cp['status'],
                'complete':complete,'verified':True}
