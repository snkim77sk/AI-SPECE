import hashlib
import sqlite3
import unittest
import test_lofin_collection as fixture
import lofin_viewer as viewer


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.case=fixture.LofinTests();self.case.setUp()
        self.case.invoke(fixture.Fixture(total=3),max_pages=2)
        self.path=self.case.root/'budget-2026.sqlite3'
    def tearDown(self): self.case.tearDown()
    def test_full_saved_rows_are_searchable_and_paginated(self):
        page=viewer.read_rows(self.path,limit=2)
        self.assertEqual((page['total'],len(page['rows']),page['has_more']),(3,2,True))
        self.assertEqual(len(viewer.read_rows(self.path,offset=2,limit=2)['rows']),1)
        found=viewer.read_rows(self.path,query='시험사업 1')
        self.assertEqual(found['total'],1)
        self.assertEqual(found['rows'][0]['raw']['dbiz_nm'],'시험사업 1')
    def test_search_is_literal_and_cannot_change_sql_scope(self):
        for query in ("' OR 1=1 --",'사업%1', '사업_1'):
            self.assertEqual(viewer.read_rows(self.path,query=query)['total'],0)
        with self.assertRaises(ValueError): viewer.read_rows(self.path,dataset='app_settings')
        with self.assertRaises(ValueError): viewer.read_rows(self.path,limit=101)
        with self.assertRaises(ValueError): viewer.read_rows(self.path,offset=-1)
    def test_viewer_preserves_db_and_never_calls_source(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        page=viewer.read_rows(self.path,dataset='budget_appropriation')
        self.assertEqual(page['source_api_calls'],0)
        self.assertTrue(page['read_only'])
        self.assertEqual(page['rows'][0]['administration'],'10000')
        self.assertIsNone(page['rows'][0]['executed'])
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_wal_reader_snapshot_does_not_block_collector_commit(self):
        setup=sqlite3.connect(self.path)
        self.assertEqual(setup.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
        setup.close()
        reader=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)
        try:
            reader.execute('PRAGMA query_only=ON');reader.execute('BEGIN')
            before=reader.execute('SELECT COUNT(*) FROM raw_records').fetchone()[0]
            writer=sqlite3.connect(self.path,timeout=0.1)
            with writer: writer.execute("INSERT INTO app_settings(key,value) VALUES('viewer-concurrency-test','committed')")
            writer.close()
            self.assertEqual(reader.execute('SELECT COUNT(*) FROM raw_records').fetchone()[0],before)
            self.assertIsNone(reader.execute("SELECT value FROM app_settings WHERE key='viewer-concurrency-test'").fetchone())
        finally: reader.close()
        after=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)
        self.assertEqual(after.execute("SELECT value FROM app_settings WHERE key='viewer-concurrency-test'").fetchone()[0],'committed')
        after.close()


if __name__=='__main__': unittest.main(verbosity=2)
