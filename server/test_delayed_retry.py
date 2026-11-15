"""Replay real collector paths in disposable DBs with all network calls denied."""
import json
import hashlib
import os
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import patch
import delayed_retry as retry
import live_collection_once as g2b
import test_backfill as backfill_fixtures
FixtureSource = backfill_fixtures.FixtureSource
from restart_recovery import reconcile, unresolved
from receipt_validation import readback


class DelayedRetryTests(unittest.TestCase):
    def setUp(self):
        self.case=backfill_fixtures.BackfillTests();self.case.setUp()
        self.clock=g2b.now().replace(hour=12,minute=0,second=0,microsecond=0)
        self.case.plan['delayed_retry']=retry.POLICY
        self.case.plan['max_pages']=1
    def tearDown(self): self.case.tearDown()
    def run_g2b(self,fixture,run,minutes=0):
        with patch.object(g2b,'now',return_value=self.clock+timedelta(minutes=minutes)):
            return self.case.invoke_worker(fixture,run)
    def test_retry_waits_then_recovers_same_page_without_losing_attempt_history(self):
        first=FixtureSource(total=2,behaviors={1:'timeout'})
        self.run_g2b(first,'failed')
        conn=self.case.ledger
        original=dict(conn.execute('SELECT * FROM calls').fetchone())
        self.assertEqual(len(first.calls),1)
        self.assertIsNone(retry.next_due(conn,self.clock+timedelta(minutes=14,seconds=59)))
        other=FixtureSource(total=2)
        self.run_g2b(other,'during-wait',14)
        self.assertEqual(other.calls[0]['day'],self.case.older)
        resumed=FixtureSource(total=2)
        self.run_g2b(resumed,'after-delay',15)
        self.assertEqual([(c['day'],c['page']) for c in resumed.calls],[(self.case.plan['source_day'],1)])
        saved=readback(self.case.root/f"g2b-real-{self.case.plan['source_day']}.sqlite3",self.case.plan['source_day'])
        self.assertTrue(saved['complete']);self.assertEqual(saved['saved'],2)
        old=dict(conn.execute('SELECT * FROM calls WHERE id=?',(original['id'],)).fetchone())
        self.assertEqual({k:v for k,v in old.items() if k!='state'},{k:v for k,v in original.items() if k!='state'})
        self.assertEqual(old['state'],'RETRY_SUPERSEDED')
        self.assertFalse(unresolved(conn))
        retry.validate_archives(conn)
    def test_repeated_failure_backoff_survives_rearming_and_caps_at_sixty_minutes(self):
        for i,minute in enumerate([0,15,45,105]):
            self.run_g2b(FixtureSource(total=2,behaviors={1:'timeout'}),'fail-'+str(i),minute)
            row=self.case.ledger.execute('SELECT * FROM delayed_retries ORDER BY call_id DESC LIMIT 1').fetchone()
            self.assertEqual(row['failure_count'],i+1)
            self.assertEqual((retry.utc(row['next_retry_at_utc'])-retry.utc(row['failed_at_utc'])).total_seconds(),[900,1800,3600,3600][i])
            due=row['next_retry_at_utc']
            retry.arm_pending(self.case.ledger,self.case.root,clock=self.clock+timedelta(minutes=minute+1))
            self.assertEqual(self.case.ledger.execute('SELECT next_retry_at_utc FROM delayed_retries WHERE call_id=?',(row['call_id'],)).fetchone()[0],due)
        self.assertEqual(self.case.ledger.execute('SELECT SUM(attempted) FROM calls').fetchone()[0],4)
        self.assertEqual(self.case.ledger.execute('SELECT COUNT(*) FROM calls WHERE verified=1').fetchone()[0],0)

    def test_verified_incomplete_checkpoint_resumes_and_preserves_previous_checkpoint(self):
        self.run_g2b(FixtureSource(total=2,behaviors={1:'timeout'}),'incomplete')
        path=self.case.root/f"g2b-real-{self.case.plan['source_day']}.sqlite3"
        with closing(sqlite3.connect(path)) as local:
            with local:
                local.execute("UPDATE collection_checkpoints SET status='INCOMPLETE'")
        source=FixtureSource(total=2)
        self.run_g2b(source,'incomplete-resume',15)
        self.assertEqual([(c['day'],c['page']) for c in source.calls],[(self.case.plan['source_day'],1)])
        self.assertTrue(readback(path,self.case.plan['source_day'])['complete'])
        previous=self.case.ledger.execute('SELECT before_json FROM delayed_checkpoint_restarts').fetchone()[0]
        self.assertEqual(json.loads(previous)['status'],'INCOMPLETE')

    def test_overlapping_retry_preserves_rows_and_continues_another_day_in_same_run(self):
        day=self.case.plan['source_day']
        self.run_g2b(FixtureSource(total=1000),'first-page')
        self.run_g2b(FixtureSource(total=1000,behaviors={(day,2):'overlap'}),'overlap')
        before=readback(self.case.root/f'g2b-real-{day}.sqlite3',day)
        self.assertEqual((before['saved'],before['next_page']),(999,2))
        self.case.plan['max_pages']=3
        source=FixtureSource(total=1000,behaviors={(day,2):'overlap'})
        result=self.run_g2b(source,'overlap-retry',15)
        self.assertEqual([(c['day'],c['page']) for c in source.calls],[(day,2),(self.case.older,1),(self.case.older,2)])
        self.assertEqual(readback(self.case.root/f'g2b-real-{day}.sqlite3',day)['saved'],999)
        self.assertTrue(readback(self.case.root/f'g2b-real-{self.case.older}.sqlite3',self.case.older)['complete'])
        self.assertEqual(result['scope_failures'][0]['reason'],'REPEATED_OR_OVERLAPPING_PAGE')
        self.assertEqual(self.case.ledger.execute("SELECT SUM(verified_rows) FROM calls WHERE run_id='overlap-retry'").fetchone()[0],1000)

    def test_provider_rate_limit_does_not_spill_into_other_dates(self):
        self.case.plan['max_pages']=3
        source=FixtureSource(total=2,behaviors={1:'http_error'})
        self.run_g2b(source,'rate-limited')
        self.assertEqual(len(source.calls),1)
        self.assertEqual(self.case.ledger.execute('SELECT SUM(verified) FROM calls').fetchone()[0],0)
        self.assertEqual(self.case.ledger.execute('SELECT state FROM backfill_days WHERE day=?',(self.case.older,)).fetchone()[0],'READY')
    def test_committed_page_is_reconciled_without_repeating_the_request(self):
        self.run_g2b(FixtureSource(total=2),'committed')
        with self.case.ledger:
            self.case.ledger.execute("UPDATE calls SET state='UNCERTAIN',stored=0,verified=0,verified_rows=0")
        retry.enqueue(self.case.ledger,1,clock=self.clock)
        reconcile(self.case.ledger,self.case.root,held_day=self.case.held)
        retry.arm_pending(self.case.ledger,self.case.root,clock=self.clock+timedelta(minutes=20))
        self.assertIsNone(retry.next_due(self.case.ledger,self.clock+timedelta(minutes=20)))
        self.assertEqual(self.case.ledger.execute('SELECT state FROM calls').fetchone()[0],'VERIFIED')
    def test_retry_still_obeys_current_day_quota_and_preserves_original(self):
        self.run_g2b(FixtureSource(total=2,behaviors={1:'timeout'}),'failed')
        original=self.case.ledger.execute('SELECT id FROM calls').fetchone()[0]
        plan=self.case.plan|{'run_id':'limited','assumed_remaining':900}
        with self.case.ledger:
            self.case.ledger.executemany("INSERT INTO calls(run_id,source_day,page,budget_day,state,attempted) VALUES('filled','2026-01-01',?,?,'VERIFIED',1)",[(n,plan['budget_day']) for n in range(1,900)])
        with patch.object(g2b,'now',return_value=self.clock+timedelta(minutes=16)):
            with self.assertRaisesRegex(g2b.Stop,'BUDGET_EXHAUSTED'):
                g2b.reserve(self.case.ledger,plan,1,self.case.plan['source_day'],retry_of=original)
        self.assertEqual(self.case.ledger.execute('SELECT state FROM calls WHERE id=?',(original,)).fetchone()[0],'UNCERTAIN')
    def test_access_denial_is_not_bypassed_and_midnight_does_not_erase_backoff(self):
        self.run_g2b(FixtureSource(total=2,behaviors={1:'timeout'}),'failed')
        with self.case.ledger:self.case.ledger.execute('DELETE FROM delayed_retries')
        retry.enqueue(self.case.ledger,1,reason='HTTP_403',clock=self.clock)
        self.assertIsNone(retry.next_due(self.case.ledger,self.clock+timedelta(days=2)))
        with self.case.ledger:self.case.ledger.execute('DELETE FROM delayed_retries')
        late=self.clock.replace(hour=23,minute=55)
        retry.enqueue(self.case.ledger,1,clock=late)
        self.assertIsNone(retry.next_due(self.case.ledger,late+timedelta(minutes=5)))
        self.assertIsNotNone(retry.next_due(self.case.ledger,late+timedelta(minutes=15)))
    def test_empty_success_missing_items_is_zero_but_other_missing_items_remains_error(self):
        import vnext_http
        with patch.object(vnext_http,'_record_result'):
            for raw in [json.dumps({'response':{'header':{'resultCode':'00'},'body':{'totalCount':0,'pageNo':1,'numOfRows':999}}}),
                        '<response><header><resultCode>00</resultCode></header><body><totalCount>0</totalCount><pageNo>1</pageNo><numOfRows>999</numOfRows></body></response>']:
                self.assertEqual(vnext_http.parse_response(raw),([],0))
            for body in [{'totalCount':1},{}, {'totalCount':False}]:
                with self.assertRaises(vnext_http.VNextResponseError):
                    vnext_http.parse_response(json.dumps({'response':{'header':{'resultCode':'00'},'body':body}}))
    def test_historical_thirteen_pages_are_preserved_and_only_page_fourteen_retries(self):
        import historical_resume, backfill_queue
        case=backfill_fixtures.BackfillTests()
        with patch.object(g2b,'now',return_value=datetime(2026,11,13,12,tzinfo=g2b.KST)):
            case.setUp()
        try:
            fixture=FixtureSource(total=13093,behaviors={14:'timeout'})
            with patch.object(g2b,'now',return_value=datetime(2026,11,13,12,tzinfo=g2b.KST)):
                case.plan['max_pages']=8;case.invoke_worker(fixture,'trial-first')
                case.plan['max_pages']=6;case.invoke_worker(fixture,'trial-timeout')
            source=case.root/'g2b-real-2026-11-12.sqlite3'
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            hold={'status':'HOLD','source_day':'2026-11-12','next_page':14,'verified_saved':12987,'api_calls_consumed':14}
            case.hold.write_text(json.dumps(hold),encoding='utf-8')
            recorded=datetime(2026,11,13,12,tzinfo=g2b.KST).timestamp()
            os.utime(case.hold,(recorded,recorded))
            old_hold=case.hold.read_bytes()
            original_root=case.root;case.root=case.root/'live';case.root.mkdir()
            case.ledger.close();case.ledger=g2b.ledger_open(case.root/'live-calls.sqlite3')
            clock=datetime(2026,11,15,12,tzinfo=g2b.KST)
            adopted=historical_resume.adopt(case.ledger,case.root,source,case.hold,clock=clock)
            historical_resume.adopt(case.ledger,case.root,source,case.hold,clock=clock)
            self.assertEqual(case.ledger.execute('SELECT COUNT(*) FROM calls').fetchone()[0],1)
            case.plan.update(source_day='2026-11-14',range_end='2026-11-14',held_source_day='2026-11-12',
                budget_day='2026-11-15',max_pages=1,delayed_retry=retry.POLICY,historical_resume=adopted,
                hold_sha256=hashlib.sha256(old_hold).hexdigest())
            backfill_queue.save_inventory(case.ledger,[{'day':'2026-11-12','state':'HOLD',
                'db_path':str(case.root/'g2b-real-2026-11-12.sqlite3'),'next_page':14,'saved':12987,'reason':'HISTORICAL'}])
            resumed=FixtureSource(total=13093)
            with patch.object(g2b,'now',return_value=clock):case.invoke_worker(resumed,'resumed')
            self.assertEqual([(c['day'],c['page']) for c in resumed.calls],[('2026-11-12',14)])
            proof=readback(case.root/'g2b-real-2026-11-12.sqlite3','2026-11-12')
            self.assertTrue(proof['complete']);self.assertEqual(proof['saved'],13093)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
            self.assertEqual(case.hold.read_bytes(),old_hold)
        finally:case.tearDown()
if __name__=='__main__':unittest.main()
