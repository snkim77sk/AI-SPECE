import json,os,unittest
import test_viewer
from test_viewer import payload
class BudgetTests(unittest.TestCase):
 setUp=test_viewer.Tests.setUp
 request=test_viewer.Tests.request
 sync=test_viewer.Tests.sync
 def budget(self,rows=None):
  p=payload();p['sections']['budget_targets']=rows if rows is not None else [{'fiscal_year':2026,'org_name':'합성기관','project_name':'합성조명','budget_amount':100,'remaining_amount':50,'api_key':'secret-canary','private_note':'hidden-canary'}];return p
 def test_budget_closed_even_g2b_open(self):
  self.sync(self.budget());os.environ['LOFIN_PUBLIC_READ']='0'
  code,b,_=self.request('/api/results',query='section=budget_targets');self.assertEqual(code,503);self.assertNotIn('합성기관'.encode(),b)
 def test_budget_search_and_allowlist(self):
  os.environ['LOFIN_PUBLIC_READ']='1';self.sync(self.budget())
  d=json.loads(self.request('/api/results',query='section=budget_targets&q=합성')[1]);self.assertEqual(d['count'],1);self.assertNotIn('api_key',d['rows'][0]);self.assertNotIn('secret-canary',json.dumps(d))
  d=json.loads(self.request('/api/results',query='section=budget_targets&q=hidden-canary')[1]);self.assertEqual(d['count'],0)
 def test_missing_not_empty(self):
  os.environ['LOFIN_PUBLIC_READ']='1';self.sync();self.assertIn('미수집'.encode(),self.request(query='section=budget_targets')[1])
 def test_empty_not_complete(self):
  os.environ['LOFIN_PUBLIC_READ']='1';self.sync(self.budget([]));self.assertIn('수신된 자료 0건'.encode(),self.request(query='section=budget_targets')[1])
 def test_no_shopping_stage_reused(self):
  os.environ['LOFIN_PUBLIC_READ']='1';p=self.budget();p['collection_status']['stages'][0]['state']='COMPLETE';self.sync(p)
  b=self.request(query='section=budget_targets')[1];self.assertIn('수집 상태 미확인'.encode(),b);self.assertNotIn(b'COMPLETE',b)
 def test_g2b_manifest_hides_budget_and_secrets(self):
  p=self.budget();p['collection_status']['secret']='secret-canary';self.sync(p)
  b=self.request('/api/results')[1];self.assertNotIn(b'budget_targets',b);self.assertNotIn(b'secret-canary',b)
 def test_budget_html_escaping(self):
  os.environ['LOFIN_PUBLIC_READ']='1';self.sync(self.budget([{'project_name':'<script>bad</script>'}]))
  b=self.request(query='section=budget_targets')[1];self.assertNotIn(b'<script>',b);self.assertIn(b'&lt;script&gt;',b)
 def test_legacy_search_index_cannot_leak(self):
  import main
  self.sync();c=main.connect(True);c.execute("UPDATE viewer_rows SET search='legacy-secret-canary'");c.commit();c.close()
  self.assertEqual(main.read_data({'q':['legacy-secret-canary']})['count'],0)

 def test_malformed_stage_rejected(self):
  p=self.budget();p['collection_status']['stages']=[{'dataset':[],'state':'STALE'}]
  self.assertEqual(self.sync(p)[0],400)
