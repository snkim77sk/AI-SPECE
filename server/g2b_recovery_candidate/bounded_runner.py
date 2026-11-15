"""Offline-only recovery candidate; reuses the existing guarded HTTP and RAW collector.

No live entrypoint, credential loader, schedule installer, snapshot or upload code.
All writes are restricted to test-data below this candidate directory.
"""
from __future__ import annotations
import gc
import json
import os
import sqlite3
import threading
import time
import tracemalloc
from contextlib import closing, ExitStack
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
TEST_ROOT = ROOT/'test-data'
KST = timezone(timedelta(hours=9))
_RUN_LOCK = threading.Lock()


class Stop(RuntimeError):
    pass


def isolated_path(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(TEST_ROOT.resolve()) or resolved == TEST_ROOT.resolve():
        raise Stop('ISOLATED_TEST_PATH_REQUIRED')
    return resolved


def kst_day(now):
    if now.tzinfo is None:
        raise Stop('AWARE_TIME_REQUIRED')
    return now.astimezone(KST).date().isoformat()


class Ledger:
    def __init__(self, path):
        self.path = isolated_path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn = sqlite3.connect(self.path,timeout=1)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY,baseline_used INTEGER NOT NULL,
          official_remaining INTEGER NOT NULL,daily_limit INTEGER NOT NULL,page_limit INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(day TEXT PRIMARY KEY,page INTEGER NOT NULL,state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,stop_reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS binding(id INTEGER PRIMARY KEY CHECK(id=1),db_path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,source_day TEXT NOT NULL,
          page INTEGER NOT NULL,budget_day TEXT NOT NULL,state TEXT NOT NULL,
          attempted INTEGER NOT NULL DEFAULT 0,response_received INTEGER NOT NULL DEFAULT 0,
          response_confirmed INTEGER NOT NULL DEFAULT 0,stored INTEGER NOT NULL DEFAULT 0,
          verified INTEGER NOT NULL DEFAULT 0,rows INTEGER NOT NULL DEFAULT 0,
          UNIQUE(source_day,page));
        ''')

    def close(self):
        self.conn.close()

    def fixture_budget(self, day, *, used=0, remaining=950, daily_limit=950, page_limit=8):
        """Synthetic evidence only. No production reconciliation/unhold API exists."""
        date.fromisoformat(day)
        if any(type(n) is not int or n<0 for n in (used,remaining,daily_limit,page_limit)) or not daily_limit or not page_limit:
            raise Stop('INVALID_BUDGET')
        with self.conn:
            self.conn.execute('INSERT INTO usage VALUES(?,?,?,?,?)',
                              (day,used,remaining,min(950,daily_limit),min(8,page_limit)))

    def bind(self, db_path):
        with self.conn:
            self.conn.execute('INSERT OR IGNORE INTO binding VALUES(1,?)',(str(db_path),))
        if self.conn.execute('SELECT db_path FROM binding WHERE id=1').fetchone()[0] != str(db_path):
            raise Stop('LEDGER_DB_BINDING_MISMATCH')

    def enqueue(self, days, now):
        today=date.fromisoformat(kst_day(now))
        with self.conn:
            for value in sorted(set(days),reverse=True):
                day=date.fromisoformat(value)
                if day>=today or day<date(2026,9,1):
                    raise Stop('SOURCE_DAY_OUTSIDE_EXISTING_GUARD')
                self.conn.execute("INSERT OR IGNORE INTO jobs VALUES(?,1,'READY')",(day.isoformat(),))

    def usage(self, now):
        day=kst_day(now)
        base=self.conn.execute('SELECT * FROM usage WHERE day=?',(day,)).fetchone()
        count=self.conn.execute('SELECT COUNT(*) FROM calls WHERE budget_day=?',(day,)).fetchone()[0]
        if base is None:
            return {'kst_day':day,'reserved_attempts':count,'reconciled':False,'used':None,'remaining':None,'effective_remaining':0}
        remaining=max(0,min(base['daily_limit']-base['baseline_used']-count,base['official_remaining']-count))
        return {'kst_day':day,'reserved_attempts':count,'reconciled':True,
                'used':base['baseline_used']+count,'remaining':remaining,'effective_remaining':remaining,
                'daily_limit':base['daily_limit'],'page_limit':base['page_limit'], 'evidence':'OFFLINE_FIXTURE'}

    def hold_check(self, hold):
        if Path(hold).exists():
            raise Stop('EXTERNAL_HOLD')
        if self.conn.execute("SELECT 1 FROM calls WHERE state!='VERIFIED' LIMIT 1").fetchone():
            raise Stop('UNCERTAIN_OR_PENDING_NO_RETRY')
        if self.conn.execute("SELECT 1 FROM jobs WHERE state='HOLD' LIMIT 1").fetchone():
            raise Stop('INTERNAL_HOLD')

    def begin_run(self, run_id):
        try:
            with self.conn:
                self.conn.execute("INSERT INTO runs VALUES(?,'RUNNING')",(run_id,))
        except sqlite3.IntegrityError:
            raise Stop('RUN_ALREADY_INVOKED') from None

    def reserve(self, run_id, now, hold, page_cap):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            self.hold_check(hold)
            usage=self.usage(now)
            if not usage['reconciled']:
                raise Stop('UNRECONCILED_KST_USAGE')
            if usage['effective_remaining']<=0:
                raise Stop('DAILY_QUOTA')
            n=self.conn.execute('SELECT COUNT(*) FROM calls WHERE run_id=?',(run_id,)).fetchone()[0]
            if n>=min(page_cap,usage['page_limit'],8):
                raise Stop('PAGE_LIMIT')
            job=self.conn.execute("SELECT day,page FROM jobs WHERE state='READY' ORDER BY day DESC LIMIT 1").fetchone()
            if job is None:
                raise Stop('NO_WORK')
            self.hold_check(hold)
            cur=self.conn.execute("INSERT INTO calls(run_id,source_day,page,budget_day,state) VALUES(?,?,?,?,'RESERVED')",
                                  (run_id,job['day'],job['page'],usage['kst_day']))
            self.conn.execute("UPDATE jobs SET state='RUNNING' WHERE day=?",(job['day'],))
            self.conn.commit()
            return cur.lastrowid,job['day'],job['page']
        except BaseException:
            self.conn.rollback()
            raise

    def phase(self, call_id, phase):
        if phase not in ('attempted','response_received','response_confirmed','stored'):
            raise Stop('INVALID_PHASE')
        with self.conn:
            self.conn.execute(f'UPDATE calls SET {phase}=1 WHERE id=?',(call_id,))

    def verified(self, call_id, receipt):
        with self.conn:
            row=self.conn.execute('SELECT * FROM calls WHERE id=?',(call_id,)).fetchone()
            if (not row or not all(row[k] for k in ('attempted','response_received','response_confirmed','stored'))
                    or receipt['next_page']!=row['page']+1 or receipt['verified'] is not True):
                raise Stop('INCOMPLETE_CONFIRMATION')
            self.conn.execute("UPDATE calls SET state='VERIFIED',verified=1,rows=? WHERE id=?",(receipt['page_rows'],call_id))
            self.conn.execute('UPDATE jobs SET page=?,state=? WHERE day=?',
                              (receipt['next_page'],'COMPLETE' if receipt['complete'] else 'READY',row['source_day']))

    def uncertain(self, call_id):
        with self.conn:
            self.conn.execute("UPDATE calls SET state='UNCERTAIN' WHERE id=? AND state!='VERIFIED'",(call_id,))
            self.conn.execute("UPDATE jobs SET state='HOLD' WHERE day=(SELECT source_day FROM calls WHERE id=?)",(call_id,))

    def summary(self, run_id, now):
        row=self.conn.execute('''SELECT COUNT(*) reserved,COALESCE(SUM(attempted),0) attempted,
          COALESCE(SUM(response_received),0) response_received,COALESCE(SUM(response_confirmed),0) response_confirmed,
          COALESCE(SUM(stored),0) stored_pages,COALESCE(SUM(verified),0) verified_pages,COALESCE(SUM(rows),0) verified_rows
          FROM calls WHERE run_id=?''',(run_id,)).fetchone()
        return dict(row)|{'usage':self.usage(now)}


def run_once(*, db_path, ledger_path, days, hold_path, run_id, fixture=None, now=None,
             monotonic=time.monotonic, max_pages=8, deadline=None, memory_limit_mb=256, after_store=None):
    """One sequential offline cycle. Unknown response/save status is never retried."""
    if fixture is None or getattr(fixture,'offline_only',False) is not True:
        raise Stop('LIVE_EXECUTION_DISABLED')
    db_path,ledger_path=isolated_path(db_path),isolated_path(ledger_path)
    if db_path==ledger_path:
        raise Stop('LEDGER_MUST_BE_SEPARATE')
    if type(max_pages) is not int or not 1<=max_pages<=8 or not 32<=memory_limit_mb<=256:
        raise Stop('INVALID_RUN_LIMIT')
    now=now or (lambda:datetime.now(KST))
    started=monotonic()
    deadline=min(deadline if deadline is not None else started+80,started+80)
    ledger=None
    call_id=None
    registered=False
    samples=[]
    reason='NO_WORK'
    import db
    import shopping_vnext as shop
    import vnext_http
    from vnext_source_guard import operational_recent_source_context
    from vnext_store import ensure_foundation
    from process_lock import LocalDbProcessLock
    from receipt_validation import readback
    from windows_limits import process_memory
    if not _RUN_LOCK.acquire(blocking=False):
        return {'mode':'OFFLINE_FIXTURE','stop_reason':'DUPLICATE_PROCESS','attempted':0}
    try:
        with ExitStack() as stack:
            for path in (ledger_path,db_path):
                lock=LocalDbProcessLock(path)
                if not lock.acquire():
                    raise Stop('DUPLICATE_PROCESS')
                stack.callback(lock.release)
            ledger=Ledger(ledger_path)
            stack.callback(ledger.close)
            ledger.bind(db_path)
            ledger.begin_run(run_id)
            registered=True
            ledger.hold_check(hold_path)
            if not ledger.usage(now())['reconciled']:
                raise Stop('UNRECONCILED_KST_USAGE')
            ledger.enqueue(days,now())
            stack.enter_context(patch.object(db,'DB_PATH',str(db_path)))
            stack.enter_context(patch.dict(os.environ,{'G2B_RUNTIME_ROLE':'LOCAL_COLLECTOR','G2B_AUTO_SYNC':'0',
                'G2B_SERVICE_KEY':'OFFLINE_FIXTURE_NOT_A_CREDENTIAL','G2B_SQLITE_TIMEOUT':'1',
                'G2B_VNEXT_API_DAILY_LIMIT':str(ledger.usage(now())['daily_limit'])}))
            stack.enter_context(fixture.installed())
            db.init_db()
            ensure_foundation()
            # Resume only validated receipts; never use the original silent replay fallback.
            for job in ledger.conn.execute("SELECT * FROM jobs WHERE state='READY'").fetchall():
                receipt=readback(db_path,job['day'])
                if receipt:
                    if receipt['status'] not in ('RUNNING','COMPLETE'):
                        raise Stop('CHECKPOINT_REQUIRES_REVIEW')
                    # A populated DB may be imported only when it has no unresolved ledger call.
                    if job['page'] not in (1,receipt['next_page']):
                        raise Stop('LEDGER_CHECKPOINT_MISMATCH')
                    with ledger.conn:
                        ledger.conn.execute('UPDATE jobs SET page=?,state=? WHERE day=?',
                            (receipt['next_page'],'COMPLETE' if receipt['complete'] else 'READY',job['day']))
                elif job['page']!=1:
                    raise Stop('MISSING_CHECKPOINT')
            for _ in range(max_pages):
                ledger.hold_check(hold_path)
                if deadline-monotonic()<=30:
                    raise Stop('DEADLINE_MARGIN')
                if process_memory()['private_bytes']>memory_limit_mb*1024*1024:
                    raise Stop('MEMORY_LIMIT')
                call_id,day,page=ledger.reserve(run_id,now(),hold_path,max_pages)
                def request_once(url,kind):
                    if Path(hold_path).exists():
                        raise Stop('EXTERNAL_HOLD')
                    left=deadline-monotonic()
                    if left<=30:
                        raise Stop('DEADLINE_MARGIN')
                    return vnext_http.request(url,kind,timeout=min(10,left-20),retries=1,
                        observer=lambda phase:ledger.phase(call_id,phase),strict_response_limit=True)
                with patch.object(shop,'_request',request_once):
                    with operational_recent_source_context(collection_date=day,max_requests=1):
                        result=shop.collect_all(day,day,page_size=fixture.page_size,max_pages=1,resume=True)
                if result['status'] not in ('RUNNING','COMPLETE') or result.get('reason'):
                    raise Stop('SOURCE_PAGE_REJECTED')
                ledger.phase(call_id,'stored')
                if after_store:
                    after_store(db_path,day,page)
                receipt=readback(db_path,day,expected_page=page)
                ledger.verified(call_id,receipt)
                call_id=None
                del result,receipt
                gc.collect()
                sample=process_memory()|{'day':day,'page':page}
                if tracemalloc.is_tracing():
                    sample['python_retained_bytes']=tracemalloc.get_traced_memory()[0]
                samples.append(sample)
                if sample['private_bytes']>memory_limit_mb*1024*1024:
                    raise Stop('MEMORY_LIMIT')
            reason='PAGE_LIMIT' if ledger.conn.execute("SELECT 1 FROM jobs WHERE state='READY' LIMIT 1").fetchone() else 'NO_WORK'
    except Exception as exc:
        # Exception messages, URLs and credential-bearing upstream text are not logged.
        reason=str(exc) if type(exc) is Stop else type(exc).__name__
    finally:
        # The ExitStack has closed handles; reopen only the isolated ledger for final diagnostics.
        try:
            if ledger_path.exists():
                final=Ledger(ledger_path)
                try:
                    if call_id is not None:
                        final.uncertain(call_id)
                    if registered:
                        with final.conn:
                            final.conn.execute('UPDATE runs SET stop_reason=? WHERE id=?',(reason,run_id))
                    summary=final.summary(run_id,now()) if registered else {'attempted':0}
                finally:
                    final.close()
            else:
                summary={'attempted':0}
        finally:
            _RUN_LOCK.release()
    return summary|{'mode':'OFFLINE_FIXTURE','stop_reason':reason,'elapsed_seconds':round(monotonic()-started,3),
                    'memory_samples':samples,'live_calls':0,'production_writes':0}
