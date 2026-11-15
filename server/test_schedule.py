"""Offline verification of automatic policy, package binding and scheduled worker."""
import hashlib
import json
import sqlite3
import os
import sys
import time
import subprocess
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import live_collection_once as live
import backfill_queue
import schedule_policy as policy
import scheduled_entry
from offline_fixtures import FixtureSource
import test_backfill as backfill_fixtures


class PolicyTests(unittest.TestCase):
    def test_locked_snapshot_reuses_proof_and_invalidates_writes_and_journals(self):
        with tempfile.TemporaryDirectory(prefix='g2b-identity-test-') as temp:
            root=Path(temp); path=root/'fixture.db'; path.write_bytes(b'first')
            conn=sqlite3.connect(root/'cache.sqlite3')
            calls=[]
            def load():
                calls.append(1)
                return {'verified':True,'value':path.read_text()}
            try:
                backfill_queue.validated_snapshot(conn,path,load,reuse_unchanged=True)
                with patch.object(hashlib,'file_digest',side_effect=AssertionError('unexpected full scan')):
                    self.assertEqual(backfill_queue.validated_snapshot(conn,path,load,reuse_unchanged=True)['value'],'first')
                path.write_bytes(b'updated')
                self.assertEqual(backfill_queue.validated_snapshot(conn,path,load,reuse_unchanged=True)['value'],'updated')
                self.assertEqual(len(calls),2)
                Path(str(path)+'-wal').write_bytes(b'pending transaction')
                backfill_queue.validated_snapshot(conn,path,load,reuse_unchanged=True)
                self.assertEqual(len(calls),3)
            finally:
                conn.close()

    def test_interrupted_preparation_records_truth_and_remains_resumable(self):
        with tempfile.TemporaryDirectory(prefix='g2b-interrupted-test-') as temp:
            root=Path(temp); run_id='scheduled-test'
            conn=live.ledger_open(root/'live-calls.sqlite3')
            with conn:
                conn.execute("INSERT INTO calls(run_id,source_day,page,budget_day,state,attempted) VALUES(?,?,?,?,?,1)",
                    (run_id,'2026-01-01',1,live.now().date().isoformat(),'UNCERTAIN'))
            try:
                with patch.object(scheduled_entry,'STATE_ROOT',root):
                    result=scheduled_entry.interrupted_result(run_id,time.monotonic(),
                        {'timed_out':True,'controller_exit_code':1,'process_tree_stopped':True})
                self.assertEqual((result['attempted'],result['verified_pages']),(1,0))
                self.assertEqual(result['stop_reason'],'CYCLE_DEADLINE')
                self.assertTrue(result['resume_on_next_schedule'])
                self.assertFalse(scheduled_entry.run_verified(result))
                self.assertEqual(conn.execute('SELECT state FROM calls').fetchone()[0],'UNCERTAIN')
                self.assertEqual(conn.execute('SELECT state FROM runs').fetchone()[0],'INTERRUPTED_HELD')
            finally:
                conn.close()

    @unittest.skipUnless(os.name=='nt','Windows job integration')
    def test_preparation_hang_is_killed_before_outer_task_limit(self):
        from bounded_cycle import run_bounded
        with tempfile.TemporaryDirectory(prefix='g2b-watchdog-test-') as temp:
            root=Path(temp); started=time.monotonic()
            result=run_bounded([sys.executable,'-c',"import sys,time; assert sys.stdin.buffer.readline()==b'GO\\n'; time.sleep(30)"],
                cwd=root,log=subprocess.DEVNULL,started=started,max_seconds=.4)
            self.assertTrue(result['timed_out'])
            self.assertTrue(result['process_tree_stopped'])
            self.assertGreater(result['peak_job_memory_bytes'],0)
            self.assertLess(time.monotonic()-started,3)

    @unittest.skipUnless(os.name=='nt','Windows nested job integration')
    def test_bounded_controller_can_run_existing_nested_worker_job(self):
        from bounded_cycle import run_bounded
        with tempfile.TemporaryDirectory(prefix='g2b-nested-test-') as temp:
            root=Path(temp)
            code="""import sys,subprocess
from pathlib import Path
assert sys.stdin.buffer.readline()==b'GO\\n'
sys.path.insert(0,sys.argv[1])
from windows_limits import WindowsJob
p=subprocess.Popen([sys.executable,'-c',"import sys;sys.stdin.buffer.readline()"],stdin=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
with WindowsJob(p,256):
    p.stdin.write(b'GO\\n');p.stdin.flush();p.stdin.close();p.wait(timeout=3)
assert p.returncode==0
Path(sys.argv[2]).write_text('nested worker completed')
"""
            result=run_bounded([sys.executable,'-c',code,str(Path(live.SOURCE)),str(root/'completed.txt')],
                cwd=root,log=subprocess.DEVNULL,started=time.monotonic(),max_seconds=4)
            self.assertEqual(result['controller_exit_code'],0)
            self.assertFalse(result['timed_out'])
            self.assertEqual((root/'completed.txt').read_text(),'nested worker completed')
            self.assertLess(result['peak_job_memory_bytes'],256*1024*1024)

    def test_initial_assumption_and_kst_reset(self):
        self.assertEqual(policy.day_policy(datetime(2026,11,14,14,59,tzinfo=timezone.utc))['effective_day_budget'],900)
        self.assertEqual(policy.day_policy(datetime(2026,11,14,15,0,tzinfo=timezone.utc))['effective_day_budget'],950)

    def test_midnight_margin(self):
        self.assertTrue(policy.cycle_fits_before_reset(datetime(2026,11,14,23,58,29,tzinfo=policy.KST)))
        self.assertFalse(policy.cycle_fits_before_reset(datetime(2026,11,14,23,58,30,tzinfo=policy.KST)))
        self.assertTrue(policy.cycle_fits_before_reset(datetime(2026,11,15,0,0,tzinfo=policy.KST)))

    def test_same_quarter_has_same_run_id(self):
        a=datetime(2026,11,14,22,31,tzinfo=policy.KST)
        b=datetime(2026,11,14,22,44,59,tzinfo=policy.KST)
        c=datetime(2026,11,14,22,45,tzinfo=policy.KST)
        self.assertEqual(policy.slot_id(a),policy.slot_id(b))
        self.assertNotEqual(policy.slot_id(b),policy.slot_id(c))

    def test_daily_limit_shared_across_runs_and_uncertain_never_reset(self):
        with tempfile.TemporaryDirectory(prefix='g2b-schedule-test-') as temp:
            conn=live.ledger_open(Path(temp)/'ledger.sqlite3')
            try:
                clock=datetime(2026,11,15,10,0,tzinfo=policy.KST)
                plan={'run_id':'new','mode':'WINDOWS_SCHEDULED_BACKFILL','source_day':'2026-11-14',
                    'range_start':'2026-01-01','range_end':'2026-11-14','held_source_day':'2026-11-12',
                    'max_pages':8,'max_seconds':85,'schedule_name':'G2B-Bounded-Backfill-20260101',**policy.day_policy(clock)}
                with conn:
                    conn.executemany("INSERT INTO calls(run_id,source_day,page,budget_day,state) VALUES('prior','2026-01-01',?,'2026-11-15','VERIFIED')",[(n,) for n in range(1,951)])
                with patch.object(live,'now',return_value=clock):
                    with self.assertRaisesRegex(live.Stop,'BUDGET_EXHAUSTED'):
                        live.reserve(conn,plan,1)
                    with self.assertRaisesRegex(live.Stop,'SCHEDULE_BUDGET_MISMATCH'):
                        live.reserve(conn,plan|{'effective_day_budget':951},1)
                with conn:
                    conn.execute("UPDATE calls SET budget_day='2026-11-14',state='UNCERTAIN' WHERE id=1")
                with patch.object(live,'now',return_value=clock):
                    live.reserve(conn,plan,1)
                    with self.assertRaisesRegex(live.Stop,'PENDING_OR_UNCERTAIN_NO_RETRY'):
                        live.reserve(conn,plan,1,source_day='2026-01-01')
            finally:
                conn.close()

    def test_snapshot_cache_requires_full_content_match(self):
        with tempfile.TemporaryDirectory(prefix='g2b-cache-test-') as temp:
            root=Path(temp)
            path=root/'fixture.db'
            path.write_bytes(b'first')
            conn=sqlite3.connect(root/'cache.sqlite3')
            try:
                calls=[]
                def load():
                    calls.append(1)
                    return {'verified':True}
                backfill_queue.validated_snapshot(conn,path,load)
                backfill_queue.validated_snapshot(conn,path,load)
                self.assertEqual(len(calls),1)
                path.write_bytes(b'other')
                backfill_queue.validated_snapshot(conn,path,load)
                self.assertEqual(len(calls),2)
            finally:
                conn.close()

    def test_package_changed_file_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='g2b-package-test-') as temp:
            root=Path(temp)
            path=root/'program.py'
            path.write_bytes(b'expected')
            (root/'runtime-manifest.json').write_text(json.dumps({'task_name':scheduled_entry.TASK_NAME,
                'files':{'program.py':hashlib.sha256(path.read_bytes()).hexdigest()}}),encoding='utf-8')
            scheduled_entry.verify_package(root)
            path.write_bytes(b'changed!')
            with self.assertRaisesRegex(ValueError,'RUNTIME_FILE_CHANGED'):
                scheduled_entry.verify_package(root)

    def test_workspace_entry_cannot_start_collection(self):
        with self.assertRaisesRegex(RuntimeError,'DEPLOYED_TASK_ENTRY_REQUIRED'):
            scheduled_entry.main()

    def test_zero_call_initialization_failure_is_not_success(self):
        report={'reserved':0,'verified_pages':0,'run_state':'FINISHED','under_90_seconds':True,
            'worker_exited':True,'worker_exit_code':0,'days_readback':{},'stop_reason':'OSError'}
        self.assertFalse(scheduled_entry.run_verified(report))
        self.assertTrue(scheduled_entry.run_verified(report|{'stop_reason':'NO_READY_DAYS'}))


class ScheduledWorkerTests(unittest.TestCase):
    def test_scheduled_mode_saves_and_resumes_using_existing_worker(self):
        case=backfill_fixtures.BackfillTests()
        case.setUp()
        try:
            case.plan.update(policy.day_policy(live.now()))
            case.plan.update(mode='WINDOWS_SCHEDULED_BACKFILL',schedule_name='G2B-Bounded-Backfill-20260101')
            fixture=FixtureSource(total=1000)
            result=case.invoke_worker(fixture)
            self.assertEqual(result['stop_reason'],'PAGE_LIMIT')
            self.assertEqual(len(fixture.calls),3)
            self.assertEqual(case.ledger.execute('SELECT SUM(verified_rows) FROM calls').fetchone()[0],1999)
            self.assertEqual(fixture.network_denials,0)
        finally:
            case.tearDown()


if __name__=='__main__':
    unittest.main(verbosity=2)
