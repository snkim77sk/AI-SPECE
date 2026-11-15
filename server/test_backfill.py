"""Offline-only tests of the explicitly authorized Jan 1 collection range."""
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import types
import unittest
import sqlite3
from contextlib import closing
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import live_collection_once as live
import backfill_queue as queue
import db
from offline_fixtures import FixtureSource
from receipt_validation import readback
from vnext_source_guard import (manual_backfill_source_context,operational_recent_source_context,
    require_source_request_context,VNextSourceAccessError)


def no_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise AssertionError('REAL_NETWORK_FORBIDDEN')
sys.addaudithook(no_network)


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='g2b-backfill-test-')
        self.root=Path(self.temp.name)
        self.hold=self.root/'hold.json'
        self.hold.write_text('{"status":"HOLD"}',encoding='utf-8')
        latest=(live.now().date()-timedelta(days=1)).isoformat()
        self.held=(live.now().date()-timedelta(days=2)).isoformat()
        self.older=(live.now().date()-timedelta(days=3)).isoformat()
        self.plan={'run_id':'first','mode':'EXPLICIT_MANUAL_BACKFILL','budget_basis':'USER_ASSUMPTION',
            'assumed_remaining':900,'budget_day':live.now().date().isoformat(),'source_day':latest,
            'range_start':'2026-01-01','range_end':latest,'held_source_day':self.held,'max_pages':3,
            'max_seconds':85,'hold_sha256':hashlib.sha256(self.hold.read_bytes()).hexdigest()}
        self.ledger=live.ledger_open(self.root/'live-calls.sqlite3')
        queue.save_inventory(self.ledger,[
            {'day':day,'state':state,'db_path':str(self.root/f'g2b-real-{day}.sqlite3'),
             'next_page':1,'saved':0,'reason':''}
            for day,state in [(latest,'READY'),(self.held,'HOLD'),(self.older,'READY')]])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def invoke_worker(self,fixture,run_id='first'):
        run=self.root/run_id
        run.mkdir()
        plan=self.plan|{'run_id':run_id,'started_monotonic':time.monotonic()}
        live.write_json(run/'plan.json',plan)
        stdin=types.SimpleNamespace(buffer=io.BytesIO(b'GO\n'))
        with patch.object(live,'HOLD',self.hold),patch.object(live,'load_key',return_value='OFFLINE-TEST'), \
             patch.object(sys,'stdin',stdin),patch.object(urllib.request,'build_opener',return_value=fixture), \
             patch.dict(os.environ,{},clear=False),patch.object(db,'DB_PATH',db.DB_PATH):
            live.worker(run)
        return json.loads((run/'worker-result.json').read_text(encoding='utf-8'))

    def test_inclusive_newest_first_range(self):
        days=queue.enumerate_days('2026-01-01','2026-11-13')
        self.assertEqual((len(days),days[0],days[-1]),(317,'2026-11-13','2026-01-01'))
        with self.assertRaises(ValueError):
            queue.enumerate_days('2025-12-31','2026-11-13')

    def test_held_and_completed_days_are_skipped(self):
        latest=self.plan['source_day']
        self.assertEqual(queue.next_job(self.ledger,'2026-01-01',latest)['day'],latest)
        queue.record_verified(self.ledger,latest,{'complete':True,'next_page':2,'saved':1})
        self.assertEqual(queue.next_job(self.ledger,'2026-01-01',latest)['day'],self.older)

    def test_manual_range_and_original_guard_remain_distinct(self):
        with manual_backfill_source_context(collection_date='2026-01-01',range_start='2026-01-01',
             range_end=self.plan['range_end'],held_dates=[self.held]):
            pass
        with self.assertRaises(VNextSourceAccessError):
            with operational_recent_source_context(collection_date='2026-01-01'):
                pass
        for day in ('2025-12-31',self.held):
            with self.assertRaises(VNextSourceAccessError):
                with manual_backfill_source_context(collection_date=day,range_start='2026-01-01',
                     range_end=self.plan['range_end'],held_dates=[self.held]):
                    pass

    def test_reservation_cannot_cross_date_or_held_boundary(self):
        for day in ('2025-12-31',self.held,live.now().date().isoformat()):
            with self.assertRaises(live.Stop):
                live.reserve(self.ledger,self.plan,1,day)
        live.reserve(self.ledger,self.plan,1,'2026-01-01')
        self.assertEqual(self.ledger.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)

    def test_transport_only_accepts_exact_shopping_day(self):
        endpoint='https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList'
        query='?serviceKey=OFFLINE&numOfRows=999&pageNo=1&type=json&inqryDiv=1&inqryBgnDate=20260101&inqryEndDate=20260101'
        with manual_backfill_source_context(collection_date='2026-01-01',range_start='2026-01-01',
             range_end=self.plan['range_end'],held_dates=[self.held],max_requests=1):
            with self.assertRaises(VNextSourceAccessError):
                require_source_request_context(g2b_url=endpoint+query.replace('inqryEndDate=20260101','inqryEndDate=20260102'))
            require_source_request_context(g2b_url=endpoint+query)
            with self.assertRaises(VNextSourceAccessError):
                require_source_request_context(g2b_url=endpoint+query)

    def test_worker_finishes_day_then_resumes_next_day_without_duplicates(self):
        fixture=FixtureSource(total=1000)
        first=self.invoke_worker(fixture)
        self.assertEqual(first['stop_reason'],'PAGE_LIMIT')
        self.assertEqual([(c['day'],c['page']) for c in fixture.calls],
            [(self.plan['source_day'],1),(self.plan['source_day'],2),(self.older,1)])
        self.assertTrue(readback(self.root/f"g2b-real-{self.plan['source_day']}.sqlite3",self.plan['source_day'])['complete'])
        self.assertEqual(readback(self.root/f'g2b-real-{self.older}.sqlite3',self.older)['next_page'],2)
        second=self.invoke_worker(fixture,'second')
        self.assertEqual(second['stop_reason'],'NO_READY_DAYS')
        self.assertEqual((len(fixture.calls),fixture.calls[-1]['page']),(4,2))
        self.assertTrue(readback(self.root/f'g2b-real-{self.older}.sqlite3',self.older)['complete'])
        self.assertEqual(self.ledger.execute('SELECT SUM(verified_rows) FROM calls').fetchone()[0],2000)
        self.assertEqual(self.ledger.execute("SELECT state FROM backfill_days WHERE day=?",(self.held,)).fetchone()[0],'HOLD')

    def test_worker_timeout_has_one_attempt_and_keeps_hold(self):
        fixture=FixtureSource(total=1000,behaviors={1:'timeout'})
        result=self.invoke_worker(fixture)
        self.assertEqual(result['safe_error_code'],'NETWORK')
        self.assertEqual(len(fixture.calls),1)
        self.assertEqual(self.ledger.execute('SELECT state FROM calls').fetchone()[0],'UNCERTAIN')
        with self.assertRaises(live.Stop):
            live.reserve(self.ledger,self.plan,1,self.plan['source_day'])
        normal=FixtureSource(total=1000)
        continued=self.invoke_worker(normal,'after-error')
        self.assertEqual(continued['stop_reason'],'NO_READY_DAYS')
        self.assertEqual([(c['day'],c['page']) for c in normal.calls],[(self.older,1),(self.older,2)])
        self.assertEqual(self.ledger.execute("SELECT COUNT(*) FROM calls WHERE state='UNCERTAIN'").fetchone()[0],1)

    def test_scheduled_inventory_reuses_completed_days_but_validates_active_database(self):
        self.invoke_worker(FixtureSource(total=1000))
        original=self.root/'original.sqlite3'
        with closing(sqlite3.connect(original)) as conn:
            conn.execute('CREATE TABLE collection_checkpoints(dataset TEXT)')
        kwargs=dict(start='2026-01-01',end=self.plan['source_day'],held_day=self.held,
            held_page=1,held_saved=0,original_db=original,live_root=self.root)
        import receipt_validation
        with patch.object(receipt_validation,'readback',wraps=readback) as check:
            jobs=queue.inventory(**kwargs,cache_conn=self.ledger,reuse_completed=True)
            self.assertEqual([call.args[1] for call in check.call_args_list],[self.older])
            latest=next(job for job in jobs if job['day']==self.plan['source_day'])
            self.assertEqual((latest['state'],latest['saved']),('COMPLETE',1000))
        with closing(sqlite3.connect(self.root/f'g2b-real-{self.older}.sqlite3')) as conn:
            with conn: conn.execute("UPDATE vnext_collection_items SET payload_sha256='broken' WHERE rowid=1")
        jobs=queue.inventory(**kwargs,cache_conn=self.ledger,reuse_completed=True)
        self.assertEqual(next(job for job in jobs if job['day']==self.older)['state'],'HOLD')
        with patch.object(receipt_validation,'readback',wraps=readback) as check:
            queue.inventory(**kwargs)
            self.assertIn(self.plan['source_day'],[call.args[1] for call in check.call_args_list])

    def test_late_unsent_request_yields_and_resumes_same_page(self):
        fixture=FixtureSource(total=1000)
        with patch.object(live.time,'monotonic',side_effect=[100.0,100.0,151.0]):
            result=self.invoke_worker(fixture,'late')
        self.assertEqual(result['stop_reason'],'DEADLINE_MARGIN')
        self.assertEqual(fixture.calls,[])
        self.assertEqual(self.ledger.execute('SELECT state,attempted FROM calls').fetchone()[:],('NOT_SENT',0))
        proof=readback(self.root/f"g2b-real-{self.plan['source_day']}.sqlite3",self.plan['source_day'])
        self.assertEqual((proof['status'],proof['next_page']),('RUNNING',1))
        self.invoke_worker(fixture,'resumed')
        self.assertEqual((fixture.calls[0]['day'],fixture.calls[0]['page']),(self.plan['source_day'],1))


if __name__=='__main__':
    unittest.main(verbosity=2)
