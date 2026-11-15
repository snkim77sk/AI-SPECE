"""Newest-first daily plan. Existing completed data is read-only and never re-fetched."""
import json
import hashlib
import sqlite3
from contextlib import closing
from datetime import date,timedelta
from pathlib import Path

START='2026-01-01'


def enumerate_days(start,end):
    first,last=date.fromisoformat(start),date.fromisoformat(end)
    if start!=START or last<first:
        raise ValueError('INVALID_BACKFILL_RANGE')
    return [(last-timedelta(days=n)).isoformat() for n in range((last-first).days+1)]


def validated_snapshot(conn,path,loader):
    """Reuse receipt validation only while the entire DB content hash is unchanged."""
    if conn is None:
        return loader()
    path=Path(path)
    def digest():
        with path.open('rb') as stream:
            return hashlib.file_digest(stream,'sha256').hexdigest()
    before=digest()
    conn.execute('CREATE TABLE IF NOT EXISTS backfill_validated_files(path TEXT PRIMARY KEY,sha256 TEXT NOT NULL,metadata_json TEXT NOT NULL)')
    row=conn.execute('SELECT sha256,metadata_json FROM backfill_validated_files WHERE path=?',(str(path),)).fetchone()
    if row and row[0]==before:
        return json.loads(row[1])
    metadata=loader()
    if digest()!=before:
        raise ValueError('DB_CHANGED_DURING_READ_ONLY_VALIDATION')
    with conn:
        conn.execute('INSERT INTO backfill_validated_files VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,metadata_json=excluded.metadata_json',
                     (str(path),before,json.dumps(metadata)))
    return metadata


def inventory(*,start,end,held_day,held_page,held_saved,original_db,live_root,cache_conn=None,resume_held=False,reuse_completed=False):
    from receipt_validation import verify_receipt,readback
    days=enumerate_days(start,end)
    def original_receipts():
        existing={}
        with closing(sqlite3.connect(Path(original_db).as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            rows=conn.execute("SELECT * FROM collection_checkpoints WHERE dataset='shopping_delivery'").fetchall()
            for row in rows:
                cp=dict(row)
                day=cp['range_start']
                if cp['range_end']!=day:
                    continue
                if cp['status']=='COMPLETE' and verify_receipt(conn,cp,complete=True):
                    existing[day]={'state':'COMPLETE_EXISTING','db_path':str(original_db),'next_page':cp['page_no'],
                                   'saved':cp['saved_count'],'reason':'READ_ONLY_RECEIPT_VERIFIED'}
                else:
                    existing[day]={'state':'HOLD','db_path':str(original_db),'next_page':cp['page_no'],
                                   'saved':cp['saved_count'],'reason':'ORIGINAL_SCOPE_REQUIRES_REVIEW'}
        return existing
    existing=validated_snapshot(cache_conn,original_db,original_receipts)
    # A completed daily job never issues another request. Reuse that durable
    # result instead of rereading every historical DB in each 90-second cycle.
    # Active/retry scopes still go through the content hash and full receipt
    # checks, and every page sent to the website is independently validated.
    completed={}
    if reuse_completed and cache_conn is not None and cache_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backfill_days'").fetchone():
        completed={r['day']:dict(r) for r in cache_conn.execute("SELECT * FROM backfill_days WHERE state='COMPLETE'")}
    jobs=[]
    for day in days:
        path=Path(live_root)/f'g2b-real-{day}.sqlite3'
        job={'day':day,'state':'READY','db_path':str(path),'next_page':1,'saved':0,'reason':''}
        if day==held_day and not resume_held:
            job.update(state='HOLD',next_page=held_page,saved=held_saved,reason='ORIGINAL_UNCERTAIN_PAGE_HOLD')
        elif day in existing and not (day==held_day and resume_held):
            job.update(existing[day])
        elif (day in completed and completed[day]['db_path']==str(path) and path.is_file()):
            job.update(completed[day])
        elif path.exists():
            from process_lock import LocalDbProcessLock
            from restart_recovery import recover_local_journal
            try:
                with LocalDbProcessLock(path):
                    recover_local_journal(path)
                    receipt=validated_snapshot(cache_conn,path,lambda:readback(path,day))
            except (ValueError,sqlite3.Error,OSError,RuntimeError) as exc:
                # A damaged/locked daily database must not hide other valid days.
                job.update(state='HOLD',reason='DAILY_DB_'+type(exc).__name__.upper())
                jobs.append(job)
                continue
            if receipt:
                job.update(next_page=receipt['next_page'],saved=receipt['saved'])
                job['state']='COMPLETE' if receipt['complete'] else ('READY' if receipt['status']=='RUNNING' else 'HOLD')
                if job['state']=='HOLD':
                    job['reason']='CHECKPOINT_REQUIRES_REVIEW'
        jobs.append(job)
    return jobs


def save_inventory(conn,jobs):
    conn.execute('''CREATE TABLE IF NOT EXISTS backfill_days(day TEXT PRIMARY KEY,state TEXT NOT NULL,
        db_path TEXT NOT NULL,next_page INTEGER NOT NULL,saved INTEGER NOT NULL,reason TEXT NOT NULL)''')
    with conn:
        for job in jobs:
            conn.execute('''INSERT INTO backfill_days VALUES(:day,:state,:db_path,:next_page,:saved,:reason)
                ON CONFLICT(day) DO UPDATE SET state=excluded.state,db_path=excluded.db_path,
                next_page=excluded.next_page,saved=excluded.saved,reason=excluded.reason''',job)


def next_job(conn,start,end):
    row=conn.execute("SELECT * FROM backfill_days WHERE state='READY' AND day BETWEEN ? AND ? ORDER BY day DESC LIMIT 1",
                     (start,end)).fetchone()
    return dict(row) if row else None


def isolate_unresolved_days(conn):
    from restart_recovery import unresolved_days
    days=unresolved_days(conn)
    with conn:
        conn.executemany("UPDATE backfill_days SET state='HOLD',reason='UNCERTAIN_SCOPE_NO_AUTOMATIC_RETRY' WHERE day=?",[(day,) for day in days])
    return days


def record_verified(conn,day,receipt):
    with conn:
        conn.execute("UPDATE backfill_days SET state=?,next_page=?,saved=?,reason='' WHERE day=?",
            ('COMPLETE' if receipt['complete'] else 'READY',receipt['next_page'],receipt['saved'],day))


def summary(conn,start,end):
    rows=list(conn.execute('SELECT state,COUNT(*) FROM backfill_days WHERE day BETWEEN ? AND ? GROUP BY state',(start,end)))
    counts={str(r[0]):r[1] for r in rows}
    next_value=next_job(conn,start,end)
    return {'range_start':start,'range_end':end,'total_days':sum(counts.values()),'days_by_state':counts,
            'next_day':next_value['day'] if next_value else None,
            'next_page':next_value['next_page'] if next_value else None,
            'range_complete':bool(counts) and not counts.get('READY') and not counts.get('HOLD')}
