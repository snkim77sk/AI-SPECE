import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import manual_schema_recheck as manual
import live_collection_once as live
import restart_recovery as recovery
import vnext_http as http
from response_evidence import summarize


def deny_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo'): raise AssertionError('REAL_NETWORK_FORBIDDEN')
sys.addaudithook(deny_network)


def envelope(body,code='00'):
    return json.dumps({'response':{'header':{'resultCode':code},'body':body}}).encode()


class SchemaTests(unittest.TestCase):
    def test_success_explicit_zero_with_items(self):
        with patch.object(http,'_record_result'):
            for total in (0,'0'):
                self.assertEqual(http.parse_response(envelope({'items':[],'totalCount':total,'numOfRows':999,'pageNo':1})),([],0))
    def test_missing_items_never_hides_data_or_unknown_count(self):
        with patch.object(http,'_record_result'):
            for body in ({},{'totalCount':0},{'totalCount':3},{'totalCount':None},{'totalCount':False},{'totalCount':'bad'},
                         {'totalCount':0,'rows':[{'id':1}]},{'totalCount':0,'item':[{'id':1}]}):
                with self.subTest(body=body),self.assertRaises(http.VNextResponseError): http.parse_response(envelope(body))
    def test_error_or_missing_success_envelope_is_not_empty(self):
        with patch.object(http,'_record_result'):
            for raw in (envelope({'totalCount':0},'22'),b'{"totalCount":0}',b'<html>down</html>',b''):
                with self.assertRaises(http.VNextApiError): http.parse_response(raw)
    def test_evidence_never_contains_source_text_credentials(self):
        raw=b'{"response":{"header":{"resultCode":"00","resultMsg":"TEST-SECRET"},"body":{"totalCount":0,"serviceKey":"TEST-SECRET"}}}'
        result=summarize(raw,200,'application/json')
        self.assertNotIn('TEST-SECRET',json.dumps(result))
        self.assertNotIn('reproducible_envelope',result)
    def test_zero_page_commits_real_collector_receipt(self):
        import db,shopping_vnext as shop,urllib.request
        from receipt_validation import readback
        from vnext_source_guard import manual_backfill_source_context
        with tempfile.TemporaryDirectory() as td,patch.object(db,'DB_PATH',str(Path(td)/'test.sqlite3')), \
             patch.dict(os.environ,{'G2B_SERVICE_KEY':'OFFLINE-TEST','G2B_RUNTIME_ROLE':'LOCAL_COLLECTOR'}), \
             patch.object(urllib.request,'urlopen',return_value=io.BytesIO(envelope({'items':[],'totalCount':0,'pageNo':1,'numOfRows':999}))):
            db.init_db()
            with manual_backfill_source_context(collection_date=manual.DAY,range_start='2026-01-01',range_end=manual.DAY,held_dates=['2026-11-12'],max_requests=1):
                result=shop.collect_all(manual.DAY,manual.DAY,max_pages=1)
            proof=readback(Path(db.DB_PATH),manual.DAY,expected_page=1)
            self.assertEqual(result['status'],'COMPLETE');self.assertTrue(proof['verified']);self.assertEqual(proof['saved'],0)


class ManualLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.conn=live.ledger_open(Path(self.temp.name)/'calls.sqlite3')
        self.conn.execute("INSERT INTO calls VALUES(65,'old','2026-11-14',1,'2026-11-15','UNCERTAIN',1,1,0,0,0,0)");self.conn.commit()
    def tearDown(self):self.conn.close();self.temp.cleanup()
    def test_one_manual_recheck_preserves_old_attempt_and_counts_twice(self):
        call=manual.reserve_recheck(self.conn,'test',65,'2026-11-15')
        self.assertTrue(recovery.unresolved(self.conn))
        for event in ('attempted','response_received','response_confirmed','stored'):live.phase(self.conn,call,event)
        manual.finish_recheck(self.conn,call,{'verified':True,'next_page':2,'page_rows':0})
        self.assertFalse(recovery.unresolved(self.conn))
        self.assertEqual(tuple(self.conn.execute('SELECT attempted,response_received,response_confirmed,stored,verified FROM calls WHERE id=65').fetchone()),(1,1,0,0,0))
        self.assertEqual(self.conn.execute('SELECT SUM(attempted) FROM calls').fetchone()[0],2)
        with self.assertRaises(ValueError):manual.reserve_recheck(self.conn,'again',65,'2026-11-15')
    def test_fake_supersession_never_releases_hold(self):
        self.conn.execute("UPDATE calls SET state='RECHECK_SUPERSEDED'");self.conn.commit()
        with self.assertRaises(ValueError):recovery.unresolved(self.conn)
    def test_different_page_or_historical_hold_not_authorized(self):
        self.conn.execute("UPDATE calls SET source_day='2026-11-12',page=14");self.conn.commit()
        with self.assertRaises(ValueError):manual.reserve_recheck(self.conn,'test',65,'2026-11-15')


if __name__=='__main__':unittest.main()
