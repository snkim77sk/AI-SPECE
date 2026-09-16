"""One-day, one-page read probes on fresh temporary SQLite; no production writes."""
import argparse
import datetime as dt
import functools
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--allow-live', action='store_true')
args = parser.parse_args()
out = ROOT/'verification'
out.mkdir(exist_ok=True)
# Override path before any application import; never reuse a serving DB.
with tempfile.TemporaryDirectory(prefix='g2b-safe-canary-') as temp:
    os.environ['G2B_DB_PATH'] = str(Path(temp)/'canary.sqlite3')
    os.environ['G2B_AUTO_SYNC'] = '0'
    os.environ['G2B_VNEXT_API_DAILY_LIMIT'] = '6'
    os.environ['LOFIN_VNEXT_API_DAILY_LIMIT'] = '1'
    import db
    import budget_snapshot_vnext
    import g2b_vnext_canary
    import vnext_http
    db.init_db()
    report = {'production_db_touched': False, 'main_merge_hold': True,
              'bulk_collection_attempted': False, 'approval_scope': 'sample schema only',
              'python': sys.version, 'live_allowed_for_this_invocation': args.allow_live}
    day = dt.datetime.now(ZoneInfo('Asia/Seoul')).date()-dt.timedelta(days=1)
    report['probe_day_kst'] = day.isoformat()
    report['generated_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    if not args.allow_live:
        report['g2b'] = report['budget'] = {'status':'NOT_REQUESTED','live_request_attempted':False}
    else:
        if os.getenv('G2B_SERVICE_KEY','').strip():
            # One HTTP attempt per logical probe, at most six requests total.
            for module in (g2b_vnext_canary.bid_vnext, g2b_vnext_canary.award_vnext,
                           g2b_vnext_canary.contract_vnext, g2b_vnext_canary.shopping_vnext):
                module._request = functools.partial(vnext_http.request, retries=1, timeout=20)
            report['g2b'] = g2b_vnext_canary.run_canary(today=day, rows=10, lookback_days=1)
        else:
            report['g2b'] = {'status':'BLOCKED','reason':'G2B_SERVICE_KEY_NOT_CONFIGURED','live_request_attempted':False}
        report['budget'] = budget_snapshot_vnext.run_budget_canary(snapshot_date=day, rows=10)
    report['all_sample_schemas_verified'] = (report['g2b'].get('status')=='CONCLUSIVE'
                                             and report['budget'].get('schema_verified') is True)
    report['whole_source_completeness_verified'] = False
    (out/'canary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
