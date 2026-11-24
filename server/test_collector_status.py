from datetime import datetime, timezone
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import collector_status as c
import delayed_retry as retry
from restart_recovery import ensure_schema as ensure_ledger_schema


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
                db.executescript('CREATE TABLE calls(run_id TEXT,budget_day TEXT,state TEXT,attempted INTEGER,response_confirmed INTEGER,stored INTEGER,verified INTEGER,verified_rows INTEGER,service TEXT,snapshot TEXT,page INTEGER);CREATE TABLE backfill_days(day TEXT,state TEXT,next_page INTEGER,saved INTEGER,reason TEXT);CREATE TABLE jobs(service TEXT,snapshot TEXT,state TEXT,next_page INTEGER,saved INTEGER);')
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
    def test_verified_late_recheck_is_counted_with_delivery_only(self):
        root=self.root/c.SOURCES['g2b'][0]/'recent-empty-rechecks'
        root.mkdir()
        (root/'2026-11-14--2026-11-15--01-00001.json').write_text(json.dumps({
            'version':1,'budget_day':'2026-11-15','run_id':'scheduled-20261115T1700',
            'attempted':True,'response_confirmed':True,'outcome':'VERIFIED','receipt':{'page_rows':999}}))
        g2b,budget=self.snapshot()['sources']
        self.assertEqual((g2b['counted'],g2b['attempted'],g2b['verified'],g2b['verified_rows']),(4,3,2,1008))
        self.assertEqual((budget['counted'],budget['verified_rows']),(3,9))
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
    def test_quota_wait_keeps_last_configured_limit_without_claiming_a_new_run(self):
        folder=self.root/'lofin-collection'
        with closing(sqlite3.connect(folder/'lofin-calls.sqlite3')) as db,db:
            db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY)')
            db.execute("INSERT INTO runs VALUES('scheduled-20261115T1700')")
        path=folder/'scheduled-20261115T1700/plan.json'
        plan=json.loads(path.read_text());plan['delayed_retry']={'authorization':'USER_20261115_DELAYED_RESTART'}
        path.write_text(json.dumps(plan))
        (folder/'lofin-status.json').write_text(json.dumps({'state':'WAIT','safe_reason':'LOCAL_QUOTA_WAIT'}))
        result=self.snapshot()['sources'][1]
        self.assertEqual(result['limit'],100);self.assertTrue(result['retry_enabled'])
        self.assertIsNone(result['last_run']['run_id'])
        self.assertEqual(result['last_run']['reason'],'LOCAL_QUOTA_WAIT')
    def test_response_error_uses_safe_worker_classification(self):
        folder=self.root/'live-bounded-20261114/scheduled-20261115T1700'
        (folder/'result.json').write_text(json.dumps({'stop_reason':'VNextResponseError'}))
        self.assertEqual(self.snapshot()['sources'][0]['last_run']['reason'],'SOURCE_RESPONSE_ERROR')
        (folder/'worker-result.json').write_text(json.dumps({'safe_error_code':'SCHEMA'}))
        self.assertEqual(self.snapshot()['sources'][0]['last_run']['reason'],'SOURCE_SCHEMA_ERROR')
        self.assertEqual(c.code('NETWORK'),'SOURCE_NETWORK_OR_TIMEOUT')
        self.assertEqual(c.code('SOURCE_PAGE_REJECTED'),'SOURCE_RESPONSE_ERROR')
        self.assertEqual(c.code('REPEATED_OR_OVERLAPPING_PAGE'),'REPEATED_OR_OVERLAPPING_PAGE')
        self.assertEqual(c.code('SECRET_PROVIDER_BODY'),'STATUS_UNAVAILABLE')


