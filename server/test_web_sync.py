import hashlib
import io
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import web_sync_client as client


def fixture(base, day='2026-11-14', dataset='shopping_delivery'):
    path = base / ('live-bounded-20261114/g2b-real-'+day+'.sqlite3' if dataset=='shopping_delivery' else 'lofin-collection/budget-2026.sqlite3')
    path.parent.mkdir(parents=True, exist_ok=True)
    scope = day+':'+day if dataset=='shopping_delivery' else '2026:'+day
    payload = {'prdctIdntNoNm':'시험조명', 'corpNm':'시험업체', 'dminsttNm':'시험기관', 'prdctAmt':'1000',
               'api_key':'MUST-NOT-LEAVE-SERVER', 'private_note':'PRIVATE-ONLY'}
    if dataset=='budget': payload.update(fyr='2026', exe_ymd=day, dbiz_nm='시험사업', laf_hg_nm='시험기관', bdg_cash_amt='', ep_amt='100')
    body = json.dumps(payload,ensure_ascii=False); digest=hashlib.sha256(body.encode()).hexdigest()
    pairs=[['test-record',digest]]; response_hash=hashlib.sha256(json.dumps(pairs).encode()).hexdigest()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('''CREATE TABLE collection_checkpoints(dataset,scope_key,cursor_value,page_no,saved_count,source_total,status);
CREATE TABLE vnext_collection_pages(dataset,scope_key,generation,page_no,response_hash,item_count);
CREATE TABLE vnext_collection_items(dataset,scope_key,generation,page_no,source_key,payload_sha256);
CREATE TABLE raw_records(dataset,source_key,payload_json,payload_sha256);''')
        conn.execute('INSERT INTO collection_checkpoints VALUES(?,?,?,?,?,?,?)',(dataset,scope,json.dumps({'version':2,'generation':'fixture'}),2,1,1,'COMPLETE'))
        conn.execute('INSERT INTO vnext_collection_pages VALUES(?,?,?,?,?,?)',(dataset,scope,'fixture',1,response_hash,1))
        conn.execute('INSERT INTO vnext_collection_items VALUES(?,?,?,?,?,?)',(dataset,scope,'fixture',1,'test-record',digest))
        conn.execute('INSERT INTO raw_records VALUES(?,?,?,?)',(dataset,'test-record',body,digest))
    return path


class WebSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.path=fixture(self.root)
        self.config={'source_root':str(self.root),'state_directory':str(self.root/'sync-state'),'server_url':'https://approved.example'}
        self.calls=[]

    def sender(self,origin,token,payload):
        self.calls.append(payload)
        return {'ok':True,'digest':client.hashed(payload),'revision':payload['revision'],'stored_rows':len(payload['rows'])}

    def run_sync(self,sender=None):
        with patch('socket.socket',side_effect=AssertionError('REAL_NETWORK_FORBIDDEN')):
            return client.run(self.config,sender=sender or self.sender,token_loader=lambda c:'fixture-token-only')

    def test_real_projection_excludes_credentials_and_preserves_source(self):
        before=self.path.read_bytes();result=self.run_sync()
        self.assertEqual(result['confirmed_rows'],1);self.assertEqual(result['source_api_calls'],0)
        self.assertNotIn('MUST-NOT',json.dumps(self.calls));self.assertNotIn('PRIVATE-ONLY',json.dumps(self.calls))
        self.assertEqual(before,self.path.read_bytes())

    def test_new_day_is_discovered_and_already_confirmed_page_is_skipped(self):
        self.run_sync();self.assertEqual(self.run_sync()['attempted_pages'],0)
        fixture(self.root,'2026-11-15');result=self.run_sync()
        self.assertEqual(result['confirmed_rows'],1);self.assertEqual(self.calls[-1]['scope'],'2026-11-15')

    def test_transfer_failure_does_not_mark_success_or_block_other_source(self):
        fixture(self.root,dataset='budget')
        def sender(origin,token,payload):
            if payload['dataset']=='shopping_delivery':raise TimeoutError('do not log secrets')
            return self.sender(origin,token,payload)
        result=self.run_sync(sender)
        self.assertEqual(result['confirmed_pages'],1);self.assertEqual(result['state'],'CONTINUING_WITH_SYNC_ERROR')
        self.assertNotIn('do not log',json.dumps(result));self.assertEqual(self.run_sync()['confirmed_pages'],1)

    def test_unknown_budget_value_is_not_zero(self):
        fixture(self.root,dataset='budget');self.run_sync()
        row=next(p for p in self.calls if p['dataset']=='budget')['rows'][0]
        self.assertIsNone(row['budget_amount']);self.assertIsNone(row['remaining_amount']);self.assertFalse(row['unit_verified'])

    def test_raw_corruption_refuses_export_and_keeps_source(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:conn.execute("UPDATE raw_records SET payload_json='{}'")
        before=self.path.read_bytes();result=self.run_sync()
        self.assertEqual(result['confirmed_pages'],0);self.assertEqual(before,self.path.read_bytes())

    def test_https_origin_only_and_explicit_enable(self):
        target=self.root/'config.json'
        for url in ('http://example.com','https://user:password@example.com','https://example.com/path','https://example.com/?token=x'):
            target.write_text(json.dumps({'enabled':True,'server_url':url}))
            with self.assertRaises(ValueError):client.load_config(target)
        target.write_text(json.dumps({'enabled':False,'server_url':'https://example.com'}))
        with self.assertRaises(ValueError):client.load_config(target)

    def test_redirect_is_refused(self):
        with self.assertRaisesRegex(ValueError,'REDIRECT_REFUSED'):client.NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_wrong_or_malformed_web_ack_is_not_confirmation(self):
        items,errors=client.inventory([self.path]);self.assertFalse(errors)
        payload=client.export_page(items[0])
        good={'ok':True,'digest':client.hashed(payload),'revision':payload['revision'],'stored_rows':1}
        for ack in ([],None,{},dict(good,digest='wrong'),dict(good,revision='wrong'),dict(good,stored_rows=True),dict(good,stored_rows=0)):
            with self.subTest(ack=ack),patch.object(client.urllib.request,'build_opener') as opener:
                opener.return_value.open.return_value=io.BytesIO(json.dumps(ack).encode())
                with self.assertRaisesRegex(ValueError,'SYNC_RECEIPT_MISMATCH'):
                    client.send_page('https://approved.example','fixture-token',payload)

    def test_eight_page_limit_then_resume_without_duplicates(self):
        for day in range(1,10):fixture(self.root,f'2026-11-{day:02}')
        first=self.run_sync();self.assertEqual(first['attempted_pages'],8)
        second=self.run_sync();self.assertEqual(second['attempted_pages'],2)
        self.assertEqual(self.run_sync()['attempted_pages'],0)
        self.assertEqual(len({(p['scope'],p['page_no']) for p in self.calls}),10)

    def test_source_write_lock_is_released_before_projection(self):
        original=client.project
        def project(*args):
            # A separate simulated collector can commit between bounded reads.
            with closing(sqlite3.connect(self.path,timeout=.05)) as writer,writer:
                writer.execute('CREATE TABLE fixture_lock_probe(value)')
                writer.execute('INSERT INTO fixture_lock_probe VALUES(1)')
            return original(*args)
        with patch.object(client,'project',side_effect=project):
            self.assertEqual(self.run_sync()['confirmed_pages'],1)


if __name__=='__main__':unittest.main()
