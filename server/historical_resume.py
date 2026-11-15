"""Adopt verified trial receipts into a new live DB; preserve the trial unchanged."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from delayed_retry import AUTHORIZATION, enqueue


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def adopt(conn, root, source, hold_path, *, clock=None):
    """Caller owns ledger, historical source and target-day collector locks."""
    from receipt_validation import readback
    from process_lock import LocalDbProcessLock
    root,source,hold_path=Path(root),Path(source),Path(hold_path)
    hold=json.loads(hold_path.read_text(encoding='utf-8'))
    day=hold['source_day']
    if (day!='2026-11-12' or hold.get('next_page')!=14 or hold.get('verified_saved')!=12987
            or hold.get('api_calls_consumed')!=14):
        raise ValueError('HISTORICAL_RECEIPT_CONTRACT_CHANGED')
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS historical_resumes(
            source_day TEXT PRIMARY KEY, authorization TEXT NOT NULL,
            original_db_sha256 TEXT NOT NULL, original_hold_sha256 TEXT NOT NULL,
            receipt_json TEXT NOT NULL, imported_call_id INTEGER NOT NULL)''')
    existing=conn.execute('SELECT * FROM historical_resumes WHERE source_day=?',(day,)).fetchone()
    if existing:
        if existing['authorization']!=AUTHORIZATION or existing['original_hold_sha256']!=sha(hold_path):
            raise ValueError('HISTORICAL_AUTHORIZATION_CHANGED')
        return {'authorization':AUTHORIZATION,'source_day':day}
    target=root/f'g2b-real-{day}.sqlite3'
    if source.resolve()==target.resolve(): raise ValueError('HISTORICAL_SOURCE_MUST_STAY_READ_ONLY')
    with LocalDbProcessLock(target):
        before=sha(source)
        receipt=readback(source,day)
        if (not receipt or not receipt['verified'] or receipt['complete']
                or receipt['next_page']!=14 or receipt['saved']!=12987):
            raise ValueError('HISTORICAL_SOURCE_RECEIPT_INVALID')
        if not target.exists():
            temporary=root/(target.name+'.'+uuid.uuid4().hex+'.partial')
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
                original.execute('PRAGMA query_only=ON')
                with closing(sqlite3.connect(temporary)) as copied:
                    original.backup(copied)
            if readback(temporary,day)!=receipt or sha(source)!=before:
                raise ValueError('HISTORICAL_COPY_VERIFICATION_FAILED')
            temporary.rename(target)
        if readback(target,day)!=receipt or sha(source)!=before:
            raise ValueError('HISTORICAL_TARGET_CHANGED')
        # This is the documented original page-14 attempt, not a new source call.
        # Its historical quota day and unknown response flags remain explicit.
        with conn:
            run_id='imported-trial-timeout-20261113'
            conn.execute("INSERT OR IGNORE INTO runs VALUES(?,'2026-11-13','IMPORTED_HISTORICAL_ATTEMPT')",(run_id,))
            call=conn.execute("INSERT INTO calls(run_id,source_day,page,budget_day,state,attempted) VALUES(?,?,14,'2026-11-13','UNCERTAIN',1)",(run_id,day)).lastrowid
            conn.execute('INSERT INTO historical_resumes VALUES(?,?,?,?,?,?)',
                         (day,AUTHORIZATION,before,sha(hold_path),json.dumps(receipt,sort_keys=True),call))
        enqueue(conn,call,failed_at=datetime.fromtimestamp(hold_path.stat().st_mtime,timezone.utc),
                reason='HISTORICAL_TIMEOUT',clock=clock)
    return {'authorization':AUTHORIZATION,'source_day':day}


def allowed(plan):
    return plan.get('historical_resume')=={'authorization':AUTHORIZATION,'source_day':plan.get('held_source_day')}
