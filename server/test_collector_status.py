from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import collector_status as c


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.now=datetime(2026,11,15,8,tzinfo=timezone.utc)
        for source,(folder,ledger,status_file) in c.SOURCES.items():
            p=self.root/folder;p.mkdir();run=p/'scheduled-20261115T1700';run.mkdir()
            (p/status_file).write_text(json.dumps({'run_id':run.name,'state':'RUNNING','observed_at_kst':'2026-11-15T17:00:00+09:00','api_key':'SECRET'}))
            (run/'plan.json').write_text(json.dumps({'budget_day':'2026-11-15','effective_day_budget':950,'configured_daily_ceiling':950,'daily_limit':100,'secret':'SECRET'}))
            with closing(sqlite3.connect(p/ledger)) as db, db:
                db.executescript('CREATE TABLE calls(run_id TEXT,budget_day TEXT,state TEXT,attempted INTEGER,response_confirmed INTEGER,stored INTEGER,verified INTEGER,verified_rows INTEGER,service TEXT,snapshot TEXT,page INTEGER);CREATE TABLE backfill_days(day TEXT,state TEXT,next_page INTEGER,saved INTEGER,reason TEXT);CREATE TABLE jobs(service TEXT,snapshot TEXT,next_page INTEGER,saved INTEGER);')
                for day,state,attempted in [('2026-11-15','VERIFIED',1),('2026-11-15','UNCERTAIN',1),('2026-11-15','RESERVED',0),('2026-11-15','NOT_SENT',0),('2026-11-14','VERIFIED',1)]:
                    v=state=='VERIFIED'
                    db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?)',(run.name,day,state,attempted,int(v),int(v),int(v),9 if v else 0,'QWGJK','2026-11-14',1))
                db.execute("INSERT INTO backfill_days VALUES('2026-11-12','HOLD',14,12987,'ORIGINAL_UNCERTAIN_PAGE_HOLD')")
    def snapshot(self,**kwargs):
        with patch('socket.socket',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            return c.collect_status(self.root,self.now,task_reader=kwargs.get('task_reader',lambda:{}))
    def test_read_only_counts_include_failed_and_pending_but_exclude_not_sent(self):
        before={p:p.read_bytes() for p in self.root.rglob('*.sqlite3')}
        p=self.snapshot()
        for s in p['sources']:
            self.assertEqual((s['counted'],s['attempted'],s['pending'],s['verified']),(3,2,1,1))
            self.assertIsNone(s['official_remaining'])
            self.assertEqual(s['last_run']['reason'],'STATUS_UNAVAILABLE')
        self.assertNotIn('SECRET',json.dumps(p));self.assertNotIn(str(self.root),json.dumps(p))
        self.assertEqual(before,{p:p.read_bytes() for p in before})
    def test_running_requires_scheduler_observation(self):
        task={'state':'Running','enabled':True,'last_result':267009,'last_run_at':None,'next_run_at':None}
        self.assertEqual(self.snapshot(task_reader=lambda:{'g2b':task})['sources'][0]['last_run']['reason'],'RUN_RESULT_PENDING')
        task['state']='Ready'
        self.assertEqual(self.snapshot(task_reader=lambda:{'g2b':task})['sources'][0]['last_run']['reason'],'RUN_ENDED_WITHOUT_RESULT')
    def test_midnight_queries_new_day_and_reset_is_kst(self):
        self.now=datetime(2026,11,15,15,0,0,tzinfo=timezone.utc);p=self.snapshot()
        self.assertEqual(p['day'],'2026-11-16');self.assertEqual(p['reset_at'],'2026-11-16T15:00:00.000Z')
        self.assertEqual(p['sources'][0]['attempted'],0);self.assertEqual(p['sources'][0]['limit'],950)
    def test_missing_ledger_is_unknown_not_zero(self):
        (self.root/'lofin-collection/lofin-calls.sqlite3').unlink()
        s=self.snapshot()['sources'][1];self.assertFalse(s['available']);self.assertIsNone(s['counted'])
        self.assertEqual(self.snapshot()['sources'][0]['attempted'],2)
    def test_timeout_reason_does_not_imply_quota_exhaustion(self):
        path=self.root/'lofin-collection/scheduled-20261115T1700/result.json'
        path.write_text(json.dumps({'supervisor_outcome':'TIMEOUT_NO_RETRY','finished_at_kst':'2026-11-15T17:01:25+09:00','key':'SECRET'}))
        s=self.snapshot()['sources'][1];self.assertEqual(s['last_run']['reason'],'TIMEOUT_NO_RETRY');self.assertEqual(s['limit']-s['counted'],97)
    def test_response_error_uses_safe_worker_classification(self):
        folder=self.root/'live-bounded-20261114/scheduled-20261115T1700'
        (folder/'result.json').write_text(json.dumps({'stop_reason':'VNextResponseError'}))
        self.assertEqual(self.snapshot()['sources'][0]['last_run']['reason'],'SOURCE_RESPONSE_ERROR')
        (folder/'worker-result.json').write_text(json.dumps({'safe_error_code':'SCHEMA'}))
        self.assertEqual(self.snapshot()['sources'][0]['last_run']['reason'],'SOURCE_SCHEMA_ERROR')


if __name__=='__main__':unittest.main()
