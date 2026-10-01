"""One-page live QWGJK deployment canary for G2B vNext 4.x.

This command intentionally touches the configured production budget PostgreSQL store,
but it is hard-capped to one logical QWGJK page for the current KST date. It never
collects shopping, service, bid, award, contract, AIDFA, or education sources.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import budget_pg_store
import budget_storage
import budget_vnext
import lofin_vnext_http
import vnext_source_guard

PAGE_SIZE = 1000
MAX_PAGES = 1
MAX_SOURCE_REQUESTS = 4
KST = ZoneInfo("Asia/Seoul")


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument(
        "--date",
        default="",
        help="current KST date only; omitted means today",
    )
    return parser


def _today_kst():
    return dt.datetime.now(KST).date()


def _snapshot_day(value):
    today = _today_kst()
    if str(value or "").strip():
        try:
            day = dt.date.fromisoformat(str(value).strip())
        except ValueError:
            raise RuntimeError(
                "DEPLOYMENT_BUDGET_CANARY_DATE_INVALID"
            ) from None
    else:
        day = today
    if day != today:
        raise RuntimeError(
            "DEPLOYMENT_BUDGET_CANARY_CURRENT_KST_DATE_REQUIRED"
        )
    return day


def _safe_error_code(exc):
    message = str(exc or "").strip()
    if isinstance(exc, RuntimeError) and (
        message.startswith("DEPLOYMENT_BUDGET_CANARY_")
        or message.startswith("BUDGET_POSTGRES_")
        or message.startswith("G2B_BUDGET_")
        or message.startswith("VNEXT_")
    ):
        return message[:180]
    return type(exc).__name__


def run_canary(*, allow_live=False, snapshot_date=""):
    if allow_live is not True:
        raise RuntimeError("DEPLOYMENT_BUDGET_CANARY_LIVE_LOCKED")

    day = _snapshot_day(snapshot_date)
    os.environ["G2B_AUTO_SYNC"] = "0"

    if budget_storage.backend_name() != "POSTGRESQL":
        raise RuntimeError(
            "DEPLOYMENT_BUDGET_CANARY_POSTGRESQL_REQUIRED"
        )
    if not budget_storage.storage_configured():
        raise RuntimeError("BUDGET_POSTGRES_NOT_CONFIGURED")
    if not budget_storage.storage_ready():
        raise RuntimeError(
            budget_storage.storage_error_code()
            or "DEPLOYMENT_BUDGET_CANARY_POSTGRES_NOT_READY"
        )
    if not lofin_vnext_http.get_lofin_key():
        raise RuntimeError("DEPLOYMENT_BUDGET_CANARY_LOFIN_KEY_REQUIRED")

    context_after = {}
    with vnext_source_guard.operational_budget_source_context(
        snapshot_date=day.isoformat(),
        max_requests=MAX_SOURCE_REQUESTS,
    ):
        result = budget_vnext.collect_full_budget(
            day.year,
            day.isoformat(),
            page_size=PAGE_SIZE,
            max_pages=MAX_PAGES,
            resume=True,
        )
        context_after = (
            vnext_source_guard.current_source_request_context() or {}
        )

    scope = f"{day.year}:{day.isoformat()}"
    checkpoint = budget_pg_store.get_checkpoint("budget", scope) or {}
    collector = {
        "status": str(result.get("status") or ""),
        "complete": bool(result.get("complete")),
        "resumed": bool(result.get("resumed")),
        "fetched": int(result.get("fetched") or 0),
        "saved": int(result.get("saved") or 0),
        "source_total": result.get("source_total"),
        "completion_reason": str(
            result.get("completion_reason") or ""
        ),
    }
    checkpoint_safe = {
        "scope": scope,
        "status": str(checkpoint.get("status") or ""),
        "page_no": int(checkpoint.get("page_no") or 0),
        "page_size": int(checkpoint.get("page_size") or 0),
        "fetched_count": int(checkpoint.get("fetched_count") or 0),
        "saved_count": int(checkpoint.get("saved_count") or 0),
        "source_total": (
            None
            if int(checkpoint.get("source_total") or -1) < 0
            else int(checkpoint.get("source_total") or 0)
        ),
    }
    return {
        "canary_scope": "QWGJK_CURRENT_KST_DATE_ONE_LOGICAL_PAGE_MAX",
        "snapshot_date_kst": day.isoformat(),
        "fiscal_year": day.year,
        "page_size": PAGE_SIZE,
        "max_pages": MAX_PAGES,
        "max_source_requests": MAX_SOURCE_REQUESTS,
        "source_io_performed": int(
            context_after.get("requests_used") or 0
        ) > 0,
        "source_requests_used": int(
            context_after.get("requests_used") or 0
        ),
        "transport_successes": int(
            context_after.get("transport_successes_used") or 0
        ),
        "budget_postgres_ready": True,
        "collector": collector,
        "checkpoint": checkpoint_safe,
        "resume_expected": bool(
            not collector["complete"]
            and checkpoint_safe["status"] == "RUNNING"
        ),
        "source_collection_completeness_verified": False,
        "other_sources_touched": False,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = run_canary(
            allow_live=args.allow_live,
            snapshot_date=args.date,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "FAILED",
            "error_code": _safe_error_code(exc),
            "source_collection_completeness_verified": False,
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
