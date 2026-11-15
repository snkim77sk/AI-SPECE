"""Fixture worker. An audit hook blocks every real socket connection/DNS lookup."""
import sys


def deny_network(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise RuntimeError('NETWORK_FORBIDDEN_OFFLINE')


sys.addaudithook(deny_network)
if __name__=='__main__':
    # Wait before imports, DB creation or fixture activity until the Job is assigned.
    if sys.stdin.buffer.readline()!=b'GO\n':
        raise SystemExit('SUPERVISOR_HANDSHAKE_REQUIRED')
    import json
    import time
    import tracemalloc
    from datetime import datetime,timedelta
    from bounded_runner import Ledger,run_once,isolated_path,KST
    from offline_fixtures import FixtureSource
    directory=isolated_path(sys.argv[1])
    scenario=sys.argv[2]
    if scenario=='memory':
        allocated=[]
        try:
            while True:
                allocated.append(bytearray(4*1024*1024))
        except MemoryError:
            (directory/'memory-limit.json').write_text(json.dumps({'allocation_denied':True,'live_calls':0}))
            raise SystemExit(0)
    now=datetime.now(KST)
    day=(now.date()-timedelta(days=1)).isoformat()
    ledger_path=directory/'ledger.sqlite3'
    ledger=Ledger(ledger_path)
    if not ledger.conn.execute('SELECT 1 FROM usage WHERE day=?',(now.date().isoformat(),)).fetchone():
        ledger.fixture_budget(now.date().isoformat())
    ledger.close()
    behavior={1:scenario} if scenario in ('hang','crash') else None
    fixture=FixtureSource(total=999*10,payload_bytes=2048,behaviors=behavior)
    tracemalloc.start()
    result=run_once(db_path=directory/'collector.sqlite3',ledger_path=ledger_path,days=[day],
        hold_path=directory/'HOLD.json',run_id=scenario,fixture=fixture)
    result['fixture_transport_calls']=len(fixture.calls)
    (directory/'worker-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
