from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from test_web_sync import fixture
import gpt_sync_client as g


class FakeSite:
    def __init__(self): self.rows={};self.posts=0;self.reads=0;self.fail=set();self.lost=False
    def receipt(self,p):self.reads+=1;return self.rows.get(g.page_id(p))
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
        with patch('socket.socket',side_effect=AssertionError('NETWORK_FORBIDDEN')):return g.run(self.config,self.site)
    def test_private_fields_never_export_and_source_unchanged(self):
        p=self.fixture();before=p.read_bytes();items,_=g.inventory([p]);v=g.export_server_page(items[0]);self.assertNotIn('api_key',v['rows'][0]);self.assertNotIn('private_note',v['rows'][0]);self.assertEqual(v['source_updated_at'],'2026-11-14T15:14:05.000Z');self.assertEqual(self.run_sync()['confirmed_pages'],1);self.assertEqual(before,p.read_bytes())
    def test_lost_post_response_resolved_by_read_before_any_repost(self):
        self.fixture();self.site.lost=True;self.assertEqual(self.run_sync()['confirmed_pages'],0);self.assertEqual(self.run_sync()['confirmed_pages'],1);self.assertEqual(self.site.posts,1);self.assertEqual(self.run_sync()['attempted_pages'],0)
    def test_error_isolated_and_does_not_log_exception_content(self):
        self.fixture();self.fixture(dataset='budget');self.site.fail.add('shopping_delivery');r=self.run_sync();self.assertEqual(r['confirmed_pages'],1);self.assertEqual(r['source_api_calls'],0);self.assertNotIn('SECRET',json.dumps(r))
    def test_eight_pages_and_resume(self):
        for day in range(1,11):self.fixture(f'2026-11-{day:02}')
        self.assertEqual(self.run_sync()['confirmed_pages'],8);self.assertEqual(self.run_sync()['confirmed_pages'],2);self.assertEqual(self.site.posts,10)
    def test_exact_site_origin_only(self):
        for origin in ['https://example.com',g.SITE_ORIGIN+'.evil',g.SITE_ORIGIN+'/wrong']:
            with self.assertRaises(ValueError):g.SiteClient(origin,'placeholder')
    def test_receipt_identity_revision_and_counts_are_verified(self):
        p=self.fixture();items,_=g.inventory([p]);v=g.export_server_page(items[0]);r=self.site.upload(v);self.assertEqual(g.validate_receipt(r,v),r)
        for bad in [dict(r,id='bad'),dict(r,revision='bad'),dict(r,source_rows=0),None]:
            with self.assertRaises(ValueError):g.validate_receipt(bad,v)


if __name__=='__main__':unittest.main()
