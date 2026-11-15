import hashlib
import json
import sqlite3
import unittest
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path
import test_lofin_collection as fixture
import lofin_analysis as analysis
import lofin_scheduled_entry as scheduled


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.LofinTests();self.case.setUp()
        self.case.invoke(fixture.Fixture(total=2),max_pages=2)
        self.path=self.case.root/'budget-2026.sqlite3'
    def tearDown(self): self.case.tearDown()
    def test_both_layers_stay_separate_and_query_does_not_mutate_db(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        r=analysis.analyze(self.path)
        self.assertEqual(r['rows'],4)
        self.assertIsNone(r['combined_budget_total'])
        self.assertFalse(r['unit_verified'])
        detail=next(x for x in r['layers'] if x['source']=='QWGJK')
        self.assertEqual(detail['collected_business_amount_sums']['bdg_cash_amt'],'2000000')
        self.assertEqual(detail['top_rows'][0]['execution_rate_percent'],'25.00')
        annual=next(x for x in r['layers'] if x['source']=='AIDFA')
        self.assertIsNone(annual['collected_business_amount_sums'])
        self.assertIsNone(annual['top_rows'][0]['executed'])
        self.assertEqual(annual['top_rows'][0]['administration_budget'],'10000')
        self.assertEqual(annual['top_rows'][0]['financial_activity_budget'],'5000')
        self.assertEqual(annual['top_rows'][0]['raw_amounts']['padm_oper_prsm_exps'],'9000')
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
    def test_previous_snapshot_is_not_added_to_current_budget(self):
        conn=sqlite3.connect(self.path)
        with conn:
            conn.execute("INSERT INTO raw_records(dataset,source_key,source_date,payload_json,payload_sha256) SELECT dataset,source_key||'-old','2026-01-01',payload_json,payload_sha256 FROM raw_records WHERE dataset='budget'")
        conn.close()
        r=analysis.analyze(self.path)
        detail=next(x for x in r['layers'] if x['source']=='QWGJK')
        self.assertEqual(detail['rows'],2)
        self.assertEqual(detail['collected_business_amount_sums']['bdg_cash_amt'],'2000000')
    def test_unknown_amount_and_large_integer_are_preserved(self):
        self.assertIsNone(analysis.amount(''))
        self.assertIsNone(analysis.amount('NaN'))
        self.assertIsNone(analysis.amount(True))
        self.assertEqual(analysis.amount('9,007,199,254,740,993'),Decimal('9007199254740993'))
    def test_html_escapes_source_text(self):
        r=analysis.analyze(self.path)
        r['layers'][0]['top_rows'][0]['project']='<script>alert(1)</script>'
        output=analysis.render(r)
        self.assertNotIn('<script>alert(1)</script>',output)
        self.assertIn('&lt;script&gt;',output)
    def test_missing_db_shows_no_synthetic_analysis(self):
        r=analysis.analyze(self.case.root/'missing.sqlite3')
        self.assertEqual(r['rows'],0)
        self.assertEqual(r['api_calls'],0)
        self.assertIn('시험 자료는 표시하지 않습니다',analysis.render(r))
    def test_schedule_requires_both_services_and_same_runtime_proof(self):
        root=self.case.root
        (root/'runtime-manifest.json').write_text('{}')
        with patch.object(scheduled,'ROOT',root):
            with self.assertRaisesRegex(ValueError,'RUNTIME_MISMATCH'): scheduled.require_live_proof(root)
            plan=root/'first/plan.json'
            data=json.loads(plan.read_text());data['runtime_manifest_sha256']=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest()
            plan.write_text(json.dumps(data))
            scheduled.require_live_proof(root)
            with self.case.conn: self.case.conn.execute("UPDATE calls SET verified_rows=0 WHERE service='QWGJK'")
            with self.assertRaisesRegex(ValueError,'BOTH_BUDGET_SOURCES'): scheduled.require_live_proof(root)

    def test_updated_orchestrator_reuses_only_identical_verified_collector_core(self):
        root=self.case.root/'current';previous=self.case.root/'previous';root.mkdir();previous.mkdir()
        old={'files':{'lofin_candidate/lofin_vnext_http.py':'core-sha','lofin_collection.py':'old'}}
        new={'files':{'lofin_candidate/lofin_vnext_http.py':'core-sha','lofin_collection.py':'new'}}
        (previous/'runtime-manifest.json').write_text(json.dumps(old))
        digest=hashlib.sha256((previous/'runtime-manifest.json').read_bytes()).hexdigest()
        (root/'runtime-manifest.json').write_text(json.dumps(new))
        with patch.object(scheduled,'ROOT',root),patch.object(scheduled,'PREVIOUS',previous):
            self.assertTrue(scheduled.compatible_proof(digest))
            new['files']['lofin_candidate/lofin_vnext_http.py']='changed-core'
            (root/'runtime-manifest.json').write_text(json.dumps(new))
            self.assertFalse(scheduled.compatible_proof(digest))


if __name__=='__main__': unittest.main(verbosity=2)
