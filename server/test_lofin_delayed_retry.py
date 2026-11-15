"""LOFIN recovery runs in its own process, never sharing G2B source modules."""
import unittest
from datetime import timedelta
from unittest.mock import patch
import lofin_collection as lofin
import test_lofin_collection as fixtures
import delayed_retry as retry


class LofinDelayedRetryTests(unittest.TestCase):
    def test_failed_checkpoint_resumes_after_delay_and_other_source_continues(self):
        case=fixtures.LofinTests();case.setUp()
        clock=lofin.now().replace(hour=12,minute=0,second=0,microsecond=0)
        try:
            with patch.object(lofin,'now',return_value=clock):
                case.invoke(fixtures.Fixture(error=True),'lofin-failed',max_pages=1,delayed=True)
            with patch.object(lofin,'now',return_value=clock+timedelta(minutes=5)):
                other=fixtures.Fixture(total=2);case.invoke(other,'other',max_pages=1,delayed=True)
            self.assertEqual(other.calls,[('QWGJK',1)])
            with patch.object(lofin,'now',return_value=clock+timedelta(minutes=15)):
                resumed=fixtures.Fixture(total=2);case.invoke(resumed,'lofin-recovered',max_pages=1,delayed=True)
            self.assertEqual(resumed.calls,[('AIDFA',1)])
            proof=lofin.readback(case.root/'budget-2026.sqlite3','AIDFA','2026')
            self.assertTrue(proof['complete']);self.assertEqual(proof['saved'],2)
            self.assertEqual(case.conn.execute('SELECT COUNT(*) FROM delayed_checkpoint_restarts').fetchone()[0],1)
            self.assertFalse(lofin.unresolved(case.conn));retry.validate_archives(case.conn)
        finally:case.tearDown()


if __name__=='__main__':unittest.main()
