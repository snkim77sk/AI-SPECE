"""Operational newest-first shopping delivery collection.

This pathway is intentionally narrower than historical backfill:
- only the official shopping delivery-detail endpoint,
- exactly one calendar day per source context,
- newest day first,
- bounded page/request budgets,
- RAW preservation before post-classification,
- no unlock of APPROVED_HISTORICAL.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import classification_vnext
import shopping_vnext
from db import set_setting
from vnext_source_guard import operational_recent_source_context
from vnext_store import get_checkpoint

KST = ZoneInfo("Asia/Seoul")
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_MAX_DAYS_PER_RUN = 14
DEFAULT_PAGE_SIZE = 999
DEFAULT_MAX_PAGES_PER_DAY = 40
DEFAULT_REQUEST_BUDGET_PER_DAY = 64


def _as_day(value=None):
    if value is None:
        return dt.datetime.now(KST).date()
    if isinstance(value, dt.datetime):
        return value.astimezone(KST).date() if value.tzinfo else value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def _status(name, value):
    set_setting(f"shopping_recent_{name}", value)


def _resume_for_day(day, today):
    """Refresh a completed current day; otherwise continue the committed prefix."""
    scope = f"{day.isoformat()}:{day.isoformat()}"
    cp = get_checkpoint(shopping_vnext.DATASET, scope)
    if not cp:
        return True
    if day == today and str(cp.get("status") or "") == "COMPLETE":
        return False
    return True


def collect_latest_first(
    *,
    today=None,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    max_days=DEFAULT_MAX_DAYS_PER_RUN,
    page_size=DEFAULT_PAGE_SIZE,
    max_pages_per_day=DEFAULT_MAX_PAGES_PER_DAY,
    request_budget_per_day=DEFAULT_REQUEST_BUDGET_PER_DAY,
):
    """Collect recent shopping delivery RAW newest-to-oldest and post-classify it.

    A day that cannot finish within the bounded page budget stops the descent. The
    next run resumes that same day before any older day is attempted.
    """
    current = _as_day(today)
    lookback = max(1, min(int(lookback_days), 31))
    day_budget = max(1, min(int(max_days), lookback))
    page_size = max(1, min(int(page_size), 999))
    max_pages = max(1, int(max_pages_per_day))
    request_budget = max(1, min(int(request_budget_per_day), 64))

    started = dt.datetime.now(KST)
    _status("state", "RUNNING")
    _status("last_started_at_kst", started.isoformat(timespec="seconds"))
    _status("last_error", "")
    _status("order", "NEWEST_FIRST")
    _status("lookback_days", lookback)

    results = []
    classification = None
    try:
        for offset in range(day_budget):
            day = current - dt.timedelta(days=offset)
            if offset >= lookback:
                break
            iso = day.isoformat()
            _status("current_date", iso)
            resume = _resume_for_day(day, current)
            with operational_recent_source_context(
                collection_date=iso,
                max_requests=request_budget,
            ):
                result = shopping_vnext.collect_all(
                    iso,
                    iso,
                    page_size=page_size,
                    max_pages=max_pages,
                    resume=resume,
                )
            results.append({"date": iso, **result})
            # RAW is never filtered during collection. Keep the customer-facing
            # shopping view current by classifying new/changed RAW immediately.
            classification = classification_vnext.classify_dataset(
                shopping_vnext.DATASET,
                batch_size=1000,
            )
            if not result.get("complete"):
                _status("state", "PARTIAL")
                _status("last_completed_date", "")
                return {
                    "status": "PARTIAL",
                    "order": "NEWEST_FIRST",
                    "today": current.isoformat(),
                    "results": results,
                    "classification": classification,
                }
            _status("last_completed_date", iso)

        finished = dt.datetime.now(KST)
        _status("state", "COMPLETE")
        _status("last_finished_at_kst", finished.isoformat(timespec="seconds"))
        return {
            "status": "COMPLETE",
            "order": "NEWEST_FIRST",
            "today": current.isoformat(),
            "results": results,
            "classification": classification,
        }
    except Exception as exc:
        _status("state", "FAILED")
        _status("last_error", type(exc).__name__)
        _status(
            "last_finished_at_kst",
            dt.datetime.now(KST).isoformat(timespec="seconds"),
        )
        raise
