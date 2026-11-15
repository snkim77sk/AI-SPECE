"""Offline tests of new live orchestration guards; no key or live API needed."""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import live_collection_once as live


def no_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise AssertionError('NETWORK_FORBIDDEN_IN_TEST')
sys.addaudithook(no_network)


class ManualGuards(unittest.TestCase):
    def setUp(self):
        self.plan={'run_id':'offline-guard-test','mode':'EXPLICIT_MANUAL_LATEST_ONLY',
            'budget_basis':'USER_ASSUMPTION','assumed_remaining':900,
            'budget_day':live.now().date().isoformat(),
            'source_day':(live.now().date()-timedelta(days=1)).isoformat(),
            'held_source_day':(live.now().date()-timedelta(days=2)).isoformat(),
            'max_pages':8,'max_seconds':85}
        self.temp=tempfile.TemporaryDirectory(prefix='g2b-live-guard-')
        self.ledger=live.ledger_open(Path(self.temp.name)/'ledger.sqlite3')

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_requires_latest_scope_and_limits(self):
        live.validate_plan(self.plan)
        for change in ({'source_day':self.plan['held_source_day']},
                       {'held_source_day':self.plan['source_day']},
                       {'max_pages':9},{'assumed_remaining':901},
                       {'budget_day':'2000-01-01'},{'max_seconds':91}):
            with self.subTest(change=change), self.assertRaises(live.Stop):
                live.validate_plan(self.plan|change)

    def test_unresolved_attempt_is_never_retried(self):
        first=live.reserve(self.ledger,self.plan,1)
        live.phase(self.ledger,first,'attempted')
        with self.assertRaises(live.Stop):
            live.reserve(self.ledger,self.plan,1)
        with self.assertRaises(live.Stop):
            live.reserve(self.ledger,self.plan,2)
        self.assertEqual(self.ledger.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)

    def test_phases_and_unique_scope(self):
        first=live.reserve(self.ledger,self.plan,1)
        for event in ('attempted','response_received','response_confirmed','stored'):
            live.phase(self.ledger,first,event)
        with self.ledger:
            self.ledger.execute("UPDATE calls SET state='VERIFIED',verified=1 WHERE id=?",(first,))
        with self.assertRaises(sqlite3.IntegrityError):
            live.reserve(self.ledger,self.plan,1)
        live.reserve(self.ledger,self.plan,2)
        self.assertEqual(self.ledger.execute('SELECT SUM(attempted) FROM calls').fetchone()[0],1)

    def test_page_limit(self):
        for page in range(1,9):
            call=live.reserve(self.ledger,self.plan,page)
            with self.ledger:
                self.ledger.execute("UPDATE calls SET state='VERIFIED' WHERE id=?",(call,))
        with self.assertRaises(live.Stop):
            live.reserve(self.ledger,self.plan,9)

    def test_daily_assumption_limit(self):
        with self.ledger:
            self.ledger.executemany("INSERT INTO calls(run_id,source_day,page,budget_day,state) VALUES(?,?,?,?,'VERIFIED')",
                [('earlier','2026-01-01',p,self.plan['budget_day']) for p in range(1,901)])
        with self.assertRaises(live.Stop):
            live.reserve(self.ledger,self.plan,1)

    def test_existing_lock_preserves_bytes(self):
        lock=Path(self.temp.name)/'existing.lock'
        lock.write_bytes(b'original metadata')
        before=lock.stat().st_mtime_ns
        first=live.existing_lock(lock)
        try:
            with self.assertRaises(live.Stop):
                live.existing_lock(lock)
        finally:
            live.unlock(first)
        self.assertEqual(lock.read_bytes(),b'original metadata')
        self.assertEqual(lock.stat().st_mtime_ns,before)

    def test_kst_midnight_uses_korea_day_not_utc_day(self):
        before=datetime(2026,11,14,14,59,59,tzinfo=timezone.utc)
        after=before+timedelta(seconds=1)
        self.assertEqual(live.quota_window(before)['budget_day'],'2026-11-14')
        self.assertEqual(live.quota_window(before)['quota_reset_at_kst'],'2026-11-15T00:00:00+09:00')
        self.assertEqual(live.quota_window(after)['budget_day'],'2026-11-15')
        self.assertEqual(live.quota_window(after)['quota_reset_at_kst'],'2026-11-16T00:00:00+09:00')
        self.assertFalse(live.quota_window(after)['quota_reset_starts_collection'])

    def test_old_run_stops_at_midnight_without_another_reservation(self):
        plan=self.plan|{'budget_day':'2026-11-14','source_day':'2026-11-13','held_source_day':'2026-11-12'}
        live.validate_plan(plan,datetime(2026,11,14,14,59,59,tzinfo=timezone.utc))
        with patch.object(live,'now',return_value=datetime(2026,11,14,15,0,0,tzinfo=timezone.utc)):
            with self.assertRaisesRegex(live.Stop,'KST_DAY_CHANGED'):
                live.reserve(self.ledger,plan,1)
        self.assertEqual(self.ledger.execute('SELECT COUNT(*) FROM calls').fetchone()[0],0)

    def test_new_day_ignores_previous_usage_without_deleting_history(self):
        with self.ledger:
            self.ledger.executemany("INSERT INTO calls(run_id,source_day,page,budget_day,state) VALUES(?,?,?,?,'VERIFIED')",
                [('previous','2026-11-13',p,'2026-11-14') for p in range(1,901)])
        plan=self.plan|{'budget_day':'2026-11-15','source_day':'2026-11-14','held_source_day':'2026-11-12'}
        with patch.object(live,'now',return_value=datetime(2026,11,15,0,0,tzinfo=live.KST)):
            live.reserve(self.ledger,plan,1)
        self.assertEqual(self.ledger.execute("SELECT COUNT(*) FROM calls WHERE budget_day='2026-11-14'").fetchone()[0],900)
        self.assertEqual(self.ledger.execute("SELECT COUNT(*) FROM calls WHERE budget_day='2026-11-15'").fetchone()[0],1)

    def test_prior_uncertain_scope_held_but_next_day_can_update(self):
        with self.ledger:
            self.ledger.execute("INSERT INTO calls(run_id,source_day,page,budget_day,state) VALUES('previous','2026-11-13',1,'2026-11-14','UNCERTAIN')")
        plan=self.plan|{'budget_day':'2026-11-15','source_day':'2026-11-14','held_source_day':'2026-11-12'}
        with patch.object(live,'now',return_value=datetime(2026,11,15,0,0,tzinfo=live.KST)):
            live.reserve(self.ledger,plan,1)
        self.assertEqual(self.ledger.execute("SELECT COUNT(*) FROM calls WHERE state='UNCERTAIN'").fetchone()[0],1)


if __name__=='__main__':
    unittest.main(verbosity=2)
