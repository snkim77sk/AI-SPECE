"""Bounded late-arrival checks for verified empty shopping-delivery dates."""
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

DELAYS_HOURS = (6, 24, 72)
DAILY_CAP = 7
FOLDER = 'recent-empty-rechecks'


def read_calls(root, *, budget_day=None, source_day=None):
    pattern=(source_day or '*')+'--'+(budget_day or '*')+'--*.json'
    results=[]
    for path in (root/FOLDER).glob(pattern):
        value=json.loads(path.read_text(encoding='utf-8'))
        if (value.get('version')!=1 or path.name!=record_name(value)
                or value['outcome'] not in ('RESERVED','ATTEMPTED','VERIFIED','HELD')):
            raise ValueError('INVALID_RECHECK_JOURNAL')
        results.append(value)
    return results


def record_name(record):
    return f"{record['source_day']}--{record['budget_day']}--{record['ordinal']:02d}-{record['page']:05d}.json"


def count_usage(root, budget_day):
    return len(read_calls(root,budget_day=budget_day))


def write_record(root,record):
    folder=root/FOLDER
    folder.mkdir(exist_ok=True)
    target=folder/record_name(record)
    temp=target.with_name(target.name+'.tmp')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(record,stream,ensure_ascii=False,sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(target)


def read_receipt(path,scope):
    from receipt_validation import verify_receipt
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        deadline=time.monotonic()+5
        conn.set_progress_handler(lambda: int(time.monotonic()>deadline),10000)
        conn.execute('BEGIN')
        row=conn.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',
                         ('shopping_delivery',scope)).fetchone()
        if row is None:
            return None
        cp=dict(row)
        if cp['status'] not in ('RUNNING','COMPLETE') or not verify_receipt(conn,cp,complete=cp['status']=='COMPLETE'):
            raise ValueError('DELIVERY_RECHECK_RECEIPT_UNVERIFIED')
        metadata=json.loads(cp['cursor_value'])
        page=conn.execute('''SELECT item_count FROM vnext_collection_pages
            WHERE dataset=? AND scope_key=? AND generation=? AND page_no=?''',
            ('shopping_delivery',scope,metadata['generation'],cp['page_no']-1)).fetchone()
        return {'next_page':cp['page_no'],'saved':cp['saved_count'],'page_rows':page[0] if page else 0,
                'complete':cp['status']=='COMPLETE','generation':metadata['generation'],
                'terminal':metadata.get('completion_reason',''),'updated_at':cp['updated_at']}


def pick(conn,root,now):
    budget_day=now.date().isoformat()
    since=(now.date()-timedelta(days=7)).isoformat()
    recent=conn.execute("""SELECT d.day FROM backfill_days d
        WHERE d.state='COMPLETE' AND d.saved=0 AND d.next_page=2 AND d.day>=? AND d.day<?
         AND EXISTS (SELECT 1 FROM calls c WHERE c.source_day=d.day AND c.page=1 AND c.state='VERIFIED'
            AND c.attempted=1 AND c.response_received=1 AND c.response_confirmed=1 AND c.stored=1 AND c.verified=1 AND c.verified_rows=0)
         AND NOT EXISTS (SELECT 1 FROM calls c WHERE c.source_day=d.day
            AND c.state NOT IN ('VERIFIED','NOT_SENT','RECHECK_SUPERSEDED','RETRY_SUPERSEDED'))
        ORDER BY d.day DESC""",(since,budget_day)).fetchall()
    for (day,) in recent:
        path=root/f'g2b-real-{day}.sqlite3'
        if not path.is_file():
            continue
        try:
            original=read_receipt(path,f'{day}:{day}')
        except (ValueError,sqlite3.Error):
            continue
        if not original or not original['complete'] or original['saved']!=0 or original['terminal']!='EMPTY_PAGE':
            continue
        prior=max(read_calls(root,source_day=day),key=lambda r:(r['ordinal'],r['page']),default=None)
        if prior:
            scope=f"{day}:{day}:recheck-{prior['ordinal']}"
            try:
                receipt=read_receipt(path,scope)
            except (ValueError,sqlite3.Error):
                continue
            if prior['outcome']!='VERIFIED':
                # A committed receipt resolves a lost response. Never repeat an
                # uncertain page for which there is no committed receipt.
                if not receipt or receipt['next_page']!=prior['page']+1:
                    continue
                prior.update(outcome='VERIFIED',attempted=True,response_received=True,response_confirmed=True,receipt=receipt)
                write_record(root,prior)
            if not receipt or receipt!=prior.get('receipt'):
                continue
            if not receipt['complete']:
                return day,path,prior['ordinal'],receipt['next_page']
            if receipt['saved']>0:
                continue
        ordinal=prior['ordinal']+1 if prior else 1
        if ordinal>len(DELAYS_HOURS):
            continue
        observed=datetime.fromisoformat(original['updated_at']).replace(tzinfo=timezone.utc)
        due=observed+timedelta(hours=DELAYS_HOURS[ordinal-1])
        if prior:
            due=max(due,datetime.fromisoformat(prior['reserved_at'])+timedelta(hours=6))
        if now>=due:
            return day,path,ordinal,1
    return None


