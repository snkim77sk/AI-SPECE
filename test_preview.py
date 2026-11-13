import os,json,unittest
from unittest.mock import patch
import main,test_viewer
class PreviewTests(unittest.TestCase):
 setUp=test_viewer.Tests.setUp
 request=test_viewer.Tests.request
 sync=test_viewer.Tests.sync
 def test_preview_without_access_or_database(self):
  os.environ['G2B_PUBLIC_READ']='0'
  with patch.object(main,'connect',side_effect=AssertionError('NO_DB')):
   for section in ('shopping','vendors','budget_targets','budget_prebid'):
    code,b,_=self.request('/preview',query='section='+section)
    self.assertEqual(code,200);self.assertIn('실제 자료 없음'.encode(),b)
   self.assertEqual(self.request('/api/results')[0],503)
   self.assertEqual(self.request('/ready')[0],503)
   self.assertEqual(self.request('/style.css')[0],200)
 def test_preview_never_returns_stored_rows(self):
  self.sync();os.environ['G2B_PUBLIC_READ']='1'
  with patch.object(main,'connect',side_effect=AssertionError('NO_DB')):
   self.assertEqual(self.request('/preview')[0],200)
 def test_preview_invalid_and_escaped_search(self):
  self.assertEqual(self.request('/preview',query='section=secret')[0],400)
  b=self.request('/preview',query='q=%3Cscript%3E')[1]
  self.assertNotIn(b'<script>',b)
 def test_source_states_separate(self):
  os.environ['LOFIN_PUBLIC_READ']='1';p=test_viewer.payload()
  p['sections']['budget_targets']=[{'project_name':'synthetic'}]
  p['collection_status']['stages']=[{'dataset':'budget','state':'COMPLETE'},{'dataset':'budget_appropriation','state':'INCOMPLETE'}]
  self.sync(p);b=self.request(query='section=budget_targets')[1]
  self.assertIn(b'budget: COMPLETE',b);self.assertIn(b'budget_appropriation: INCOMPLETE',b)
