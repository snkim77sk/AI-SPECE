import copy
import datetime as dt
import gzip
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
import main
import query_store
import test_viewer


def page(dataset='shopping_delivery', number=1, count=2):
    scope = '2026-11-14' if dataset == 'shopping_delivery' else '2026:2026-11-14' if dataset == 'budget' else '2026:ALL'
    day = '2026' if dataset == 'budget_appropriation' else '2026-11-14'
    rows = [{'source_key': str(number).zfill(62)+str(i).zfill(2), 'source_date': day, 'item_name': '시험조명', 'org_name': '시험기관', 'project_name': '시험사업', 'amount': '1000'} for i in range(count)]
    return {'schema_version': 2, 'dataset': dataset, 'scope': scope, 'page_no': number, 'revision': str(number)*64,
            'source_saved_count': 4, 'source_total': 4, 'source_status': 'COMPLETE',
            'generated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'rows': rows}


class PageSyncTests(unittest.TestCase):
    setUp = test_viewer.Tests.setUp
    request = test_viewer.Tests.request

    def sync_page(self, value=None, auth=True):
        return self.request('/api/sync-page', 'POST', gzip.compress(json.dumps(value or page()).encode()), 'Bearer '+'x'*40 if auth else None, 'gzip')

    def test_authenticated_upload_then_web_search_and_refresh(self):
        self.assertEqual(self.sync_page()[0], 200)
        data = json.loads(self.request('/api/results', query='q=시험')[1])
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['manifest']['collection_status']['stages'][0]['state'], 'PARTIAL')
        self.assertIn(b'/refresh.js', self.request()[1])
        self.assertTrue(json.loads(self.request('/api/update-status')[1])['revision'])
        self.assertIn(b'60000', self.request('/refresh.js')[1])

    def test_repeat_is_idempotent_and_ack_binds_payload(self):
        value = page(); first = json.loads(self.sync_page(value)[1]); second = json.loads(self.sync_page(value)[1])
        self.assertEqual(first['digest'], second['digest']); self.assertTrue(second['unchanged'])
        self.assertEqual(main.read_data({})['count'], 2)

    def test_new_page_adds_without_erasing_history(self):
        self.sync_page(); self.sync_page(page(number=2))
        result = main.read_data({}); self.assertEqual(result['count'], 4)
        self.assertEqual(result['manifest']['collection_status']['stages'][0]['state'], 'COMPLETE')

    def test_atomic_overlap_failure_preserves_previous_rows(self):
        self.sync_page(); value = page(number=2); value['rows'][0]['source_key'] = page()['rows'][0]['source_key']
        self.assertEqual(self.sync_page(value)[0], 400); self.assertEqual(main.read_data({})['count'], 2)

    def test_stale_update_cannot_replace_new_page(self):
        self.sync_page(); value = page(); value['generated_at_utc'] = '2020-01-01T00:00:00+00:00'
        self.assertEqual(self.sync_page(value)[0], 400); self.assertEqual(main.read_data({})['count'], 2)

    def test_budget_scope_is_separate_and_closed_by_default(self):
        self.sync_page(page('budget')); os.environ['LOFIN_PUBLIC_READ']='0'
        self.assertEqual(self.request('/api/results', query='section=budget_details')[0], 503)
        os.environ['LOFIN_PUBLIC_READ']='1'
        self.assertEqual(json.loads(self.request('/api/results', query='section=budget_details')[1])['count'], 2)
        self.assertIn('세부사업 예산'.encode(), self.request(query='section=budget_details')[1])

    def test_no_secret_fields_or_wrong_scope_are_accepted(self):
        for mutate in (lambda p:p['rows'][0].update(api_key='DO-NOT-PUBLISH'), lambda p:p['rows'][0].update(source_date='2020-01-01')):
            value = page(); mutate(value)
            self.assertEqual(self.sync_page(value)[0], 400)
        self.assertFalse(query_store.path().exists())

    def test_upload_requires_token_and_read_gate_stays_closed(self):
        self.assertEqual(self.sync_page(auth=False)[0], 401)
        self.sync_page(); os.environ['G2B_PUBLIC_READ']='0'
        self.assertEqual(self.request('/api/update-status')[0], 503)
        self.assertEqual(self.request('/api/results')[0], 503)

    def test_no_source_network_in_web_path(self):
        with patch('socket.socket', side_effect=AssertionError('source network forbidden')):
            self.sync_page(); self.request(); self.request('/api/update-status')

    def test_bad_shape_and_duplicate_keys(self):
        for key, value in [('page_no', True), ('source_total', -1), ('source_status', 'MADE_UP'), ('scope', '../private'), ('rows', [None])]:
            p = page(); p[key] = value; self.assertEqual(self.sync_page(p)[0], 400)
        p=page();p['rows'][1]=copy.deepcopy(p['rows'][0]);self.assertEqual(self.sync_page(p)[0],400)

    def test_query_is_literal_and_xss_is_escaped(self):
        p = page(); p['rows'][0]['item_name'] = '<script>danger</script>'; self.sync_page(p)
        self.assertNotIn(b'<script>danger', self.request()[1])
        self.assertEqual(json.loads(self.request('/api/results', query="q=';DROP+TABLE+query_rows;--")[1])['count'],0)

    def test_wrong_existing_database_is_preserved(self):
        conn=sqlite3.connect(query_store.path());conn.execute('CREATE TABLE raw_records(x)');conn.commit();conn.close()
        before=query_store.path().read_bytes();self.assertEqual(self.sync_page()[0],400)
        self.assertEqual(query_store.path().read_bytes(),before)

    def test_asgi_page_upload_readback_and_search_text(self):
        from fastapi.testclient import TestClient
        with TestClient(main.app) as browser:
            response=browser.post('/api/sync-page',content=gzip.compress(json.dumps(page()).encode()),
                headers={'Authorization':'Bearer '+'x'*40,'Content-Encoding':'gzip','Content-Type':'application/json'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['stored_rows'],2)
            result=browser.get('/api/results',params={'q':'시험'})
            self.assertEqual(result.json()['count'],2)
            html=browser.get('/',params={'q':'시험'}).text
            self.assertIn('value="시험"',html)
            self.assertIn('마지막 웹 반영(한국시간)',html)


if __name__ == '__main__': unittest.main()
