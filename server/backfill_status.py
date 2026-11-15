"""Read-only range inventory and prior-page preservation evidence. No API calls."""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
import live_collection_once as live
import backfill_queue
import audit_existing

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'evidence'
anchor=live.BASE/'live-bounded-20261114'
hold=json.loads(live.HOLD.read_text(encoding='utf-8'))
end=(live.now().date()-live.timedelta(days=1)).isoformat()
jobs=backfill_queue.inventory(start='2026-01-01',end=end,held_day=hold['source_day'],
    held_page=hold['next_page'],held_saved=hold['verified_saved'],
    original_db=live.BASE/'data/g2b-local.sqlite3',live_root=anchor)
with closing(sqlite3.connect((anchor/'live-calls.sqlite3').as_uri()+'?mode=ro',uri=True)) as conn:
    counts=[{'budget_day':row[0],'reserved':row[1],'verified':row[2]} for row in conn.execute(
        'SELECT budget_day,COUNT(*),SUM(verified) FROM calls GROUP BY budget_day')]
    uncertain=conn.execute("SELECT COUNT(*) FROM calls WHERE state!='VERIFIED'").fetchone()[0]
previous_pages=[]
path=anchor/'g2b-real-2026-11-13.sqlite3'
with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
    conn.row_factory=sqlite3.Row
    previous_pages=[dict(r) for r in conn.execute('SELECT * FROM vnext_collection_pages ORDER BY page_no')]
report={'observed_at_kst':live.now().isoformat(timespec='seconds'),'range_start':'2026-01-01','range_end':end,
    'total_days':len(jobs),'days_by_state':dict(Counter(j['state'] for j in jobs)),
    'next_ready':next((j for j in jobs if j['state']=='READY'),None),
    'ledger_usage':counts,'ledger_uncertain':uncertain,'jobs':jobs,
    'previous_real_page_receipts':previous_pages,'real_api_calls':0}
(OUT/'backfill-before.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
(OUT/'original-before-backfill.json').write_text(json.dumps(audit_existing.manifest(),indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('jobs','previous_real_page_receipts')},indent=2))
if uncertain:
    raise SystemExit(1)
