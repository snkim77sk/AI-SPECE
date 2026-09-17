"""Bounded one-day/one-page source probes on fresh temporary SQLite.

This script is intentionally separate from production scheduling.  It may issue a
small, hard-bounded number of live read requests only when ``--allow-live`` is
explicitly supplied and repository secrets are present.
"""
from __future__ import annotations

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

G2B_PROBE_COUNT = 6
G2B_LOOKBACK_DAYS = 3
G2B_MAX_HTTP_REQUESTS = G2B_PROBE_COUNT * G2B_LOOKBACK_DAYS
LOFIN_MAX_HTTP_REQUESTS = 1
PAGE_SIZE = 10


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true")
    return parser


def run_bounded_canary(*, allow_live=False, now=None):
    out = ROOT / "verification"
    out.mkdir(exist_ok=True)
    now = now or dt.datetime.now(ZoneInfo("Asia/Seoul"))
    day = now.date() - dt.timedelta(days=1)

    # Override path before any application import; never reuse a serving DB.
    with tempfile.TemporaryDirectory(prefix="g2b-safe-canary-") as temp:
        os.environ["G2B_DB_PATH"] = str(Path(temp) / "canary.sqlite3")
        os.environ["G2B_AUTO_SYNC"] = "0"
        os.environ["G2B_VNEXT_API_DAILY_LIMIT"] = str(G2B_MAX_HTTP_REQUESTS)
        os.environ["LOFIN_VNEXT_API_DAILY_LIMIT"] = str(LOFIN_MAX_HTTP_REQUESTS)

        import db
        import budget_snapshot_vnext
        import g2b_vnext_canary
        import vnext_http

        db.init_db()
        if len(g2b_vnext_canary.CANARY_DATASETS) != G2B_PROBE_COUNT:
            raise RuntimeError("bounded canary probe count drift detected")

        report = {
            "production_db_touched": False,
            "main_merge_hold": True,
            "bulk_collection_attempted": False,
            "approval_scope": "bounded sample identity+schema+fact only",
            "python": sys.version,
            "live_allowed_for_this_invocation": bool(allow_live),
            "probe_day_kst": day.isoformat(),
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "g2b_probe_count": G2B_PROBE_COUNT,
            "g2b_lookback_days": G2B_LOOKBACK_DAYS,
            "g2b_max_http_requests": G2B_MAX_HTTP_REQUESTS,
            "g2b_page_size": PAGE_SIZE,
            "lofin_max_http_requests": LOFIN_MAX_HTTP_REQUESTS,
        }

        if not allow_live:
            report["g2b"] = report["budget"] = {
                "status": "NOT_REQUESTED",
                "live_request_attempted": False,
            }
        else:
            if os.getenv("G2B_SERVICE_KEY", "").strip():
                # retries=1 means one HTTP attempt per logical one-day/one-page probe.
                for module in (
                    g2b_vnext_canary.bid_vnext,
                    g2b_vnext_canary.award_vnext,
                    g2b_vnext_canary.contract_vnext,
                    g2b_vnext_canary.shopping_vnext,
                ):
                    module._request = functools.partial(vnext_http.request, retries=1, timeout=20)
                report["g2b"] = g2b_vnext_canary.run_canary(
                    today=day,
                    rows=PAGE_SIZE,
                    lookback_days=G2B_LOOKBACK_DAYS,
                )
            else:
                report["g2b"] = {
                    "status": "BLOCKED",
                    "reason": "G2B_SERVICE_KEY_NOT_CONFIGURED",
                    "live_request_attempted": False,
                }

            report["budget"] = budget_snapshot_vnext.run_budget_canary(
                snapshot_date=day,
                rows=PAGE_SIZE,
            )

        report["all_sample_schemas_verified"] = (
            report["g2b"].get("status") == "CONCLUSIVE"
            and report["budget"].get("schema_verified") is True
        )
        report["whole_source_completeness_verified"] = False
        text = json.dumps(report, ensure_ascii=False, indent=2)
        (out / "canary.json").write_text(text + "\n", encoding="utf-8")
        return report


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = run_bounded_canary(allow_live=args.allow_live)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
