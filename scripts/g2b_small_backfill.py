"""One-day, page-bounded historical validation on disposable SQLite only.

This is a validation harness, not a production scheduler.  It requires a recent
same-commit bounded-canary approval and never permits an arbitrary database path.
Reports contain only aggregate counts/status, never raw source rows or credentials.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VERIFY = (ROOT / "verification").resolve()
sys.path.insert(0, str(ROOT))

MAX_PAGES = 2
G2B_PAGE_SIZE = 999
BUDGET_PAGE_SIZE = 1000


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--approval", default=str(VERIFY / "canary.json"))
    parser.add_argument("--date", default="")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    return parser


def _day(value):
    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date()
    day = dt.date.fromisoformat(str(value).strip()) if str(value).strip() else today - dt.timedelta(days=1)
    if day >= today:
        raise ValueError("validation backfill requires a completed past KST date")
    return day


def _validation_db():
    raw = str(os.getenv("G2B_DB_PATH", "") or "").strip()
    if not raw:
        raise RuntimeError("VALIDATION_DB_PATH_REQUIRED")
    path = Path(raw).resolve()
    if path.parent != VERIFY or path.name != "small_backfill.sqlite3":
        raise RuntimeError("VALIDATION_DB_PATH_UNSAFE")
    VERIFY.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    return path


def run(*, allow_live=False, approval=None, date_value="", max_pages=MAX_PAGES):
    if not allow_live:
        raise RuntimeError("SMALL_BACKFILL_LIVE_LOCKED")
    pages = int(max_pages)
    if pages < 1 or pages > MAX_PAGES:
        raise ValueError(f"max_pages must be between 1 and {MAX_PAGES}")
    day = _day(date_value)
    db_path = _validation_db()
    os.environ["G2B_AUTO_SYNC"] = "0"
    # This validation run is intentionally bounded even after canary approval.
    os.environ["G2B_VNEXT_API_DAILY_LIMIT"] = "60"
    os.environ["LOFIN_VNEXT_API_DAILY_LIMIT"] = "10"

    import db
    import award_vnext
    import bid_vnext
    import budget_snapshot_vnext
    import budget_vnext
    import contract_vnext
    import historical_vnext
    import lofin_vnext_http
    import shopping_vnext
    import vnext_http

    db.init_db()
    # One physical HTTP attempt per logical read; receipt replay is still required.
    g2b_request = functools.partial(vnext_http.request, retries=1, timeout=30)
    for module in (bid_vnext, award_vnext, contract_vnext, shopping_vnext):
        module._request = g2b_request
    budget_vnext.fetch_budget_page = functools.partial(
        lofin_vnext_http.fetch_budget_page, retries=1
    )

    approval_path = str(approval or VERIFY / "canary.json")
    date_text = day.isoformat()
    g2b = historical_vnext.run_backfill(
        date_text,
        date_text,
        chunk_days=1,
        page_size=G2B_PAGE_SIZE,
        max_pages_per_stage=pages,
        allow_live=True,
        canary_approval=approval_path,
        stop_on_incomplete=True,
    )
    budget = budget_snapshot_vnext.run_snapshots(
        [date_text],
        allow_live=True,
        canary_approval=approval_path,
        page_size=BUDGET_PAGE_SIZE,
        max_pages_per_snapshot=pages,
    )
    budget_audit = budget.get("audit") or {}
    requested_scope_complete = bool(
        g2b.get("complete") and budget_audit.get("all_requested_snapshots_complete")
    )
    report = {
        "validation_only": True,
        "validation_scope": "one completed KST date only",
        "production_db_touched": False,
        "db_artifact_exported": False,
        "date_kst": date_text,
        "max_pages_per_stage": pages,
        "g2b_page_size": G2B_PAGE_SIZE,
        "budget_page_size": BUDGET_PAGE_SIZE,
        "g2b_complete": bool(g2b.get("complete")),
        "g2b_stopped_on": g2b.get("stopped_on"),
        "g2b_raw_row_counts": historical_vnext.raw_row_counts(),
        "g2b_audit": g2b.get("audit") or historical_vnext.audit_backfill(date_text, date_text, chunk_days=1),
        "budget_audit": budget_audit,
        "requested_validation_scope_complete": requested_scope_complete,
        # A one-day validation can prove only that explicitly requested scope.  It
        # must never be promoted to a claim about the complete upstream archive.
        "whole_source_completeness_verified": False,
        "validation_db_path": db_path.name,
    }
    out = VERIFY / "small_backfill_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = run(
        allow_live=args.allow_live,
        approval=args.approval,
        date_value=args.date,
        max_pages=args.max_pages,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
