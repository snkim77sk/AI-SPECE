"""Disposable budget fixtures, with every real network connection denied."""
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
import unittest
import urllib.parse
import urllib.request
import urllib.error
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
import lofin_collection as live
import db
from vnext_source_guard import lofin_bounded_source_context,require_source_request_context,VNextSourceAccessError


def deny_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise AssertionError('REAL_NETWORK_FORBIDDEN')
sys.addaudithook(deny_network)


class Response:
    def __init__(self,payload): self.body=io.BytesIO(json.dumps(payload).encode())
    def read(self,n): return self.body.read(n)
    def __enter__(self): return self
    def __exit__(self,*args): pass


class Fixture:
    def __init__(self,total=1000,error=False): self.total=total;self.error=error;self.calls=[]
    def open(self,request,timeout):
        url=urllib.parse.urlsplit(request.full_url)
        p={k:v[0] for k,v in urllib.parse.parse_qs(url.query,keep_blank_values=True).items()}
        service=url.path.split('/')[-1];page=int(p['pIndex']);size=int(p['pSize'])
        self.calls.append((service,page))
        if self.error=='redirect':
            raise urllib.error.HTTPError('https://www.lofin365.go.kr/lf/hub/',302,'SOURCE_REDIRECT_REFUSED',{},None)
        if self.error: raise TimeoutError('synthetic-timeout')
        rows=[]
        for n in range((page-1)*size,min(page*size,self.total)):
            row={'fyr':'2026','wa_laf_cd':'1100000','wa_laf_hg_nm':'시험시','laf_cd':'1111000','laf_hg_nm':'시험구',
                 'fld_cd':'010','fld_nm':'일반공공행정','sect_cd':str(n),'sect_nm':'시험부문','acnt_dv_cd':'1','acnt_dv_nm':'일반회계'}
            if service=='QWGJK':
                row.update(exe_ymd=p['exe_ymd'],dept_cd='10',dept_nm='시험부서',dbiz_cd=str(n),dbiz_nm='시험사업 '+str(n),
                           bdg_cash_amt='1000000',ep_amt='250000')
            else: row.update(biz_bdg_tott_amt='1000000',padm_oper_exps_tott_amt='10000',fin_acv_tott_amt='5000',padm_oper_prsm_exps='9000')
            rows.append(row)
        return Response({service:[{'head':[{'list_total_count':self.total},{'RESULT':{'CODE':'INFO-000'}}]},{'row':rows}]})


class LofinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lofin-test-');self.root=Path(self.temp.name)
        self.day=(live.now().date()-timedelta(days=1)).isoformat()
        self.conn=live.ledger_open(self.root)
        with self.conn:
            self.conn.execute("INSERT INTO jobs(service,snapshot) VALUES('AIDFA','2026')")
            self.conn.execute("INSERT INTO jobs(service,snapshot) VALUES('QWGJK',?)",(self.day,))
    def tearDown(self):
        self.conn.close();self.temp.cleanup()
    def invoke(self,fixture,run_id='first',max_pages=3,delayed=False):
        run=self.root/run_id;run.mkdir()
        plan={'run_id':run_id,'budget_day':live.now().date().isoformat(),'max_pages':max_pages,'started_monotonic':time.monotonic()}
        if delayed:
            from delayed_retry import POLICY
            plan['delayed_retry']=POLICY
        live.atomic_json(run/'plan.json',plan)
        with patch.object(live,'ready',return_value=(plan['budget_day'],'')),patch.object(live,'load_key',return_value='OFFLINE'), \
             patch.object(sys,'stdin',types.SimpleNamespace(buffer=io.BytesIO(b'GO\n'))),patch.object(urllib.request,'build_opener',return_value=fixture), \
             patch.dict(os.environ,{},clear=False),patch.object(db,'DB_PATH',db.DB_PATH):
            live.worker(run)
        return json.loads((run/'worker-result.json').read_text())
    def test_both_existing_collectors_save_then_resume_without_duplicate(self):
        fixture=Fixture()
        self.assertEqual(self.invoke(fixture)['stop_reason'],'PAGE_LIMIT')
        self.assertEqual(fixture.calls,[('AIDFA',1),('QWGJK',1),('AIDFA',2)])
        self.invoke(fixture,'second')
        self.assertEqual(fixture.calls[-1],('QWGJK',2))
        self.assertEqual(len(fixture.calls),4)
        self.assertEqual(self.conn.execute('SELECT SUM(verified_rows) FROM calls').fetchone()[0],2000)
        for service,snapshot in [('AIDFA','2026'),('QWGJK',self.day)]:
            self.assertTrue(live.readback(self.root/'budget-2026.sqlite3',service,snapshot)['complete'])
    def test_timeout_stops_after_one_attempt_and_blocks_further_calls(self):
        fixture=Fixture(error=True)
        self.invoke(fixture)
        self.assertEqual(len(fixture.calls),1)
        self.assertEqual(self.conn.execute('SELECT attempted,state FROM calls').fetchone()[:],(1,'UNCERTAIN'))
        self.assertTrue(live.unresolved(self.conn))
        normal=Fixture(total=2)
        self.invoke(normal,'other-service')
        self.assertEqual(normal.calls,[('QWGJK',1)])
        self.assertTrue(live.unresolved(self.conn,'AIDFA','2026'))
        self.assertFalse(live.unresolved(self.conn,'QWGJK',self.day))

    def test_proven_unsent_deadline_reservation_restores_same_checkpoint(self):
        self.invoke(Fixture(total=1000),max_pages=1)
        path=self.root/'budget-2026.sqlite3'
        with closing(sqlite3.connect(path)) as local:
            before=local.execute('SELECT COUNT(*) FROM raw_records').fetchone()[0]
            with local: local.execute("UPDATE collection_checkpoints SET status='FAILED',last_error='Stop' WHERE dataset='budget_appropriation'")
        with self.conn:
            self.conn.execute("INSERT INTO calls(run_id,service,snapshot,page,budget_day,state) VALUES('late','AIDFA','2026',2,?,'UNCERTAIN')",(live.now().date().isoformat(),))
            self.conn.execute("UPDATE jobs SET state='HOLD' WHERE service='AIDFA'")
        events=live.reconcile(self.conn,path)
        self.assertEqual(events[-1]['outcome'],'NOT_SENT')
        receipt=live.readback(path,'AIDFA','2026')
        self.assertEqual((receipt['status'],receipt['next_page'],receipt['saved']),('RUNNING',2,999))
        with closing(sqlite3.connect(path)) as local:self.assertEqual(local.execute('SELECT COUNT(*) FROM raw_records').fetchone()[0],before)
        self.assertFalse(live.unresolved(self.conn))
        self.assertEqual(self.conn.execute("SELECT state FROM jobs WHERE service='AIDFA'").fetchone()[0],'READY')
    def test_maintenance_and_midnight_are_no_call_boundaries(self):
        with self.assertRaisesRegex(live.Stop,'OFFICIAL_MAINTENANCE_WAIT'):
            live.ready(datetime(2026,11,14,23,59,tzinfo=live.KST))
        self.assertEqual(live.ready(datetime(2026,11,15,0,0,tzinfo=live.KST))[0],'2026-11-15')
        with self.assertRaisesRegex(live.Stop,'MIDNIGHT_BOUNDARY_WAIT'):
            live.ready(datetime(2026,11,15,23,59,tzinfo=live.KST))
    def test_redirect_is_received_but_never_followed_or_retried(self):
        fixture=Fixture(error='redirect')
        self.assertEqual(self.invoke(fixture)['stop_reason'],'HTTP_302')
        self.assertEqual(len(fixture.calls),1)
        self.assertEqual(self.conn.execute('SELECT attempted,response_received,response_confirmed,state FROM calls').fetchone()[:],(1,1,0,'UNCERTAIN'))
    def test_service_page_size_and_filter_are_bound(self):
        p={'Key':'OFFLINE','Type':'json','pIndex':1,'pSize':999,'fyr':2026}
        with lofin_bounded_source_context(service='AIDFA',snapshot='2026',page=1):
            for altered in (p|{'pIndex':2},p|{'pSize':1000},p|{'fyr':2025},p|{'dbiz_nm':'filter'}):
                with self.assertRaises(VNextSourceAccessError):
                    require_source_request_context(lofin_params=altered,lofin_service='AIDFA')
            require_source_request_context(lofin_params=p,lofin_service='AIDFA')
            with self.assertRaises(VNextSourceAccessError):
                require_source_request_context(lofin_params=p,lofin_service='AIDFA')
    def test_daily_cap_counts_both_services_and_keeps_previous_days(self):
        with self.conn:
            self.conn.executemany("INSERT INTO calls(run_id,service,snapshot,page,budget_day,state) VALUES('old','AIDFA','2026',?,'2026-11-15','VERIFIED')",[(n,) for n in range(1,101)])
        plan={'run_id':'new','budget_day':'2026-11-15','max_pages':8}
        with patch.object(live,'ready',return_value=('2026-11-15','')):
            with self.assertRaisesRegex(live.Stop,'LOCAL_QUOTA_WAIT'): live.reserve(self.conn,plan,'QWGJK',self.day,1)
        with patch.object(live,'ready',return_value=('2026-11-16','')):
            live.reserve(self.conn,plan|{'budget_day':'2026-11-16'},'QWGJK',self.day,1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM calls').fetchone()[0],101)
    def test_recovery_from_committed_page_does_not_call_source(self):
        fixture=Fixture()
        self.invoke(fixture,max_pages=1)
        with self.conn:
            self.conn.execute("UPDATE calls SET state='RESERVED',stored=0,verified=0,verified_rows=0")
            self.conn.execute("UPDATE jobs SET next_page=1,saved=0")
        path=self.root/'budget-2026.sqlite3'
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        statements=[]
        connect=sqlite3.connect
        def traced(*args,**kwargs):
            connection=connect(*args,**kwargs)
            connection.set_trace_callback(statements.append)
            return connection
        with patch.object(sqlite3,'connect',side_effect=traced):
            events=live.reconcile(self.conn,path)
        self.assertEqual(events[0]['outcome'],'VERIFIED_FROM_COMMIT')
        self.assertFalse(any('quick_check' in sql.lower() for sql in statements))
        self.assertTrue(any('vnext_collection_items' in sql for sql in statements))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
        self.assertFalse(live.unresolved(self.conn))
    def test_readback_detects_corrupt_payload(self):
        self.invoke(Fixture(),max_pages=1)
        conn=sqlite3.connect(self.root/'budget-2026.sqlite3')
        with conn: conn.execute("UPDATE raw_records SET payload_json='{}' WHERE id=1")
        conn.close()
        with self.assertRaisesRegex(ValueError,'RAW_HASH_MISMATCH'):
            live.readback(self.root/'budget-2026.sqlite3','AIDFA','2026')

    def test_final_readback_checks_both_verified_tails_without_rescanning_prefixes(self):
        self.invoke(Fixture(total=2),max_pages=2)
        path=self.root/'budget-2026.sqlite3'
        tails=live.verified_run_tails(self.root/'first')
        self.assertEqual(set(tails),{('AIDFA','2026'),('QWGJK',self.day)})
        with patch('receipt_validation.verify_receipt',side_effect=AssertionError('prefix rescanned')):
            for (service,snapshot),tail in tails.items():
                final=live.readback(path,service,snapshot,expected_page=1,verified_tail=tail)
                self.assertEqual(final['response_hash'],tail['response_hash'])
                with self.assertRaisesRegex(ValueError,'LOFIN_FINAL_READBACK_MISMATCH'):
                    live.readback(path,service,snapshot,expected_page=1,verified_tail=tail|{'response_hash':'wrong'})

    def test_queue_includes_january_first_and_latest_first(self):
        # Keep this fixed-date inventory independent of setUp's current-day job.
        root=self.root/'inventory-only';root.mkdir()
        with closing(live.ledger_open(root)) as conn:
            live.sync_jobs(conn,datetime(2026,11,15,0,1,tzinfo=live.KST))
            self.assertEqual(conn.execute("SELECT MIN(snapshot),MAX(snapshot),COUNT(*) FROM jobs WHERE service='QWGJK'").fetchone()[:],('2026-01-01','2026-11-14',318))
    def test_no_key_is_saved_in_source_database(self):
        self.invoke(Fixture(),max_pages=1)
        conn=sqlite3.connect(self.root/'budget-2026.sqlite3')
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM vnext_source_credentials WHERE TRIM(value)<>''").fetchone()[0],0)
        conn.close()
        self.assertNotIn(b'OFFLINE',(self.root/'budget-2026.sqlite3').read_bytes())

    def test_midnight_adds_latest_day_without_abandoning_started_snapshot(self):
        self.invoke(Fixture(total=1000),max_pages=3)
        with self.conn:
            self.conn.execute("INSERT INTO jobs(service,snapshot) VALUES('QWGJK','2026-12-01')")
        self.assertEqual(live.next_job(self.conn)['snapshot'],self.day)
        with self.conn:
            self.conn.execute("UPDATE jobs SET state='COMPLETE' WHERE service='QWGJK' AND snapshot=?",(self.day,))
        self.assertEqual(live.next_job(self.conn)['snapshot'],'2026-12-01')


if __name__=='__main__': unittest.main(verbosity=2)
