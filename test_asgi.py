"""ASGI adapter regression: same access policy and data path as WSGI."""
import gzip,json,os,tempfile,unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import main
from test_viewer import payload

class AdapterTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  env=patch.dict(os.environ,{'G2B_RESULT_DATA_DIR':tmp.name,'G2B_PUBLIC_READ':'0','G2B_RESULT_SYNC_TOKEN':'x'*40})
  env.start();self.addCleanup(env.stop)
  self.client=TestClient(main.app);self.addCleanup(self.client.close)
 def test_health_no_data_or_secret(self):
  for route in ('/live','/health','/__ai_space_health'):
   r=self.client.get(route);self.assertEqual(r.status_code,200)
   self.assertEqual(r.json()['version'],main.VERSION)
   self.assertFalse(r.json()['source_collection_enabled'])
   self.assertNotIn('x'*40,r.text)
  self.assertFalse(main.db_path().exists())
 def test_read_access_stays_closed(self):
  for route in ('/api/results','/release-notes','/style.css','/ready'):
   self.assertEqual(self.client.get(route).status_code,503)
 def test_root_probe_is_healthy_but_private(self):
  r=self.client.get('/');self.assertEqual(r.status_code,200)
  self.assertIn('READ_ACCESS_NOT_ENABLED',r.text)
  self.assertFalse(main.db_path().exists())
  self.assertEqual(self.client.head('/').status_code,200)
 def test_no_exposed_framework_docs(self):
  for route in ('/docs','/redoc','/openapi.json'):
   self.assertEqual(self.client.get(route).status_code,404)
 def test_sync_and_authenticated_policy_unchanged(self):
  raw=gzip.compress(json.dumps(payload()).encode())
  headers={'Content-Encoding':'gzip','Content-Type':'application/json'}
  self.assertEqual(self.client.post('/api/result-sync',content=raw,headers=headers).status_code,401)
  headers['Authorization']='Bearer '+'x'*40
  r=self.client.post('/api/result-sync',content=raw,headers=headers)
  self.assertEqual(r.status_code,200);self.assertEqual(r.json()['total_rows'],2)
  self.assertEqual(self.client.get('/api/results').status_code,503)
  os.environ['G2B_PUBLIC_READ']='1' # local synthetic test only
  self.assertEqual(self.client.get('/api/results').json()['count'],1)
  self.assertIn('수집 완료 미확인',self.client.get('/').text)
  self.assertEqual(self.client.get('/ready').status_code,200)
 def test_mutation_and_corrupt_payload(self):
  self.assertEqual(self.client.post('/collect').status_code,405)
  r=self.client.post('/api/result-sync',content=b'bad',headers={'Content-Encoding':'gzip','Authorization':'Bearer '+'x'*40})
  self.assertEqual(r.status_code,400)
 def test_declared_body_limit(self):
  self.assertEqual(self.client.post('/api/result-sync',content=b'x',headers={'Content-Length':str(main.MAX_BODY+1)}).status_code,400)
 def test_streamed_body_limit(self):
  with patch.object(main,'MAX_BODY',2):
   self.assertEqual(self.client.post('/api/result-sync',content=iter([b'abc'])).status_code,400)
 def test_head_body_empty(self):
  r=self.client.head('/live');self.assertEqual(r.status_code,200);self.assertEqual(r.content,b'')