def run_one(conn,run_dir,plan,now,deadline,source_context,request):
    """Run under the scheduled supervisor's existing collection ledger lock."""
    root=run_dir.parent
    if plan['mode']!='WINDOWS_SCHEDULED_BACKFILL' or time.monotonic()>=deadline-30:
        return None
    clock=now()
    day_budget=plan['budget_day']
    if clock.date().isoformat()!=day_budget:
        return None
    late=read_calls(root,budget_day=day_budget)
    used=len(late)
    if any(item['run_id']==plan['run_id'] for item in late):
        return None
    ordinary=conn.execute("SELECT COUNT(*) FROM calls WHERE budget_day=? AND state!='NOT_SENT'",(day_budget,)).fetchone()[0]
    used_in_run=conn.execute("SELECT COUNT(*) FROM calls WHERE run_id=?",(plan['run_id'],)).fetchone()[0]
    if ordinary+used>=plan['effective_day_budget'] or used_in_run>=plan['max_pages']:
        return None
    selected=pick(conn,root,clock)
    if selected is None or time.monotonic()>=deadline-30:
        return None
    day,path,ordinal,page=selected
    if page==1 and sum(item['page']==1 for item in late)>=DAILY_CAP:
        return None
    scope=f'{day}:{day}:recheck-{ordinal}'
    from process_lock import LocalDbProcessLock
    import db
    import shopping_vnext as shop
    from vnext_collection import collect_pages

    record={'version':1,'source_day':day,'budget_day':day_budget,'ordinal':ordinal,'page':page,
            'run_id':plan['run_id'],'reserved_at':clock.isoformat(),'outcome':'RESERVED',
            'attempted':False,'response_received':False,'response_confirmed':False}
    with LocalDbProcessLock(path):
        before=read_receipt(path,scope)
        if (before['next_page'] if before else 1)!=page or before and before['complete']:
            raise ValueError('RECHECK_CHECKPOINT_CHANGED')
        write_record(root,record)
        def observe(event):
            if event in ('attempted','response_received','response_confirmed'):
                record[event]=True
                record['outcome']='ATTEMPTED'
                write_record(root,record)
        old_request=shop._request
        def request_once(url,kind):
            if record['attempted'] or time.monotonic()>=deadline-30 or now().date().isoformat()!=day_budget:
                raise ValueError('RECHECK_REQUEST_BOUNDARY')
            return request(url,kind,timeout=min(10,deadline-time.monotonic()-20),retries=1,
                           observer=observe,strict_response_limit=True)
        def lookup(dataset,key):
            with db.connect() as local:
                raw=local.execute('SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?',(dataset,key)).fetchone()
                return dict(raw) if raw else None
        def preserve(dataset,key,row,*,_conn,**fields):
            known=_conn.execute('SELECT payload_sha256 FROM raw_records WHERE dataset=? AND source_key=?',(dataset,key)).fetchone()
            if known:
                raise ValueError('RECHECK_RAW_ALREADY_PRESENT')
            return shop.preserve_raw(dataset,key,row,_conn=_conn,**fields)
        shop._request=request_once
        db.DB_PATH=str(path)
        os.environ['G2B_DB_PATH']=str(path)
        try:
            with source_context(collection_date=day,max_requests=1):
                result=collect_pages(dataset=shop.DATASET,scope=scope,range_start=day,range_end=day,page_size=999,max_pages=1,
                    resume=True,fetch=lambda n,size:shop.fetch_page(day,day,page=n,rows=size),
                    identity=shop._source_key,source_system=shop.SOURCE_SYSTEM,source_operation=shop.SHOP_OPERATION,
                    source_date=lambda row:shop._source_date(row,day),preserve=preserve,checkpoint=shop.save_checkpoint,
                    lookup=lookup,validate_row=shop._identity_problem)
            receipt=read_receipt(path,scope)
            if not receipt or receipt['next_page']!=page+1 or not record['response_confirmed'] or result.get('reason'):
                raise ValueError('RECHECK_STORE_UNCONFIRMED')
            record.update(outcome='VERIFIED',receipt=receipt)
        except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
            record.update(outcome='HELD',reason=type(exc).__name__)
        finally:
            shop._request=old_request
            write_record(root,record)
    return record