class OverlapStatusTests(unittest.TestCase):
    """Real retry-chain evidence, read-only telemetry, no Windows or source calls."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.folder=self.root/c.SOURCES['g2b'][0];self.folder.mkdir()
        self.now=datetime(2026,11,15,8,tzinfo=timezone.utc)
        self.ledger=self.folder/c.SOURCES['g2b'][1]
        with closing(sqlite3.connect(self.ledger)) as db:
            db.row_factory=sqlite3.Row
            ensure_ledger_schema(db)
            with db:
                db.execute('CREATE TABLE backfill_days(day TEXT PRIMARY KEY,state TEXT,db_path TEXT,next_page INTEGER,saved INTEGER,reason TEXT)')
        run=self.folder/'current';run.mkdir()
        (self.folder/c.SOURCES['g2b'][2]).write_text(json.dumps({'run_id':run.name,'observed_at_kst':'2026-11-15T17:00:00+09:00'}))
        (run/'plan.json').write_text(json.dumps({'budget_day':'2026-11-15','effective_day_budget':950,'delayed_retry':retry.POLICY}))
        (run/'result.json').write_text(json.dumps({'stop_reason':'NO_READY_DAYS'}))

    def add_chain(self,day,attempts=4,reason=retry.OVERLAP_REASON,*,held_page=2):
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.row_factory=sqlite3.Row
            first=db.execute('SELECT COALESCE(MAX(id),0)+1 FROM calls').fetchone()[0]
            for offset in range(attempts):
                ident=first+offset;run_id='retry-'+day+'-'+str(offset)
                original=dict(id=ident,run_id=run_id,source_day=day,page=2,budget_day='2026-11-15',
                              state='UNCERTAIN',attempted=1,response_received=1,response_confirmed=1,
                              stored=0,verified=0,verified_rows=0)
                values=original|{'state':'RETRY_SUPERSEDED' if offset<attempts-1 else 'UNCERTAIN'}
                db.execute('INSERT INTO calls('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
                db.execute('INSERT INTO delayed_retries VALUES(?,?,?,?,?,?,?,?,?)',
                    (ident,offset+1,'2026-11-15T00:00:00+00:00','2026-11-15T01:00:00+00:00',reason,
                     'REPLACED' if offset<attempts-1 else 'WAITING',ident+1 if offset<attempts-1 else None,
                     retry.AUTHORIZATION,json.dumps(original,sort_keys=True)))
                run=self.folder/run_id;run.mkdir()
                evidence=run/f'response-{ident}.json'
                evidence.write_text(json.dumps({'http_status':200,'pageNo':2,'body_sha256':'a'*64}))
            daily=self.folder/f'g2b-real-{day}.sqlite3'
            with closing(sqlite3.connect(daily)) as source, source:
                source.executescript('CREATE TABLE raw_records(source_key TEXT,payload_json TEXT);CREATE TABLE collection_checkpoints(dataset TEXT,scope_key TEXT,page_no INTEGER,saved_count INTEGER,status TEXT);')
                source.execute('INSERT INTO raw_records VALUES(?,?)',('kept-row','{"original":"preserved"}'))
                source.execute('INSERT INTO collection_checkpoints VALUES(?,?,?,?,?)',('shopping_delivery',day+':'+day,held_page,999,'INCOMPLETE'))
            db.execute('INSERT INTO backfill_days VALUES(?,?,?,?,?,?)',(day,'HOLD',str(daily),held_page,999,reason))
        return ident,evidence

    def snapshot(self):
        before={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('socket.socket',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            result=c.collect_status(self.root,self.now,task_reader=lambda:{})
        after={p.relative_to(self.root):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(after,before,'Telemetry must not change files, RAW, checkpoints, ledger or response evidence')
        self.assertEqual(result['protocol'],'windows-collector-status-v1')
        self.assertEqual(result['version'],'5.2.9')
        self.assertNotIn(str(self.root),json.dumps(result))
        return result['sources'][0]

    def test_capped_overlap_is_output_only_while_ordinary_retry_and_ready_day_remain_distinct(self):
        capped,_=self.add_chain('2026-11-14')
        ordinary,_=self.add_chain('2026-11-13',attempts=3)
        self.add_chain('2026-11-12',reason='TIMEOUT_NO_RETRY')
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.row_factory=sqlite3.Row
            db.execute("INSERT INTO backfill_days VALUES('2026-11-11','READY','unused',1,0,'')")
            self.assertTrue(retry.overlap_review_required(db,capped))
            self.assertEqual(retry.next_due(db,self.now)['id'],ordinary)
        result=self.snapshot()
        held={row['scope']:row for row in result['held']}
        self.assertEqual(held['2026-11-14'],{'scope':'2026-11-14','next_page':2,'saved':999,'reason':'SOURCE_OVERLAP_REVIEW_REQUIRED'})
        self.assertEqual(held['2026-11-13']['reason'],retry.OVERLAP_REASON)
        self.assertEqual(held['2026-11-12']['reason'],'TIMEOUT_NO_RETRY')
        self.assertEqual({row['scope'] for row in result['retries']},{'2026-11-13','2026-11-12'})
        self.assertEqual(result['progress'],[{'scope':'2026-11-11','next_page':1,'saved':0}])
        self.assertEqual(result['last_run']['reason'],'NO_READY_SCOPES')
        self.assertEqual((result['counted'],result['attempted'],result['pending']),(11,11,0))
        with closing(sqlite3.connect(self.ledger)) as db:
            db.row_factory=sqlite3.Row
            self.assertEqual(retry.next_due(db,self.now)['id'],ordinary)
            self.assertEqual(db.execute("SELECT reason FROM backfill_days WHERE day='2026-11-14'").fetchone()[0],retry.OVERLAP_REASON)
        self.assertEqual(c.code('SOURCE_OVERLAP_REVIEW_REQUIRED'),'SOURCE_OVERLAP_REVIEW_REQUIRED')

    def test_four_attempts_without_identical_response_evidence_remain_an_ordinary_hold(self):
        _,evidence=self.add_chain('2026-11-14')
        evidence.write_text(json.dumps({'http_status':200,'pageNo':2,'body_sha256':'b'*64}))
        result=self.snapshot()
        self.assertEqual(result['held'][0]['reason'],retry.OVERLAP_REASON)
        self.assertEqual(result['retries'][0]['state'],'WAITING')
        self.assertEqual(result['retries'][0]['failures'],4)

    def test_review_classification_requires_the_same_held_page(self):
        self.add_chain('2026-11-14',held_page=3)
        result=self.snapshot()
        self.assertEqual(result['held'][0]['next_page'],3)
        self.assertEqual(result['held'][0]['reason'],retry.OVERLAP_REASON)
        self.assertEqual(result['retries'],[])

    def test_completed_no_ready_run_does_not_gain_a_review_hold(self):
        result=self.snapshot()
        self.assertEqual(result['held'],[])
        self.assertEqual(result['retries'],[])
        self.assertEqual(result['last_run']['reason'],'NO_READY_SCOPES')
        self.assertTrue(result['available'])


if __name__=='__main__':unittest.main()
