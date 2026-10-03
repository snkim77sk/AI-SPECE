"""Conditional 2025 validation backfill for budget -> shopping pattern evidence.

This module is deliberately narrower than generic historical collection:
* QWGJK only for the 2025 fiscal year,
* shopping-delivery only for 2025, retaining LED/pole product codes,
* no AIDFA, bid, service, award or contract source traffic,
* no current-state replacement for the historical QWGJK snapshot,
* global process lease + existing per-source daily quota gates remain authoritative.
"""
from __future__ import annotations

import datetime as dt

import budget_storage
import budget_vnext
import g2b_database
import shopping_vnext
from db import connect
from vnext_source_guard import (
    match_backfill_budget_source_context,
    match_backfill_shopping_source_context,
)
from vnext_store import get_checkpoint

BACKFILL_YEAR = 2025
SHOPPING_START = dt.date(2025, 1, 1)
SHOPPING_END = dt.date(2025, 12, 31)
BUDGET_SNAPSHOT = dt.date(2025, 12, 31)
DEFAULT_SHOPPING_DAYS_PER_RUN = 7
MAX_SHOPPING_DAYS_PER_RUN = 14
DEFAULT_SHOPPING_MAX_PAGES = 40
DEFAULT_BUDGET_MAX_PAGES = 100


def _shopping_scope(day):
    value = day if isinstance(day, dt.date) else dt.date.fromisoformat(str(day))
    return f"match-backfill:{BACKFILL_YEAR}:{value.isoformat()}"


def _shopping_completed_days():
    prefix = f"match-backfill:{BACKFILL_YEAR}:"
    completed = set()
    with connect() as conn:
        rows = conn.execute(
            """SELECT scope_key,status
               FROM collection_checkpoints
               WHERE dataset='shopping_delivery'
                 AND scope_key LIKE ?
               ORDER BY scope_key""",
            (prefix + "%",),
        ).fetchall()
    for row in rows:
        if str(row["status"] or "").upper() != "COMPLETE":
            continue
        scope = str(row["scope_key"] or "")
        if not scope.startswith(prefix):
            continue
        try:
            day = dt.date.fromisoformat(scope[len(prefix):])
        except ValueError:
            continue
        if SHOPPING_START <= day <= SHOPPING_END:
            completed.add(day)
    return completed


def _budget_checkpoint():
    scope = f"history:{BACKFILL_YEAR}:{BUDGET_SNAPSHOT.isoformat()}"
    if budget_storage.using_postgres():
        import budget_pg_store
        return budget_pg_store.get_checkpoint("budget", scope)
    return get_checkpoint("budget", scope)


def build_2025_backfill_plan(match_summary=None):
    summary = dict(match_summary or {})
    needed = bool(summary.get("expand_2025_recommended", True))
    reasons = list(summary.get("expansion_reasons") or [])
    completed = _shopping_completed_days()
    total_days = (SHOPPING_END - SHOPPING_START).days + 1

    next_day = None
    day = SHOPPING_START
    while day <= SHOPPING_END:
        if day not in completed:
            next_day = day
            break
        day += dt.timedelta(days=1)

    budget_cp = _budget_checkpoint()
    budget_complete = bool(
        budget_cp
        and str(budget_cp.get("status") or "").upper() == "COMPLETE"
    )
    return {
        "year": BACKFILL_YEAR,
        "needed": needed,
        "reasons": reasons,
        "shopping_start": SHOPPING_START.isoformat(),
        "shopping_end": SHOPPING_END.isoformat(),
        "shopping_total_days": total_days,
        "shopping_complete_days": len(completed),
        "shopping_next_date": next_day.isoformat() if next_day else "",
        "shopping_complete": len(completed) >= total_days,
        "budget_snapshot": BUDGET_SNAPSHOT.isoformat(),
        "budget_complete": budget_complete,
        "budget_checkpoint_status": (
            str(budget_cp.get("status") or "") if budget_cp else "NOT_STARTED"
        ),
        "source_scope": "QWGJK_2025_PLUS_SHOPPING_LED_POLE_ONLY",
        "generic_historical_mode_opened": False,
    }


def _run_budget_snapshot(*, max_pages):
    cp = _budget_checkpoint()
    if cp and str(cp.get("status") or "").upper() == "COMPLETE":
        return {
            "status": "COMPLETE",
            "complete": True,
            "action": "SKIPPED_COMPLETE",
            "scope": f"history:{BACKFILL_YEAR}:{BUDGET_SNAPSHOT.isoformat()}",
        }

    with match_backfill_budget_source_context(
        snapshot_date=BUDGET_SNAPSHOT.isoformat(),
        max_requests=500,
    ):
        result = budget_vnext.collect_full_budget(
            BACKFILL_YEAR,
            BUDGET_SNAPSHOT.isoformat(),
            page_size=1000,
            max_pages=max(1, min(int(max_pages), 500)),
            resume=True,
            advance_current=False,
        )
    return {
        **dict(result or {}),
        "action": "COLLECTED_OR_RESUMED",
        "historical_match_backfill": True,
    }


def _run_shopping_days(*, max_days, max_pages):
    completed = _shopping_completed_days()
    wanted = max(
        1,
        min(int(max_days), MAX_SHOPPING_DAYS_PER_RUN),
    )
    results = []
    day = SHOPPING_START
    while day <= SHOPPING_END and len(results) < wanted:
        if day in completed:
            day += dt.timedelta(days=1)
            continue
        with match_backfill_shopping_source_context(
            collection_date=day.isoformat(),
            max_requests=64,
        ):
            result = shopping_vnext.collect_match_backfill_day(
                day.isoformat(),
                page_size=999,
                max_pages=max(1, min(int(max_pages), 64)),
                resume=True,
            )
        item = {
            **dict(result or {}),
            "source_date": day.isoformat(),
            "scope": _shopping_scope(day),
        }
        results.append(item)
        if item.get("complete") is not True:
            break
        completed.add(day)
        day += dt.timedelta(days=1)
    return results


def run_2025_backfill(
    match_summary,
    *,
    shopping_days=DEFAULT_SHOPPING_DAYS_PER_RUN,
    shopping_max_pages=DEFAULT_SHOPPING_MAX_PAGES,
    budget_max_pages=DEFAULT_BUDGET_MAX_PAGES,
    force=False,
):
    """Run one bounded 2025 validation-backfill cycle when 2026 evidence is weak."""
    before = build_2025_backfill_plan(match_summary)
    if not before["needed"] and not bool(force):
        return {
            "status": "SKIPPED_EVIDENCE_SUFFICIENT",
            "before": before,
            "after": before,
            "source_traffic": False,
        }

    with g2b_database.operational_cycle_lease(
        "g2b_v41_operational_cycle",
        shared=False,
    ) as acquired:
        if not acquired:
            return {
                "status": "LEASE_HELD",
                "before": before,
                "after": before,
                "source_traffic": False,
            }

        budget_result = _run_budget_snapshot(max_pages=budget_max_pages)
        shopping_results = _run_shopping_days(
            max_days=shopping_days,
            max_pages=shopping_max_pages,
        )

    after = build_2025_backfill_plan(match_summary)
    return {
        "status": (
            "COMPLETE"
            if after["budget_complete"] and after["shopping_complete"]
            else "PARTIAL"
        ),
        "before": before,
        "after": after,
        "budget": budget_result,
        "shopping": shopping_results,
        "source_traffic": True,
        "generic_historical_mode_opened": False,
    }
