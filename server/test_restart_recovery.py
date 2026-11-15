"""Power-interruption tests use disposable databases and zero real network calls."""
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import live_collection_once as live
import backfill_queue
import restart_recovery as recovery
import db
from offline_fixtures import FixtureSource
from process_lock import LocalDbProcessLock
from receipt_validation import readback


def deny_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise AssertionError('REAL_NETWORK_FORBIDDEN')
sys.addaudithook(deny_network)


def invoke(root, crash=None):
    """A separate process can disappear while holding SQLite/Windows locks."""
    import urllib.request
    import shopping_vnext as shop
    plan=json.loads((root/'fixture-plan.json').read_text())
    run=root/('crashed' if crash else 'continued')
    run.mkdir()
    plan.update(run_id=run.name,started_monotonic=time.monotonic())
    live.write_json(run/'plan.json',plan)
    fixture=FixtureSource(total=1000)
    real_phase=live.phase
    real_preserve=shop.preserve_raw
    def preserve(*args,**kwargs):
        result=real_preserve(*args,**kwargs)
        if crash=='mid_transaction':
            os._exit(83)
        return result
    def phase(conn, call_id, event):
        if crash=='before_send' and event=='attempted':
            os._exit(83)
        if crash=='after_commit' and event=='stored':
            os._exit(83)
        real_phase(conn,call_id,event)
        if crash=='uncertain' and event=='attempted':
            os._exit(83)
    with patch.object(live,'HOLD',root/'hold.json'),patch.object(live,'load_key',return_value='OFFLINE-TEST'), \
         patch.object(sys,'stdin',types.SimpleNamespace(buffer=io.BytesIO(b'GO\n'))), \
         patch.object(urllib.request,'build_opener',return_value=fixture),patch.object(live,'phase',side_effect=phase), \
         patch.object(shop,'preserve_raw',side_effect=preserve), \
         patch.dict(os.environ,{},clear=False),patch.object(db,'DB_PATH',db.DB_PATH):
        live.worker(run)
    (run/'fixture-calls.json').write_text(json.dumps(fixture.calls))


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='g2b-restart-test-')
        self.root=Path(self.temp.name)
        self.day=(live.now().date()-timedelta(days=1)).isoformat()
        self.held=(live.now().date()-timedelta(days=2)).isoformat()
        hold=self.root/'hold.json'
        hold.write_text('{"status":"HOLD"}')
        self.plan={'run_id':'crashed','mode':'EXPLICIT_MANUAL_BACKFILL','budget_basis':'USER_ASSUMPTION',
            'assumed_remaining':900,'budget_day':live.now().date().isoformat(),'source_day':self.day,
            'range_start':'2026-01-01','range_end':self.day,'held_source_day':self.held,'max_pages':1,
            'max_seconds':85,'hold_sha256':hashlib.sha256(hold.read_bytes()).hexdigest()}
        (self.root/'fixture-plan.json').write_text(json.dumps(self.plan))
        self.ledger=live.ledger_open(self.root/'live-calls.sqlite3')
        backfill_queue.save_inventory(self.ledger,[{'day':self.day,'state':'READY',
            'db_path':str(self.root/f'g2b-real-{self.day}.sqlite3'),'next_page':1,'saved':0,'reason':''}])
        with self.ledger:
            self.ledger.execute("INSERT INTO runs VALUES('crashed',?,'RUNNING')",(self.plan['budget_day'],))

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def crash(self,stage):
        child=subprocess.run([sys.executable,'-B',__file__,'--crash-child',str(self.root),stage],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(child.returncode,83,child.stderr.decode(errors='replace'))

    def reconcile(self):
        with LocalDbProcessLock(self.root/'live-calls.sqlite3'):
            return recovery.reconcile(self.ledger,self.root,held_day=self.held)

    def test_crash_before_transport_can_resume_without_erasing_call_history(self):
        self.crash('before_send')
        result=self.reconcile()
        self.assertEqual(result['outcomes'][0]['outcome'],'NOT_SENT')
        self.assertFalse(result['unresolved'])
        invoke(self.root)
        calls=[dict(row) for row in self.ledger.execute('SELECT * FROM calls ORDER BY id')]
        self.assertEqual([(row['page'],row['state'],row['attempted']) for row in calls],[(1,'NOT_SENT',0),(1,'VERIFIED',1)])
        self.assertEqual(readback(self.root/f'g2b-real-{self.day}.sqlite3',self.day)['saved'],999)
        self.assertEqual(self.ledger.execute('SELECT COUNT(*) FROM restart_reconciliations').fetchone()[0],1)

    def test_crash_after_page_commit_resumes_next_page_with_no_duplicate(self):
        self.crash('after_commit')
        path=self.root/f'g2b-real-{self.day}.sqlite3'
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        result=self.reconcile()
        self.assertEqual(result['outcomes'][0]['outcome'],'VERIFIED_FROM_COMMIT')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
        self.assertEqual(self.ledger.execute('SELECT stored,verified,verified_rows FROM calls').fetchone()[:],(1,1,999))
        receipt=readback(path,self.day)
        backfill_queue.record_verified(self.ledger,self.day,receipt)
        invoke(self.root)
        self.assertEqual([r[0] for r in self.ledger.execute('SELECT page FROM calls ORDER BY id')],[1,2])
        self.assertEqual(readback(path,self.day)['saved'],1000)
        self.assertTrue(readback(path,self.day)['complete'])

    def test_attempted_unknown_stays_held_after_restart_and_midnight(self):
        self.crash('uncertain')
        with self.ledger:
            self.ledger.execute("UPDATE calls SET budget_day='2026-01-01'")
        result=self.reconcile()
        self.assertTrue(result['unresolved'])
        self.assertEqual(result['network_calls'],0)
        self.assertEqual(result['outcomes'][0]['outcome'],'HOLD_UNCERTAIN_NO_RETRY')
        with self.assertRaisesRegex(live.Stop,'PENDING_OR_UNCERTAIN_NO_RETRY'):
            live.reserve(self.ledger,self.plan,1)

    def test_power_loss_in_transaction_rolls_back_partial_raw_and_keeps_unknown_hold(self):
        self.crash('mid_transaction')
        result=self.reconcile()
        self.assertTrue(result['unresolved'])
        path=self.root/f'g2b-real-{self.day}.sqlite3'
        conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        try:
            self.assertEqual(conn.execute('PRAGMA quick_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM raw_records').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM vnext_collection_pages').fetchone()[0],0)
        finally:
            conn.close()
        self.assertEqual(readback(path,self.day)['next_page'],1)

    def test_historical_hold_cannot_be_released_even_with_no_attempt(self):
        with self.ledger:
            self.ledger.execute('INSERT INTO calls(run_id,source_day,page,budget_day) VALUES(?,?,?,?)',
                                ('crashed',self.held,14,self.plan['budget_day']))
        self.assertTrue(self.reconcile()['unresolved'])
        self.assertEqual(self.ledger.execute('SELECT state FROM calls').fetchone()[0],'RESERVED')

    def test_corrupt_committed_receipt_is_never_marked_verified(self):
        self.crash('after_commit')
        path=self.root/f'g2b-real-{self.day}.sqlite3'
        conn=sqlite3.connect(path)
        with conn:
            conn.execute("UPDATE vnext_collection_pages SET response_hash='invalid'")
        conn.close()
        self.assertTrue(self.reconcile()['unresolved'])
        self.assertEqual(self.ledger.execute('SELECT verified FROM calls').fetchone()[0],0)

    def test_old_ledger_migration_preserves_every_row_and_unique_sent_page(self):
        path=self.root/'legacy.sqlite3'
        conn=sqlite3.connect(path)
        conn.row_factory=sqlite3.Row
        conn.execute(recovery.CALLS_SCHEMA[:-1]+',UNIQUE(source_day,page))')
        with conn:
            conn.execute("INSERT INTO calls(run_id,source_day,page,budget_day,state,attempted) VALUES('old','2026-01-01',1,'2026-11-14','VERIFIED',1)")
        before=tuple(conn.execute('SELECT * FROM calls').fetchone())
        recovery.ensure_schema(conn)
        self.assertEqual(tuple(conn.execute('SELECT * FROM calls').fetchone()),before)
        self.assertEqual(tuple(conn.execute('SELECT * FROM calls_before_restart_v2').fetchone()),before)
        recovery.ensure_schema(conn)
        with self.assertRaises(sqlite3.IntegrityError),conn:
            conn.execute("INSERT INTO calls(run_id,source_day,page,budget_day) VALUES('new','2026-01-01',1,'2026-11-14')")
        conn.close()

    def test_proven_pretransport_error_is_released_without_source_call(self):
        with self.ledger:
            self.ledger.execute("INSERT INTO calls(run_id,source_day,page,budget_day,state) VALUES(?,?,1,?,'UNCERTAIN')",
                                ('crashed',self.day,self.plan['budget_day']))
        result=self.reconcile()
        self.assertFalse(result['unresolved'])
        self.assertEqual(result['network_calls'],0)
        self.assertEqual(self.ledger.execute('SELECT state FROM calls').fetchone()[0],'NOT_SENT')


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash-child':
        invoke(Path(sys.argv[2]),sys.argv[3])
    else:
        unittest.main(verbosity=2)
