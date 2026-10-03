"""Operational forward shopping-delivery collection.

Bootstrap rule:
- start at 2026-09-01,
- collect one calendar day at a time in ascending order,
- stop at the latest completed source day (D-1 in Korea time),
- skip checkpoints that are already structurally COMPLETE,
- retry the first failed/incomplete date before moving forward,
- normalize target rows during collection without persisting source JSON,
- never unlock the wider APPROVED_HISTORICAL mode.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import classification_vnext
import shopping_vnext
from db import get_setting, set_setting
from vnext_collection import (
    compact_verified_terminal_receipt,
    verified_compact_completion,
    verified_terminal_receipt,
)
from vnext_source_guard import operational_recent_source_context
from vnext_store import get_checkpoint

KST = ZoneInfo("Asia/Seoul")
BOOTSTRAP_START_DATE = dt.date(2026, 9, 1)
LATEST_SOURCE_LAG_DAYS = 1
DEFAULT_MAX_DAYS_PER_RUN = 62
DEFAULT_PAGE_SIZE = 999
DEFAULT_MAX_PAGES_PER_DAY = 40
DEFAULT_REQUEST_BUDGET_PER_DAY = 64
DEFAULT_RECHECK_DAYS = 7
MAX_RECHECK_DAYS = 7
RECHECK_STATE_KEY = "shopping_recent_recheck_state"
DEFAULT_LONGTAIL_RECHECK_DAYS_PER_RUN = 2
DEFAULT_RETENTION_DAYS = 365
MAX_RETENTION_DAYS = 365
MAX_LONGTAIL_RECHECK_DAYS_PER_RUN = 2
LONGTAIL_RECHECK_STATE_KEY = "shopping_longtail_recheck_state"


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


def _retention_start_day(requested_start, run_date, retention_days):
    requested = max(_as_date(requested_start), BOOTSTRAP_START_DATE)
    days = max(0, min(int(retention_days), MAX_RETENTION_DAYS))
    if days <= 0:
        return requested
    floor = _as_date(run_date) - dt.timedelta(days=days)
    return max(requested, floor)


def _status(name, value):
    set_setting(f"shopping_recent_{name}", value)


def _scope(day):
    iso = day.isoformat()
    return f"{iso}:{iso}"


def _already_complete(day, *, storage_prepared=False):
    cp = get_checkpoint(
        shopping_vnext.DATASET,
        _scope(day),
        schema_prepared=bool(storage_prepared),
    )
    if not cp:
        return False
    if verified_compact_completion(cp):
        return True
    receipt_valid = verified_terminal_receipt(
        cp,
        schema_prepared=bool(storage_prepared),
    )
    if receipt_valid and shopping_vnext.compact_complete_enabled():
        compact_verified_terminal_receipt(
            cp,
            schema_prepared=bool(storage_prepared),
            receipt_verified=True,
        )
    return bool(receipt_valid)


def _days_forward(start_day, latest_day):
    current = start_day
    while current <= latest_day:
        yield current
        current += dt.timedelta(days=1)


def _load_recheck_state(run_date):
    run_iso = _as_date(run_date).isoformat()
    try:
        payload = json.loads(str(get_setting(RECHECK_STATE_KEY, "") or ""))
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict) or str(payload.get("run_date") or "") != run_iso:
        return {"run_date": run_iso, "dates": []}
    dates = []
    for value in payload.get("dates") or []:
        try:
            iso = _as_date(value).isoformat()
        except (TypeError, ValueError):
            continue
        if iso not in dates:
            dates.append(iso)
    return {"run_date": run_iso, "dates": dates}


def _save_recheck_state(state):
    run_date = _as_date((state or {}).get("run_date")).isoformat()
    dates = sorted({
        _as_date(value).isoformat()
        for value in ((state or {}).get("dates") or [])
    })
    set_setting(
        RECHECK_STATE_KEY,
        json.dumps(
            {"run_date": run_date, "dates": dates},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _recheck_window(start_day, latest_day, recheck_days):
    days = max(0, min(int(recheck_days), MAX_RECHECK_DAYS))
    if days <= 0 or start_day > latest_day:
        return []
    window_start = max(
        start_day,
        latest_day - dt.timedelta(days=days - 1),
    )
    return list(_days_forward(window_start, latest_day))


def _longtail_window(start_day, latest_day, recent_recheck_days):
    if start_day > latest_day:
        return None
    recent = _recheck_window(start_day, latest_day, recent_recheck_days)
    end_day = (
        recent[0] - dt.timedelta(days=1)
        if recent
        else latest_day
    )
    if end_day < start_day:
        return None
    return start_day, end_day


def _load_longtail_state(start_day, end_day, run_date):
    run_iso = _as_date(run_date).isoformat()
    try:
        payload = json.loads(
            str(get_setting(LONGTAIL_RECHECK_STATE_KEY, "") or "")
        )
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        next_day = _as_date(payload.get("next_date"))
    except (TypeError, ValueError):
        next_day = start_day
    if next_day < start_day or next_day > end_day:
        next_day = start_day

    stored_run_date = str(payload.get("run_date") or "").strip()
    dates = []
    if stored_run_date == run_iso:
        for value in payload.get("dates") or []:
            try:
                iso = _as_date(value).isoformat()
            except (TypeError, ValueError):
                continue
            if iso not in dates:
                dates.append(iso)
    return {
        "run_date": run_iso,
        "next_date": next_day.isoformat(),
        "dates": dates,
    }


def _save_longtail_state(state):
    run_date = _as_date((state or {}).get("run_date")).isoformat()
    next_date = _as_date((state or {}).get("next_date")).isoformat()
    dates = sorted({
        _as_date(value).isoformat()
        for value in ((state or {}).get("dates") or [])
    })
    set_setting(
        LONGTAIL_RECHECK_STATE_KEY,
        json.dumps(
            {
                "run_date": run_date,
                "next_date": next_date,
                "dates": dates,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _next_rotating_day(day, start_day, end_day):
    candidate = day + dt.timedelta(days=1)
    return start_day if candidate > end_day else candidate


def _rotating_days(start_day, end_day, cursor):
    total = (end_day - start_day).days + 1
    current = _as_date(cursor)
    if current < start_day or current > end_day:
        current = start_day
    for _ in range(total):
        yield current
        current = _next_rotating_day(current, start_day, end_day)


def _notify_progress(progress, event, **details):
    if progress is None:
        return
    try:
        progress({"event": str(event), **details})
    except Exception:
        return


def collect_forward(
    *,
    start_date=BOOTSTRAP_START_DATE,
    latest_date=None,
    max_days=DEFAULT_MAX_DAYS_PER_RUN,
    page_size=DEFAULT_PAGE_SIZE,
    max_pages_per_day=DEFAULT_MAX_PAGES_PER_DAY,
    request_budget_per_day=DEFAULT_REQUEST_BUDGET_PER_DAY,
    recheck_days=0,
    longtail_recheck_days_per_run=0,
    retention_days=0,
    progress=None,
    defer_classification=False,
):
    """Collect normalized shopping delivery records from the oldest requested day toward D-1.

    Already verified COMPLETE days do not consume the per-run day budget. The first
    failed or incomplete date is retried and must finish before a newer date starts.
    """
    requested_start_day = _as_date(start_date)
    recheck_run_date = _kst_today()
    start_day = _retention_start_day(
        requested_start_day,
        recheck_run_date,
        retention_days,
    )
    latest_day = _latest_available_day(latest_date)
    if start_day > latest_day:
        return {
            "status": "COMPLETE",
            "order": "FORWARD",
            "start_date": start_day.isoformat(),
            "requested_start_date": requested_start_day.isoformat(),
            "retention_start_date": start_day.isoformat(),
            "latest_available_date": latest_day.isoformat(),
            "results": [],
            "rechecks": [],
            "longtail_rechecks": [],
            "classification": None,
        }

    day_budget = max(1, min(int(max_days), 62))
    page_size = max(1, min(int(page_size), 999))
    max_pages = max(1, min(int(max_pages_per_day), DEFAULT_MAX_PAGES_PER_DAY))
    request_budget = max(1, min(int(request_budget_per_day), 64))
    recent_recheck_days = max(0, min(int(recheck_days), MAX_RECHECK_DAYS))
    longtail_days_per_run = max(
        0,
        min(
            int(longtail_recheck_days_per_run),
            MAX_LONGTAIL_RECHECK_DAYS_PER_RUN,
        ),
    )

    started = dt.datetime.now(KST)
    _status("state", "RUNNING")
    _status("last_started_at_kst", started.isoformat(timespec="seconds"))
    _status("last_error", "")
    _status("order", "FORWARD")
    _status("start_date", start_day.isoformat())
    _status("latest_available_date", latest_day.isoformat())

    # DDL/schema preparation is run-scoped. All date/page work below assumes
    # these idempotent schemas already exist and performs data/checkpoint I/O only.
    shopping_vnext.prepare_collection_storage()

    results = []
    rechecks = []
    longtail_rechecks = []
    total_days = (latest_day - start_day).days + 1
    _notify_progress(
        progress, "run_start",
        start_date=start_day.isoformat(),
        requested_start_date=requested_start_day.isoformat(),
        retention_days=max(0, min(int(retention_days), MAX_RETENTION_DAYS)),
        latest_date=latest_day.isoformat(),
        total_days=total_days,
    )
    _notify_progress(progress, "prepare_start", stage="identity_migration")
    # 4.1 fresh start does not import pre-cutover shopping RAW; this remains an
    # explicit compatibility tombstone.
    identity_migration = shopping_vnext.migrate_legacy_source_keys()
    _notify_progress(progress, "prepare_complete", stage="identity_migration")
    classification = None
    if defer_classification:
        _notify_progress(
            progress, "classification_deferred",
            stage="batch_end",
        )
    else:
        _notify_progress(progress, "classification_start", stage="existing_records")
        # Default behavior preserves immediate stale-classification repair.
        classification = classification_vnext.classify_dataset(
            shopping_vnext.DATASET,
            batch_size=1000,
        )
        _notify_progress(
            progress, "classification_complete", stage="existing_records",
            classified=int((classification or {}).get("classified") or 0),
        )
    attempted = 0
    completed_this_run = set()
    try:
        for day_index, day in enumerate(_days_forward(start_day, latest_day), 1):
            iso = day.isoformat()
            if _already_complete(day, storage_prepared=True):
                _notify_progress(
                    progress, "day_skipped", date=iso,
                    day_index=day_index, total_days=total_days,
                )
                continue
            if attempted >= day_budget:
                break

            attempted += 1
            _status("current_date", iso)
            _notify_progress(
                progress, "day_start", date=iso,
                day_index=day_index, total_days=total_days,
                attempted=attempted,
            )

            def page_progress(event):
                data = dict(event or {})
                event_name = str(data.pop("event", "page_progress"))
                _notify_progress(
                    progress, event_name, date=iso,
                    day_index=day_index, total_days=total_days, **data,
                )
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
                    progress=page_progress,
                    storage_prepared=True,
                )
            results.append({"date": iso, **result})

            if not defer_classification:
                _notify_progress(
                    progress, "classification_start", stage="day",
                    date=iso, day_index=day_index, total_days=total_days,
                )
                classification = classification_vnext.classify_dataset(
                    shopping_vnext.DATASET,
                    batch_size=1000,
                )
                _notify_progress(
                    progress, "classification_complete", stage="day",
                    date=iso, day_index=day_index, total_days=total_days,
                    classified=int((classification or {}).get("classified") or 0),
                )
            if not result.get("complete"):
                if defer_classification:
                    _notify_progress(
                        progress, "classification_start", stage="batch_end",
                        date=iso, day_index=day_index, total_days=total_days,
                    )
                    classification = classification_vnext.classify_dataset(
                        shopping_vnext.DATASET,
                        batch_size=5000,
                    )
                    _notify_progress(
                        progress, "classification_complete", stage="batch_end",
                        date=iso, day_index=day_index, total_days=total_days,
                        classified=int((classification or {}).get("classified") or 0),
                    )
                _notify_progress(
                    progress, "day_partial", date=iso,
                    day_index=day_index, total_days=total_days,
                    saved=int(result.get("saved") or 0),
                    source_total=result.get("source_total"),
                )
                _status("state", "PARTIAL")
                return {
                    "status": "PARTIAL",
                    "order": "FORWARD",
                    "start_date": start_day.isoformat(),
            "requested_start_date": requested_start_day.isoformat(),
            "retention_start_date": start_day.isoformat(),
                    "latest_available_date": latest_day.isoformat(),
                    "results": results,
                    "rechecks": rechecks,
                    "longtail_rechecks": longtail_rechecks,
                    "classification": classification,
                    "identity_migration": identity_migration,
                }
            completed_this_run.add(day)
            _status("last_completed_date", iso)
            _notify_progress(
                progress, "day_complete", date=iso,
                day_index=day_index, total_days=total_days,
                saved=int(result.get("saved") or 0),
                source_total=result.get("source_total"),
            )

        remaining = any(
            day not in completed_this_run
            and not _already_complete(day, storage_prepared=True)
            for day in _days_forward(start_day, latest_day)
        )

        # Only spend quota on late-arrival rechecks after the baseline is fully
        # caught up through D-1. Each source date is rechecked at most once per KST
        # day; dates freshly collected in this run count as already observed today.
        if not remaining and recent_recheck_days > 0:
            run_date = recheck_run_date
            state = _load_recheck_state(run_date)
            checked = set(state["dates"])
            window = _recheck_window(start_day, latest_day, recent_recheck_days)
            _notify_progress(
                progress,
                "recheck_start",
                recheck_days=recent_recheck_days,
                window_start=(window[0].isoformat() if window else ""),
                window_end=(window[-1].isoformat() if window else ""),
            )
            for day in window:
                iso = day.isoformat()
                if iso in checked:
                    _notify_progress(
                        progress,
                        "recheck_skipped",
                        date=iso,
                        reason="ALREADY_RECHECKED_TODAY",
                    )
                    continue
                if day in completed_this_run:
                    checked.add(iso)
                    state["dates"] = sorted(checked)
                    _save_recheck_state(state)
                    _notify_progress(
                        progress,
                        "recheck_skipped",
                        date=iso,
                        reason="COLLECTED_THIS_RUN",
                    )
                    continue

                _notify_progress(progress, "recheck_day_start", date=iso)
                with operational_recent_source_context(
                    collection_date=iso,
                    max_requests=request_budget,
                ):
                    recheck = shopping_vnext.collect_all(
                        iso,
                        iso,
                        page_size=page_size,
                        max_pages=max_pages,
                        resume=False,
                        progress=None,
                        storage_prepared=True,
                    )
                rechecks.append({"date": iso, **recheck})
                if not recheck.get("complete"):
                    remaining = True
                    _notify_progress(
                        progress,
                        "recheck_day_partial",
                        date=iso,
                        saved=int(recheck.get("saved") or 0),
                        source_total=recheck.get("source_total"),
                    )
                    break
                checked.add(iso)
                state["dates"] = sorted(checked)
                _save_recheck_state(state)
                _notify_progress(
                    progress,
                    "recheck_day_complete",
                    date=iso,
                    saved=int(recheck.get("saved") or 0),
                    source_total=recheck.get("source_total"),
                )
            _notify_progress(
                progress,
                "recheck_complete",
                completed_dates=len(checked),
                attempted=len(rechecks),
            )

        # Long-tail correction is lower priority than both baseline catch-up and
        # the recent 7-day window. Only a run that started fully caught up may
        # spend quota on older COMPLETE dates.
        if (
            not remaining
            and longtail_days_per_run > 0
            and attempted == 0
        ):
            window = _longtail_window(
                start_day,
                latest_day,
                recent_recheck_days,
            )
            if window is not None:
                longtail_start, longtail_end = window
                longtail_state = _load_longtail_state(
                    longtail_start,
                    longtail_end,
                    recheck_run_date,
                )
                recent_checked = set(
                    _load_recheck_state(recheck_run_date)["dates"]
                )
                longtail_checked = set(longtail_state["dates"])
                cursor = _as_date(longtail_state["next_date"])
                longtail_attempted = 0
                remaining_slots = max(
                    0,
                    longtail_days_per_run - len(longtail_checked),
                )
                if remaining_slots > 0:
                    _notify_progress(
                        progress,
                        "longtail_recheck_start",
                        window_start=longtail_start.isoformat(),
                        window_end=longtail_end.isoformat(),
                        next_date=cursor.isoformat(),
                        max_days=longtail_days_per_run,
                        already_checked=len(longtail_checked),
                    )
                    for day in _rotating_days(
                        longtail_start,
                        longtail_end,
                        cursor,
                    ):
                        iso = day.isoformat()
                        next_day = _next_rotating_day(
                            day,
                            longtail_start,
                            longtail_end,
                        )
                        if iso in recent_checked or iso in longtail_checked:
                            cursor = next_day
                            longtail_state["next_date"] = cursor.isoformat()
                            _save_longtail_state(longtail_state)
                            continue

                        _notify_progress(
                            progress,
                            "longtail_recheck_day_start",
                            date=iso,
                        )
                        with operational_recent_source_context(
                            collection_date=iso,
                            max_requests=request_budget,
                        ):
                            longtail = shopping_vnext.collect_all(
                                iso,
                                iso,
                                page_size=page_size,
                                max_pages=max_pages,
                                resume=False,
                                progress=None,
                                storage_prepared=True,
                            )
                        longtail_rechecks.append({"date": iso, **longtail})
                        longtail_attempted += 1
                        if not longtail.get("complete"):
                            remaining = True
                            _notify_progress(
                                progress,
                                "longtail_recheck_day_partial",
                                date=iso,
                                saved=int(longtail.get("saved") or 0),
                                source_total=longtail.get("source_total"),
                            )
                            break

                        cursor = next_day
                        longtail_checked.add(iso)
                        longtail_state["dates"] = sorted(longtail_checked)
                        longtail_state["next_date"] = cursor.isoformat()
                        _save_longtail_state(longtail_state)
                        _notify_progress(
                            progress,
                            "longtail_recheck_day_complete",
                            date=iso,
                            saved=int(longtail.get("saved") or 0),
                            source_total=longtail.get("source_total"),
                        )
                        if len(longtail_checked) >= longtail_days_per_run:
                            break

                    _notify_progress(
                        progress,
                        "longtail_recheck_complete",
                        attempted=longtail_attempted,
                        checked_today=len(longtail_checked),
                        next_date=cursor.isoformat(),
                        status=("PARTIAL" if remaining else "COMPLETE"),
                    )

        if defer_classification:
            _notify_progress(progress, "classification_start", stage="batch_end")
            classification = classification_vnext.classify_dataset(
                shopping_vnext.DATASET,
                batch_size=5000,
            )
            _notify_progress(
                progress, "classification_complete", stage="batch_end",
                classified=int((classification or {}).get("classified") or 0),
            )

        status = "PARTIAL" if remaining else "COMPLETE"
        finished = dt.datetime.now(KST)
        _status("state", status)
        _status("last_finished_at_kst", finished.isoformat(timespec="seconds"))
        _notify_progress(
            progress, "run_complete", status=status,
            total_days=total_days, attempted=attempted,
        )
        return {
            "status": status,
            "order": "FORWARD",
            "start_date": start_day.isoformat(),
            "requested_start_date": requested_start_day.isoformat(),
            "retention_start_date": start_day.isoformat(),
            "latest_available_date": latest_day.isoformat(),
            "results": results,
            "rechecks": rechecks,
            "longtail_rechecks": longtail_rechecks,
            "classification": classification,
            "identity_migration": identity_migration,
        }
    except Exception as exc:
        import vnext_http
        from vnext_source_guard import VNextSourceAccessError

        finished = dt.datetime.now(KST).isoformat(timespec="seconds")
        if isinstance(exc, vnext_http.VNextLocalQuotaReached):
            _notify_progress(
                progress, "run_waiting_quota",
                error_type=type(exc).__name__,
                total_days=total_days,
                attempted=attempted,
            )
            _status("state", "WAITING_QUOTA")
            _status("last_error", "LOCAL_QUOTA")
            _status("last_finished_at_kst", finished)
            return {
                "status": "WAITING_QUOTA",
                "order": "FORWARD",
                "start_date": start_day.isoformat(),
            "requested_start_date": requested_start_day.isoformat(),
            "retention_start_date": start_day.isoformat(),
                "latest_available_date": latest_day.isoformat(),
                "results": results,
                "rechecks": rechecks,
                "longtail_rechecks": longtail_rechecks,
                "classification": classification,
                "identity_migration": identity_migration,
                "quota": vnext_http.api_usage("shopping"),
            }

        if (
            isinstance(exc, VNextSourceAccessError)
            and str(exc) == "VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED"
        ):
            _notify_progress(
                progress, "run_partial_request_budget",
                error_type=type(exc).__name__,
                total_days=total_days,
                attempted=attempted,
            )
            _status("state", "PARTIAL")
            _status("last_error", "PER_DAY_REQUEST_BUDGET_EXHAUSTED")
            _status("last_finished_at_kst", finished)
            return {
                "status": "PARTIAL",
                "order": "FORWARD",
                "start_date": start_day.isoformat(),
            "requested_start_date": requested_start_day.isoformat(),
            "retention_start_date": start_day.isoformat(),
                "latest_available_date": latest_day.isoformat(),
                "results": results,
                "rechecks": rechecks,
                "longtail_rechecks": longtail_rechecks,
                "classification": classification,
                "identity_migration": identity_migration,
            }

        _notify_progress(
            progress, "run_failed",
            error_type=type(exc).__name__,
            total_days=total_days,
        )
        _status("state", "FAILED")
        _status("last_error", type(exc).__name__)
        _status("last_finished_at_kst", finished)
        raise


# Compatibility for older callers; behavior is intentionally forward from 2026-09-01.
def collect_latest_first(**kwargs):
    kwargs.pop("today", None)
    kwargs.pop("lookback_days", None)
    return collect_forward(**kwargs)
