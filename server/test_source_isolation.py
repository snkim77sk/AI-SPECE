"""Offline-only review: independent keys, state, and real Windows child-process failures."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from source_profiles import PROFILES,KST,source_readiness,child_environment
sys.path.insert(0,str(Path(__file__).resolve().parent/'g2b_recovery_candidate'))
from windows_limits import WindowsJob


def no_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise AssertionError('REAL_NETWORK_FORBIDDEN')
sys.addaudithook(no_network)


class SourceIsolationTests(unittest.TestCase):
    def test_credentials_never_cross_child_boundaries(self):
        parent={'G2B_SERVICE_KEY':'FAKE-G2B','LOFIN_API_KEY':'FAKE-LOFIN','OTHER_SETTING':'kept'}
        g2b=child_environment('g2b',parent,'FAKE-G2B-ONLY')
        lofin=child_environment('lofin',parent,'FAKE-LOFIN-ONLY')
        self.assertNotIn('LOFIN_API_KEY',g2b)
        self.assertNotIn('G2B_SERVICE_KEY',lofin)
        self.assertEqual(g2b['OTHER_SETTING'],'kept')
        self.assertEqual(lofin['OTHER_SETTING'],'kept')
        self.assertNotEqual(PROFILES['g2b'].state_root,PROFILES['lofin'].state_root)
        self.assertNotEqual(PROFILES['g2b'].encrypted_credential,PROFILES['lofin'].encrypted_credential)

    def test_lofin_maintenance_or_hold_does_not_stop_g2b(self):
        clock=datetime(2026,11,14,23,0,tzinfo=KST)
        self.assertEqual(source_readiness('lofin',clock,transport_verified=False),'MAINTENANCE')
        self.assertEqual(source_readiness('g2b',clock,transport_verified=True),'READY')
        after=datetime(2026,11,15,0,0,tzinfo=KST)
        self.assertEqual(source_readiness('lofin',after,source_hold=True),'HOLD')
        self.assertEqual(source_readiness('g2b',after,transport_verified=True),'READY')
        self.assertEqual(source_readiness('lofin',after),'FIRST_RESPONSE_VERIFICATION_REQUIRED')

    def test_failed_or_timed_out_lofin_process_does_not_stop_g2b_save(self):
        for failure in ('crash','hang'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory(prefix='g2b-isolation-') as temp:
                root=Path(temp)
                good_db=root/'g2b-fixture.sqlite3'
                completion_marker=root/'lofin-finished.txt'
                good_code="import sqlite3,sys,time; sys.stdin.buffer.readline(); time.sleep(0.1); c=sqlite3.connect(sys.argv[1]); c.execute('CREATE TABLE fixture(value INTEGER)'); c.execute('INSERT INTO fixture VALUES(7)'); c.commit(); c.close()"
                bad_code="import os,sys,time; from pathlib import Path; sys.stdin.buffer.readline(); "+(
                    "os._exit(77)" if failure=='crash' else "time.sleep(10); Path(sys.argv[1]).write_text('completed')")
                children=[]
                timed_out=[]
                try:
                    for source,code,argv in [('g2b',good_code,[str(good_db)]),('lofin',bad_code,[str(completion_marker)])]:
                        process=subprocess.Popen([sys.executable,'-B','-c',code,*argv],stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                        job=WindowsJob(process,64)
                        children.append((source,process,job))
                        process.stdin.write(b'GO\n');process.stdin.flush();process.stdin.close()
                    deadline=time.monotonic()+2
                    while any(p.poll() is None for _,p,_ in children) and time.monotonic()<deadline:
                        time.sleep(0.02)
                    for source,process,job in children:
                        if process.poll() is None:
                            timed_out.append(source)
                            job.close()
                        process.wait(timeout=3)
                    self.assertEqual(children[0][1].returncode,0)
                    if failure=='crash':
                        self.assertEqual(children[1][1].returncode,77)
                    else:
                        # Windows job termination can return 0: require deadline and completion evidence.
                        self.assertIn('lofin',timed_out)
                        self.assertFalse(completion_marker.exists())
                    conn=sqlite3.connect(good_db.as_uri()+'?mode=ro',uri=True)
                    try:
                        self.assertEqual(conn.execute('SELECT value FROM fixture').fetchone()[0],7)
                    finally:
                        conn.close()
                finally:
                    for _,process,job in children:
                        job.close()
                        if process.poll() is None:
                            process.kill();process.wait(timeout=3)


if __name__=='__main__':
    unittest.main(verbosity=2)
