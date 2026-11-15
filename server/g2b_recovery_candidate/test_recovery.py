import hashlib
import json
import os
import sqlite3
import tempfile
import time
import tracemalloc
import unittest
from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

import db
import vnext_http
from bounded_runner import Ledger,Stop,TEST_ROOT,KST,run_once,kst_day
from offline_fixtures import FixtureSource
from process_lock import LocalDbProcessLock
from receipt_validation import readback
from offline_supervisor import supervise

TEST_RESULTS=[]

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root=Path(self.temp.name)
        self.db=self.root/'collector.sqlite3'
        self.ledger=self.root/'ledger.sqlite3'
        self.hold=self.root/'HOLD.json'
        self.now=datetime.now(KST)
        self.day=(self.now.date()-timedelta(days=1)).isoformat()
        self.older=(self.now.date()-timedelta(days=2)).isoformat()
        self.serial=0

    def tearDown(self):
        self.temp.cleanup()

    def budget(self,**kwargs):
        ledger=Ledger(self.ledger)
        try:
            ledger.fixture_budget(self.now.date().isoformat(),**kwargs)
        finally:
            ledger.close()

    def run_cycle(self,fixture=None,**kwargs):
        self.serial+=1
        args=dict(db_path=self.db,ledger_path=self.ledger,days=[self.day],hold_path=self.hold,
                  run_id=str(self.serial),fixture=fixture or FixtureSource(total=50,page_size=10),now=lambda:self.now)
        args.update(kwargs)
        result=run_once(**args)
        TEST_RESULTS.append({'test':self._testMethodName,**{k:v for k,v in result.items() if k!='memory_samples'}})
        return result

    def rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute('SELECT COUNT(*) FROM raw_records').fetchone()[0]

    def test_save_resume_complete_skip_and_stage_counts(self):
        self.budget()
        fixture=FixtureSource(total=25,page_size=10)
        first=self.run_cycle(fixture,max_pages=2)
        self.assertEqual([first[k] for k in ('attempted','response_received','response_confirmed','stored_pages','verified_pages','verified_rows')],[2,2,2,2,2,20])
        self.assertEqual(readback(self.db,self.day)['next_page'],3)
        second=self.run_cycle(fixture,max_pages=8)
        self.assertEqual((second['attempted'],second['verified_rows'],self.rows()),(1,5,25))
        third=self.run_cycle(fixture)
        self.assertEqual(third['attempted'],0)
        self.assertEqual([c['page'] for c in fixture.calls],[1,2,3])
        self.assertTrue(all(c['timeout']<=10 for c in fixture.calls))

    def test_latest_completed_day_first(self):
        self.budget()
        fixture=FixtureSource(total=1,page_size=10)
        self.run_cycle(fixture,days=[self.older,self.day],max_pages=2)
        self.assertEqual([c['day'] for c in fixture.calls],[self.day,self.older])

    def test_external_hold_zero_attempts(self):
        self.budget()
        self.hold.write_text('{"status":"HOLD"}')
        result=self.run_cycle()
        self.assertEqual((result['stop_reason'],result['attempted']),('EXTERNAL_HOLD',0))
        self.assertTrue(self.hold.exists())

    def test_unknown_quota_blocks(self):
        result=self.run_cycle()
        self.assertEqual((result['stop_reason'],result['attempted']),('UNRECONCILED_KST_USAGE',0))
        self.assertIsNone(result['usage']['remaining'])

    def test_timeout_is_not_retried_in_same_or_next_run(self):
        self.budget()
        fixture=FixtureSource(total=30,page_size=10,behaviors={2:'timeout'})
        first=self.run_cycle(fixture)
        self.assertEqual((first['attempted'],first['response_confirmed'],first['stored_pages'],first['verified_rows']),(2,1,1,10))
        next_run=self.run_cycle(fixture)
        self.assertEqual((next_run['stop_reason'],next_run['attempted']),('UNCERTAIN_OR_PENDING_NO_RETRY',0))
        self.assertEqual(len(fixture.calls),2)
        self.assertEqual(first['usage']['used'],2)

    def test_malformed_response_distinct_from_received(self):
        self.budget()
        result=self.run_cycle(FixtureSource(behaviors={1:'malformed'}))
        self.assertEqual((result['attempted'],result['response_received'],result['response_confirmed'],result['stored_pages']),(1,1,0,0))

    def test_rate_limit_no_retry(self):
        self.budget()
        fixture=FixtureSource(behaviors={1:'rate_limit'})
        result=self.run_cycle(fixture)
        self.assertEqual((result['attempted'],len(fixture.calls),result['response_confirmed']),(1,1,0))

    def test_http_error_is_a_received_but_unconfirmed_response(self):
        self.budget()
        fixture=FixtureSource(behaviors={1:'http_error'})
        result=self.run_cycle(fixture)
        self.assertEqual((result['attempted'],result['response_received'],result['response_confirmed'],len(fixture.calls)),(1,1,0,1))

    def test_checkpoint_readback_failure_is_separate_from_committed_storage(self):
        self.budget()
        def corrupt(path,day,page):
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('UPDATE collection_checkpoints SET saved_count=saved_count+1')
                conn.commit()
        result=self.run_cycle(after_store=corrupt)
        self.assertEqual((result['stored_pages'],result['verified_pages'],self.rows()),(1,0,10))
        self.assertEqual(self.run_cycle()['attempted'],0)

    def test_oversized_response_is_not_stored(self):
        self.budget()
        result=self.run_cycle(FixtureSource(behaviors={1:'oversize'}))
        self.assertEqual((result['attempted'],result['stored_pages']),(1,0))

    def test_unbounded_read_transport_is_rejected(self):
        class BadTransport:
            def read(self):
                raise AssertionError('unbounded read must never happen')
        with self.assertRaises(vnext_http.VNextResponseError):
            vnext_http._read_response_limited(BadTransport(),strict=True)

    def test_overlapping_page_preserves_prior_raw(self):
        self.budget()
        fixture=FixtureSource(total=30,page_size=10,behaviors={2:'overlap'})
        self.run_cycle(fixture,max_pages=1)
        with closing(sqlite3.connect(self.db)) as conn:
            before=conn.execute('SELECT source_key,payload_sha256 FROM raw_records ORDER BY source_key').fetchall()
        result=self.run_cycle(fixture)
        with closing(sqlite3.connect(self.db)) as conn:
            after=conn.execute('SELECT source_key,payload_sha256 FROM raw_records ORDER BY source_key').fetchall()
        self.assertEqual(before,after)
        self.assertEqual((result['response_confirmed'],result['stored_pages']),(1,0))

    def test_total_change_is_held_without_saving(self):
        self.budget()
        result=self.run_cycle(FixtureSource(total=30,page_size=10,behaviors={2:'changed_total'}))
        self.assertEqual((result['response_confirmed'],result['stored_pages'],self.rows()),(2,1,10))

    def test_crash_gap_after_commit_holds_without_replay(self):
        self.budget()
        def fail(*args):
            raise RuntimeError('simulated verifier interruption')
        result=self.run_cycle(after_store=fail)
        self.assertEqual((result['stored_pages'],result['verified_pages'],self.rows()),(1,0,10))
        again=self.run_cycle()
        self.assertEqual((again['attempted'],again['stop_reason']),(0,'UNCERTAIN_OR_PENDING_NO_RETRY'))
        self.assertEqual(readback(self.db,self.day)['next_page'],2)

    def test_bad_checkpoint_does_not_silently_replay_page_one(self):
        self.budget()
        self.run_cycle(max_pages=1)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('UPDATE collection_checkpoints SET page_no=50')
            conn.commit()
        fixture=FixtureSource(total=50,page_size=10)
        result=self.run_cycle(fixture)
        self.assertEqual((result['attempted'],len(fixture.calls),self.rows()),(0,0,10))

    def test_missing_revision_blocks_resume(self):
        self.budget()
        self.run_cycle(max_pages=1)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('DELETE FROM raw_record_revisions WHERE id=(SELECT MIN(id) FROM raw_record_revisions)')
            conn.commit()
        result=self.run_cycle()
        self.assertEqual(result['attempted'],0)

    def test_same_invocation_cannot_run_twice(self):
        self.budget()
        self.run_cycle(run_id='fixed',max_pages=1)
        result=self.run_cycle(run_id='fixed',max_pages=1)
        self.assertEqual((result['stop_reason'],result['attempted']),('RUN_ALREADY_INVOKED',0))

    def test_existing_os_lock_blocks_duplicate(self):
        self.budget()
        with LocalDbProcessLock(self.db):
            result=self.run_cycle()
        self.assertEqual((result['stop_reason'],result['attempted']),('DUPLICATE_PROCESS',0))

    def test_ledger_cannot_be_rebound_to_another_db(self):
        self.budget()
        self.run_cycle(max_pages=1)
        result=self.run_cycle(db_path=self.root/'other.sqlite3')
        self.assertEqual((result['stop_reason'],result['attempted']),('LEDGER_DB_BINDING_MISMATCH',0))

    def test_daily_hard_cap_950(self):
        self.budget(used=949,remaining=20,daily_limit=1000)
        result=self.run_cycle()
        self.assertEqual((result['attempted'],result['usage']['used'],result['usage']['remaining']),(1,950,0))

    def test_official_lower_remaining_wins(self):
        self.budget(used=200,remaining=1,daily_limit=900)
        result=self.run_cycle()
        self.assertEqual((result['attempted'],result['usage']['remaining']),(1,0))

    def test_official_lower_page_cap_wins(self):
        self.budget(page_limit=2)
        result=self.run_cycle(FixtureSource(total=100,page_size=10))
        self.assertEqual(result['attempted'],2)

    def test_kst_midnight_requires_fresh_evidence(self):
        before=datetime(2026,11,13,14,59,59,tzinfo=timezone.utc)
        after=before+timedelta(seconds=1)
        ledger=Ledger(self.ledger)
        try:
            ledger.fixture_budget(kst_day(before),used=949,remaining=1)
            self.assertEqual(ledger.usage(before)['used'],949)
            self.assertIsNone(ledger.usage(after)['used'])
            self.assertEqual(ledger.usage(after)['effective_remaining'],0)
        finally:
            ledger.close()

    def test_deadline_margin_stops_before_dispatch(self):
        self.budget()
        result=self.run_cycle(monotonic=lambda:100.0,deadline=129.0)
        self.assertEqual((result['attempted'],result['stop_reason']),(0,'DEADLINE_MARGIN'))

    def test_test_paths_and_live_disabled(self):
        with self.assertRaisesRegex(Stop,'ISOLATED'):
            self.run_cycle(db_path=Path('D:/G2B/data/g2b-local.sqlite3'))
        with self.assertRaisesRegex(Stop,'LIVE_EXECUTION_DISABLED'):
            run_once(db_path=self.db,ledger_path=self.ledger,days=[self.day],hold_path=self.hold,run_id='x')
        with self.assertRaisesRegex(Stop,'INVALID_RUN_LIMIT'):
            self.run_cycle(max_pages=9)

    def test_guard_rejects_today_without_network(self):
        self.budget()
        fixture=FixtureSource()
        result=self.run_cycle(fixture,days=[self.now.date().isoformat()])
        self.assertEqual((result['stop_reason'],len(fixture.calls)),('SOURCE_DAY_OUTSIDE_EXISTING_GUARD',0))

    def test_page_memory_is_bounded_across_two_cycles(self):
        self.budget()
        fixture=FixtureSource(total=999*16,page_size=999,payload_bytes=2048)
        tracemalloc.start()
        try:
            first=self.run_cycle(fixture,max_pages=8)
            second=self.run_cycle(fixture,max_pages=8)
        finally:
            tracemalloc.stop()
        self.assertEqual(first['verified_pages']+second['verified_pages'],16)
        samples=first['memory_samples']+second['memory_samples']
        self.assertLess(max(s['private_bytes'] for s in samples),256*1024*1024)
        self.assertLess(max(s['python_retained_bytes'] for s in samples[2:])-min(s['python_retained_bytes'] for s in samples[2:]),3*1024*1024)
        evidence=TEST_ROOT.parent.parent/'evidence'/'memory-pages.json'
        evidence.parent.mkdir(parents=True,exist_ok=True)
        evidence.write_text(json.dumps({'fixture_rows':999*16,'payload_bytes_per_row':2048,'cycles':[first,second]},indent=2),encoding='utf-8')

    def test_supervisor_kills_hung_request_and_keeps_pending(self):
        result=supervise(self.root,scenario='hang',seconds=2)
        self.assertEqual(result['reason'],'DEADLINE_KILLED_NO_RETRY')
        self.assertTrue(result['process_exited'])
        self.assertLess(result['elapsed_seconds'],6)
        next_run=supervise(self.root,scenario='resume',seconds=10)
        report=json.loads((self.root/'worker-result.json').read_text())
        self.assertEqual((report['stop_reason'],report['attempted']),('UNCERTAIN_OR_PENDING_NO_RETRY',0))
        self.assertEqual(next_run['spawn_count'],1)

    def test_os_memory_limit_rejects_excess_allocation(self):
        result=supervise(self.root,scenario='memory',seconds=10,memory_mb=64)
        self.assertTrue(result['process_exited'])
        self.assertTrue(json.loads((self.root/'memory-limit.json').read_text())['allocation_denied'])
        self.assertEqual(result['job_memory_limit_mb'],64)

    def test_process_crash_never_restarts(self):
        result=supervise(self.root,scenario='crash',seconds=10)
        self.assertEqual((result['exit_code'],result['spawn_count'],result['retry_count']),(77,1,0))
        supervise(self.root,scenario='resume',seconds=10)
        report=json.loads((self.root/'worker-result.json').read_text())
        self.assertEqual(report['attempted'],0)


if __name__=='__main__':
    unittest.main(verbosity=2)
