from contextlib import closing
from datetime import datetime,timedelta,timezone
import io
import json
from pathlib import Path
import sqlite3
import ssl
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from test_web_sync import fixture
import gpt_sync_client as g
import collector_status


class FakeSite:
    def __init__(self): self.rows={};self.posts=0;self.reads=0;self.fail=set();self.lost=False
    def receipt(self,p):self.reads+=1;return self.rows.get(g.page_id(p))
    def upload_status(self,p):return {'digest':g.hashed(p),'payload':p}
    def upload(self,p):
        self.posts+=1
        if p['dataset'] in self.fail:raise TimeoutError('SECRET MUST NOT BE LOGGED')
        r={'id':g.page_id(p),'revision':p['revision'],'dataset':p['dataset'],'source_rows':len(p['rows']),'matched_rows':len(p['rows'])}
        self.rows[r['id']]=r
        if self.lost:raise TimeoutError()
        return self.receipt(p)


class GptSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.site=FakeSite()
        self.config={'source_root':str(self.root),'state_directory':str(self.root/'state'),'server_url':g.SITE_ORIGIN}
    def fixture(self,day='2026-11-14',dataset='shopping_delivery'):
        path=fixture(self.root,day,dataset)
        with closing(sqlite3.connect(path)) as d,d:d.execute("ALTER TABLE collection_checkpoints ADD COLUMN updated_at TEXT DEFAULT '2026-11-14 15:14:05'")
        return path
    def run_sync(self):
        with patch('socket.socket',side_effect=AssertionError('NETWORK_FORBIDDEN')), patch.object(g,'collect_status',side_effect=lambda root:collector_status.collect_status(root,task_reader=lambda:{})):return g.run(self.config,self.site)
    def test_private_fields_never_export_and_source_unchanged(self):
        p=self.fixture();before=p.read_bytes();items,_=g.inventory([p]);v=g.export_server_page(items[0]);self.assertNotIn('api_key',v['rows'][0]);self.assertNotIn('private_note',v['rows'][0]);self.assertEqual(v['source_updated_at'],'2026-11-14T15:14:05.000Z');self.assertEqual(self.run_sync()['confirmed_pages'],1);self.assertEqual(before,p.read_bytes())
    def test_lost_post_response_resolved_by_read_before_any_repost(self):
        self.fixture();self.site.lost=True;self.assertEqual(self.run_sync()['confirmed_pages'],0);self.assertEqual(self.run_sync()['confirmed_pages'],1);self.assertEqual(self.site.posts,1);self.assertEqual(self.run_sync()['attempted_pages'],0)
    def test_error_isolated_and_does_not_log_exception_content(self):
        self.fixture();self.fixture(dataset='budget');self.site.fail.add('shopping_delivery');r=self.run_sync();self.assertEqual(r['confirmed_pages'],1);self.assertEqual(r['source_api_calls'],0);self.assertNotIn('SECRET',json.dumps(r));self.assertEqual(r['pending_pages'],1)

    def test_rejected_page_does_not_starve_same_dataset_and_retries_after_persisted_delay(self):
        self.fixture('2026-11-14');self.fixture('2026-11-13')
        clock=datetime(2026,11,15,10,tzinfo=timezone.utc)
        upload=self.site.upload
        def reject_latest(p):
            if p['scope']=='2026-11-14':raise g.SiteHTTPError(422,'SYNC_INVALID_CHECKPOINT')
            return upload(p)
        with patch.object(g,'utc_now',return_value=clock),patch.object(self.site,'upload',side_effect=reject_latest):
            first=self.run_sync()
        self.assertEqual((first['confirmed_pages'],first['pending_pages'],first['deferred_pages']),(1,1,1))
        self.assertEqual(first['errors'][0]['http_status'],422)
        self.assertEqual(first['errors'][0]['reason'],'SYNC_INVALID_CHECKPOINT')
        self.assertEqual(first['next_page_retry_at_utc'],(clock+timedelta(minutes=15)).isoformat())
        with patch.object(g,'utc_now',return_value=clock+timedelta(minutes=1)):
            waiting=self.run_sync()
        self.assertEqual((waiting['attempted_pages'],waiting['pending_pages'],waiting['deferred_pages']),(0,1,1))
        self.assertEqual(waiting['state'],'CONTINUING_WITH_SYNC_ERROR')
        with patch.object(g,'utc_now',return_value=clock+timedelta(minutes=16)):
            resumed=self.run_sync()
        self.assertEqual((resumed['confirmed_pages'],resumed['pending_pages'],resumed['deferred_pages']),(1,0,0))
        self.assertEqual(self.site.posts,2)

    def test_http_status_and_public_code_are_preserved_without_response_secrets(self):
        client=g.SiteClient(g.SITE_ORIGIN,'PRIVATE-TOKEN')
        error=urllib.error.HTTPError('https://private.invalid/?token=SECRET',422,'PRIVATE MESSAGE',{},
            io.BytesIO(b'{"error":"SYNC_INVALID_CHECKPOINT","detail":"SECRET BODY"}'))
        with patch.object(client.opener,'open',side_effect=error):
            with self.assertRaises(g.SiteHTTPError) as caught:client.request('/api/server-sync')
        self.assertEqual(caught.exception.http_status,422)
        self.assertEqual(str(caught.exception),'SYNC_INVALID_CHECKPOINT')
        self.assertNotIn('SECRET',str(caught.exception))

    def test_untrusted_error_body_is_not_a_log_message(self):
        client=g.SiteClient(g.SITE_ORIGIN,'PRIVATE-TOKEN')
        error=urllib.error.HTTPError('https://private.invalid',500,'SECRET',{},io.BytesIO(b'{"error":"SECRET URL token=abc"}'))
        with patch.object(client.opener,'open',side_effect=error):
            with self.assertRaises(g.SiteHTTPError) as caught:client.request('/api/server-sync')
        self.assertEqual((caught.exception.http_status,str(caught.exception)),(500,'SITE_HTTP_ERROR'))
    def test_eight_pages_and_resume(self):
        for day in range(1,11):self.fixture(f'2026-11-{day:02}')
        self.assertEqual(self.run_sync()['confirmed_pages'],8);self.assertEqual(self.run_sync()['confirmed_pages'],2);self.assertEqual(self.site.posts,10)
    def test_status_failure_does_not_stop_page_transfer(self):
        self.fixture()
        with patch.object(self.site,'upload_status',side_effect=TimeoutError('SECRET')):
            r=self.run_sync()
        self.assertEqual(r['confirmed_pages'],1);self.assertEqual(r['collector_status']['state'],'UNCONFIRMED');self.assertNotIn('SECRET',json.dumps(r))
    def test_status_is_sent_even_when_no_new_pages(self):
        with patch.object(self.site,'upload_status',wraps=self.site.upload_status) as status:
            r=self.run_sync()
        self.assertEqual(status.call_count,1);self.assertEqual(r['collector_status']['state'],'VERIFIED');self.assertEqual(r['attempted_pages'],0)
    def test_status_lost_response_is_resolved_without_repost(self):
        client=g.SiteClient(g.SITE_ORIGIN,'placeholder');payload={'status':'sample'};snapshot={'digest':g.hashed(payload)}
        with patch.object(client,'request',side_effect=[TimeoutError(),{'snapshot':snapshot}]) as request:
            self.assertEqual(client.upload_status(payload),snapshot)
        self.assertEqual([c.args[0] for c in request.call_args_list],['/api/server-status','/api/server-status'])
        self.assertEqual(len(request.call_args_list[1].args),1)
    def test_exact_site_origin_only(self):
        for origin in ['https://example.com',g.SITE_ORIGIN+'.evil',g.SITE_ORIGIN+'/wrong']:
            with self.assertRaises(ValueError):g.SiteClient(origin,'placeholder')
    def test_receipt_identity_revision_and_counts_are_verified(self):
        p=self.fixture();items,_=g.inventory([p]);v=g.export_server_page(items[0]);r=self.site.upload(v);self.assertEqual(g.validate_receipt(r,v),r)
        for bad in [dict(r,id='bad'),dict(r,revision='bad'),dict(r,source_rows=0),dict(r,source_rows=True),
                    dict(r,matched_rows=-1),dict(r,matched_rows=2),dict(r,matched_rows=True),
                    {k:v for k,v in r.items() if k!='matched_rows'},None]:
            with self.assertRaises(ValueError):g.validate_receipt(bad,v)

    def test_unconfirmed_receipt_preserves_queue_and_another_dataset_can_continue(self):
        self.fixture()
        self.fixture(dataset='budget')
        receipt=self.site.receipt
        def malformed(p):
            if p['dataset']!='shopping_delivery':
                return receipt(p)
            return {'id':g.page_id(p),'dataset':p['dataset'],'revision':p['revision'],
                    'source_rows':len(p['rows']),'matched_rows':'incorrect'}
        with patch.object(self.site,'receipt',side_effect=malformed):
            report=self.run_sync()
        self.assertEqual((report['confirmed_pages'],report['pending_pages'],self.site.posts),(1,1,1))
        self.assertEqual(report['errors'][0]['reason'],'SITE_RECEIPT_MISMATCH')
        self.assertEqual(self.run_sync()['confirmed_pages'],1)

    def test_site_reply_requires_an_object_and_explicit_receipt_field(self):
        client=g.SiteClient(g.SITE_ORIGIN,'placeholder')
        page=g.export_server_page(g.inventory([self.fixture()])[0][0])
        with patch.object(client.opener,'open',return_value=io.BytesIO(b'[]')):
            with self.assertRaisesRegex(ValueError,'SITE_RESPONSE_INVALID'):
                client.receipt(page)
        with patch.object(client.opener,'open',return_value=io.BytesIO(b'{}')):
            with self.assertRaisesRegex(ValueError,'SITE_RECEIPT_UNAVAILABLE'):
                client.receipt(page)

    def test_post_receipt_and_durable_readback_must_agree(self):
        client=g.SiteClient(g.SITE_ORIGIN,'placeholder')
        page=g.export_server_page(g.inventory([self.fixture()])[0][0])
        receipt=self.site.upload(page)
        with patch.object(client,'request',side_effect=[
            {'receipt':receipt}, {'receipt':dict(receipt,matched_rows=0)},
        ]):
            with self.assertRaisesRegex(ValueError,'SITE_READBACK_MISMATCH'):
                client.upload(page)

    def test_read_only_receipt_retry_for_tls_eof(self):
        client=g.SiteClient(g.SITE_ORIGIN,'placeholder')
        error=urllib.error.URLError(ssl.SSLEOFError(ssl.SSL_ERROR_EOF,'sensitive details'))
        with patch.object(client.opener,'open',side_effect=[error,io.BytesIO(b'{"receipt":null}')]) as send:
            self.assertEqual(client.request('/api/server-sync'),{'receipt':None})
        self.assertEqual([c.args[0].get_method() for c in send.call_args_list],['GET','GET'])
        self.assertLessEqual(send.call_args_list[1].kwargs['timeout'],send.call_args_list[0].kwargs['timeout'])

    def test_certificate_failure_is_not_retried_and_does_not_expose_message(self):
        client=g.SiteClient(g.SITE_ORIGIN,'placeholder')
        error=ssl.SSLCertVerificationError(1,'private details')
        with patch.object(client.opener,'open',side_effect=error) as send:
            with self.assertRaises(g.SiteTLSFailure) as caught:client.request('/api/server-sync')
        self.assertEqual(send.call_count,1)
        self.assertFalse(caught.exception.interrupted)
        self.assertEqual(caught.exception.request_method,'GET')
        self.assertNotIn('private details',str(caught.exception))

    def test_interrupted_post_resolves_receipt_without_posting_again(self):
        client=g.SiteClient(g.SITE_ORIGIN,'placeholder')
        page=g.export_server_page(g.inventory([self.fixture()])[0][0])
        stored=self.site.upload(page)
        error=ssl.SSLEOFError(ssl.SSL_ERROR_EOF,'private details')
        for durable in (stored,None):
            with patch.object(client.opener,'open',side_effect=[
                error,io.BytesIO(json.dumps({'receipt':durable}).encode()),
            ]) as send:
                if durable is None:
                    with self.assertRaises(g.SiteTLSFailure):client.upload(page)
                else:
                    self.assertEqual(client.upload(page),stored)
            self.assertEqual([c.args[0].get_method() for c in send.call_args_list],['POST','GET'])

    def test_public_service_token_is_optional_and_not_logged(self):
        class Reply:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,limit): return b'{"receipt":null}'
        class Opener:
            def __init__(self): self.requests=[]
            def open(self,req,timeout): self.requests.append(req);return Reply()
        old=g.SiteClient(g.SITE_ORIGIN,'PRIVATE-TOKEN')
        old.opener=Opener()
        self.assertEqual(old.request('/api/server-sync'),{'receipt':None})
        self.assertNotIn('X-g2b-service-token',dict(old.opener.requests[0].header_items()))
        with self.assertRaises(ValueError):g.SiteClient(g.SITE_ORIGIN,'PRIVATE-TOKEN',service_token='short')
        fresh=g.SiteClient(g.SITE_ORIGIN,'PRIVATE-TOKEN',service_token='S'*64)
        fresh.opener=Opener()
        fresh.request('/api/server-sync')
        self.assertEqual(dict(fresh.opener.requests[0].header_items())['X-g2b-service-token'],'S'*64)
        self.assertNotIn('S'*64,str(fresh.opener.requests[0].full_url))

    def test_public_service_token_uses_optional_dpapi_file(self):
        self.assertIsNone(g.load_public_service_token({}))
        with self.assertRaises(ValueError):g.load_public_service_token({'public_service_token_file':''})
        with patch.object(g,'load_token',return_value='T'*64) as loader:
            token=g.load_public_service_token({'public_service_token_file':'D:/G2B/service-token.dpapi','token_file':'existing.dpapi'})
            self.assertEqual(token,'T'*64)
            self.assertEqual(loader.call_args.args[0]['token_file'],'D:/G2B/service-token.dpapi')


if __name__=='__main__':unittest.main()
