"""Operational forward shopping-delivery collection.

Bootstrap rule:
- start at 2026-09-01,
- collect one calendar day at a time in ascending order,
- stop at the latest completed source day (D-1 in Korea time),
- skip checkpoints that are already structurally COMPLETE,
- retry the first failed/incomplete date before moving forward,
- preserve RAW before classification,
- never unlock the wider APPROVED_HISTORICAL mode.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import classification_vnext
import shopping_vnext
from db import set_setting
from vnext_collection import COLLECTION_VERSION
from vnext_source_guard import operational_recent_source_context
from vnext_store import get_checkpoint

KST = ZoneInfo("Asia/Seoul")
BOOTSTRAP_START_DATE = dt.date(2026, 9, 1)
LATEST_SOURCE_LAG_DAYS = 1
DEFAULT_MAX_DAYS_PER_RUN = 14
DEFAULT_PAGE_SIZE = 999
DEFAULT_MAX_PAGES_PER_DAY = 40
DEFAULT_REQUEST_BUDGET_PER_DAY = 64


def _kst_today():
    return dt.datetime.now(KST).date()


def _as_date(value):
    if isinstance(value, dt.datetime):
        return value.astimezone(KST).date() if value.tzinfo else value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def _latest_available_day(value=None):
    if value is None:
        return _kst_today() - dt.timedelta(days=LATEST_SOURCE_LAG_DAYS)
    return _as_date(value)


def _status(name, value):
    set_setting(f"shopping_recent_{name}", value)


def _scope(day):
    iso = day.isoformat()
    return f"{iso}:{iso}"


def _already_complete(day):
    """Trust a transactional terminal receipt without requiring current-RAW binding.

    A later source change may legitimately replace the current raw_records payload for
    the same logical delivery item. That must not make an older, already-finished day
    look uncollected and send the forward bootstrap back to the beginning.
    """
    cp = get_checkpoint(shopping_vnext.DATASET, _scope(day))
    if not cp or str(cp.get("status") or "") != "COMPLETE":
        return False
    try:
        meta = json.loads(str(cp.get("cursor_value") or "{}"))
    except (TypeError, ValueError):
        return False
    if not isinstance(meta, dict) or meta.get("version") != COLLECTION_VERSION:
        return False
    if not str(meta.get("completion_reason") or ""):
        return False
    try:
        fetched = int(cp.get("fetched_count") or 0)
        saved = int(cp.get("saved_count") or 0)
        page_no = int(cp.get("page_no") or 0)
    except (TypeError, ValueError):
        return False
    return fetched >= 0 and saved == fetched and page_no >= 1


def _days_forward(start_day, latest_day):
    current = start_day
    while current <= latest_day:
        yield current
        current += dt.timedelta(days=1)


def collect_forward(
    *,
    start_date=BOOTSTRAP_START_DATE,
    latest_date=None,
    max_days=DEFAULT_MAX_DAYS_PER_RUN,
    page_size=DEFAULT_PAGE_SIZE,
    max_pages_per_day=DEFAULT_MAX_PAGES_PER_DAY,
    request_budget_per_day=DEFAULT_REQUEST_BUDGET_PER_DAY,
):
    """Collect shopping delivery RAW from the oldest requested day toward D-1.

    Already verified COMPLETE days do not consume the per-run day budget. The first
    failed or incomplete date is retried and must finish before a newer date starts.
    """
    start_day = _as_date(start_date)
    latest_day = _latest_available_day(latest_date)
    if start_day > latest_day:
        return {
            "status": "COMPLETE",
            "order": "FORWARD",
            "start_date": start_day.isoformat(),
            "latest_available_date": latest_day.isoformat(),
            "results": [],
            "classification": None,
        }

    day_budget = max(1, min(int(max_days), 31))
    page_size = max(1, min(int(page_size), 999))
    max_pages = max(1, int(max_pages_per_day))
    request_budget = max(1, min(int(request_budget_per_day), 64))

    started = dt.datetime.now(KST)
    _status("state", "RUNNING")
    _status("last_started_at_kst", started.isoformat(timespec="seconds"))
    _status("last_error", "")
    _status("order", "FORWARD")
    _status("start_date", start_day.isoformat())
    _status("latest_available_date", latest_day.isoformat())

    results = []
    # Repair any previously collected-but-unclassified RAW before deciding that all
    # source dates can be skipped. This also recovers from a prior classifier failure
    # on a day whose collection checkpoint was already committed COMPLETE.
    classification = classification_vnext.classify_dataset(
        shopping_vnext.DATASET,
        batch_size=1000,
    )
    attempted = 0
    completed_this_run = set()
    try:
        for day in _days_forward(start_day, latest_day):
            if _already_complete(day):
                continue
            if attempted >= day_budget:
                break

            iso = day.isoformat()
            attempted += 1
            _status("current_date", iso)
            with operational_recent_source_context(
                collection_date=iso,
                max_requests=request_budget,
            ):
                result = shopping_vnext.collect_all(
                    iso,
                    iso,
                    page_size=page_size,
                    max_pages=max_pages,
                    resume=True,
                )
            results.append({"date": iso, **result})

            classification = classification_vnext.classify_dataset(
                shopping_vnext.DATASET,
                batch_size=1000,
            )
            if not result.get("complete"):
                _status("state", "PARTIAL")
                return {
                    "status": "PARTIAL",
                    "order": "FORWARD",
                    "start_date": start_day.isoformat(),
                    "latest_available_date": latest_day.isoformat(),
                    "results": results,
                    "classification": classification,
                }
            completed_this_run.add(day)
            _status("last_completed_date", iso)

        remaining = any(
            day not in completed_this_run and not _already_complete(day)
            for day in _days_forward(start_day, latest_day)
        )
        status = "PARTIAL" if remaining else "COMPLETE"
        finished = dt.datetime.now(KST)
        _status("state", status)
        _status("last_finished_at_kst", finished.isoformat(timespec="seconds"))
        return {
            "status": status,
            "order": "FORWARD",
            "start_date": start_day.isoformat(),
            "latest_available_date": latest_day.isoformat(),
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


# Compatibility for older callers; behavior is intentionally forward from 2026-09-01.
def collect_latest_first(**kwargs):
    kwargs.pop("today", None)
    kwargs.pop("lookback_days", None)
    return collect_forward(**kwargs)
