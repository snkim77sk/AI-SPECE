import copy,gzip,io,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import main

def payload():
 return {'schema_version':1,'snapshot_id':'sample-1','generated_at_utc':'2026-11-12T00:00:00+00:00','source_version':'3.2.6','sections':{'shopping':[{'source_key':'a','item_name':'<script>alert(1)</script>조명','source_date':'2026-11-12','primary_category':'LIGHTING','amount':1000}], 'vendors':[{'vendor_name':'샘플','total_amount':1000}]},'collection_status':{'collection_controls_enabled':True,'stages':[{'dataset':'shopping_delivery','state':'STALE','saved_count':12987,'source_total':13093}]},'readiness':{},'source_counts':{}}

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.env=patch.dict(os.environ,{'G2B_RESULT_DATA_DIR':self.tmp.name,'G2B_PUBLIC_READ':'1','G2B_RESULT_SYNC_TOKEN':'x'*40});self.env.start();self.addCleanup(self.env.stop)
 def request(self,path='/',method='GET',body=b'',auth=None,encoding='',query=''):
  e={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body),'HTTP_CONTENT_ENCODING':encoding}
  if auth:e['HTTP_AUTHORIZATION']=auth
  out={}
  b=b''.join(main.application(e,lambda s,h:out.update(status=int(s.split()[0]),headers=dict(h))))
  return out['status'],b,out['headers']
 def sync(self,p=None):return self.request('/api/result-sync','POST',gzip.compress(json.dumps(p or payload()).encode()),'Bearer '+'x'*40,'gzip')
 def test_version_contract(self):
  import re,tomllib
  root=Path(main.__file__).parent
  self.assertRegex(main.VERSION,r'^\d+\.\d+\.\d+$')
  self.assertEqual(tomllib.loads((root/'pyproject.toml').read_text())['project']['version'],main.VERSION)
  self.assertIn('## '+main.VERSION+' ·',(root/'CHANGELOG.md').read_text())
  self.assertEqual(json.loads(self.request('/live')[1])['version'],main.VERSION)
  self.assertIn(('v'+main.VERSION).encode(),self.request()[1])
 def test_release_notes_access(self):
  self.assertEqual(self.request('/release-notes')[0],200)
  self.assertIn(main.VERSION.encode(),self.request('/release-notes')[1])
  os.environ.pop('G2B_PUBLIC_READ');self.assertEqual(self.request('/release-notes')[0],503)
 def test_sync_readback(self):
  self.assertEqual(self.sync()[0],200);d=json.loads(self.request('/api/results')[1]);self.assertEqual(d['count'],1);self.assertFalse(d['collection_controls_enabled'])
 def test_default_closed_no_new_password(self):
  os.environ.pop('G2B_PUBLIC_READ');self.assertEqual(self.request()[0],503)
 def test_health_without_db(self):
  os.environ.pop('G2B_PUBLIC_READ');self.assertEqual(self.request('/health')[0],200);self.assertFalse(main.db_path().exists())
 def test_missing_sync_key(self):
  os.environ.pop('G2B_RESULT_SYNC_TOKEN');self.assertEqual(self.sync()[0],503)
 def test_wrong_sync_key(self):self.assertEqual(self.request('/api/result-sync','POST',b'{}','Bearer bad')[0],401)
 def test_unicode_bad_auth(self):self.assertEqual(self.request('/api/result-sync','POST',b'{}','Bearer 한글')[0],401)
 def test_unknown_mutations(self):
  for p in ['/collect','/api/collect','/settings','/reset','/']:
   self.assertEqual(self.request(p,'POST')[0],405)
 def test_xss_escaped(self):
  self.sync();s,b,h=self.request();self.assertNotIn(b'<script>',b);self.assertIn(b'&lt;script&gt;',b);self.assertIn("frame-ancestors 'none'",h['Content-Security-Policy'])
 def test_partial_not_complete(self):
  self.sync();b=self.request()[1].decode();self.assertIn('수집 완료 미확인',b);self.assertIn('12987 / 13093',b)
 def test_filter(self):
  self.sync();self.assertEqual(json.loads(self.request('/api/results',query='q=조명')[1])['count'],1);self.assertEqual(json.loads(self.request('/api/results',query='category=POLE')[1])['count'],0)
 def test_sql_text_not_code(self):
  self.sync();self.assertEqual(self.request('/api/results',query="q=';DROP+TABLE+viewer_rows;--")[0],200);self.assertEqual(main.read_data({})['count'],1)
 def test_repeat_idempotent(self):
  self.sync();self.assertTrue(json.loads(self.sync()[1])['unchanged'])
 def test_same_id_conflict_preserves(self):
  self.sync();p=payload();p['sections']['shopping']=[];self.assertEqual(self.sync(p)[0],400);self.assertEqual(main.read_data({})['count'],1)
 def test_old_timestamp_preserves(self):
  self.sync();p=payload();p['snapshot_id']='old';p['generated_at_utc']='2020-01-01T00:00:00Z';self.assertEqual(self.sync(p)[0],400);self.assertEqual(main.read_data({})['count'],1)
 def test_invalid_schema_preserves(self):
  self.sync();p=payload();p['schema_version']=True;self.assertEqual(self.sync(p)[0],400);self.assertEqual(main.read_data({})['count'],1)
 def test_invalid_row_preserves(self):
  self.sync();p=payload();p['sections']['shopping']=[False];self.assertEqual(self.sync(p)[0],400);self.assertEqual(main.read_data({})['count'],1)
 def test_corrupt_gzip(self):self.assertEqual(self.request('/api/result-sync','POST',b'bad','Bearer '+'x'*40,'gzip')[0],400)
 def test_gzip_bomb(self):
  with patch.object(main,'MAX_JSON',128):
   self.assertEqual(self.request('/api/result-sync','POST',gzip.compress(b' '*200),'Bearer '+'x'*40,'gzip')[0],400)
 def test_compressed_limit(self):
  with patch.object(main,'MAX_BODY',2):self.assertEqual(self.sync()[0],400)
 def test_nonfinite_json(self):
  with self.assertRaises(main.Invalid):main.decode(b'{"x":NaN}','')
 def test_unknown_section(self):
  p=payload();p['sections']['accounts']=[];self.assertEqual(self.sync(p)[0],400)
 def test_invalid_page(self):self.assertEqual(self.request('/api/results',query='page=no')[0],400)
 def test_head(self):self.assertEqual(self.request('/live','HEAD')[1],b'')
 def test_ready_without_snapshot(self):self.assertEqual(self.request('/ready')[0],503)
 def test_ready_with_snapshot(self):self.sync();self.assertEqual(self.request('/ready')[0],200)
 def test_no_legacy_db_mutation(self):
  c=sqlite3.connect(main.db_path());c.execute('create table raw_records (x)');c.commit();c.close();before=main.db_path().read_bytes();self.assertEqual(self.sync()[0],400);self.assertEqual(main.db_path().read_bytes(),before)
 def test_read_only_no_db_creation(self):
  self.request();self.assertFalse(main.db_path().exists())
 def test_invalid_status_stages_preserves(self):
  self.sync()
  for stages in (None,[1],{},'bad'):
   p=payload();p['snapshot_id']='bad';p['collection_status']['stages']=stages
   self.assertEqual(self.sync(p)[0],400)
   self.assertEqual(self.request()[0],200)
 def test_defensive_render_bad_saved_stages(self):
  self.sync();data=main.read_data({});data['manifest']['collection_status']['stages']=None
  self.assertIn('수집 완료 미확인',main.page_html(data,{}).decode())
 def test_invalid_deflate(self):
  bad=bytes.fromhex('1f8b080000000000000307000000000000000000')
  self.assertEqual(self.request('/api/result-sync','POST',bad,'Bearer '+'x'*40,'gzip')[0],400)
 def test_actual_schema_demand_org(self):
  p=payload();p['sections']['shopping'][0]['demand_org']='신성시';self.sync(p)
  self.assertIn('신성시',self.request()[1].decode())
 def test_no_network(self):
  with patch('socket.socket',side_effect=AssertionError('network forbidden')):
   self.sync();self.request();self.request('/api/results')
 def test_no_secrets_in_responses(self):
  self.sync();self.assertNotIn(('x'*40).encode(),self.request('/api/results')[1])
 def test_rollback_on_storage_failure(self):
  self.sync();p=payload();p['snapshot_id']='next';p['generated_at_utc']='2026-11-12T01:00:00Z'
  original=main.canonical
  def fail(v):
   if isinstance(v,dict) and v.get('snapshot_id')=='next' and 'row_counts' in v:raise ValueError('simulated')
   return original(v)
  with patch.object(main,'canonical',side_effect=fail):self.assertEqual(self.sync(p)[0],400)
  self.assertEqual(main.read_data({})['manifest']['snapshot_id'],'sample-1')

if __name__=='__main__':unittest.main()
