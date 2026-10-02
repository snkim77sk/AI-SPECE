"""G2B vNext budget collection: collect first, classify later.

This module is additive. The legacy keyword collector remains untouched while vNext
uses an independent 지방재정365 HTTP/parser path.
"""
import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

from lofin_vnext_http import SOURCE_NAME, fetch_budget_page
from vnext_paging import source_page_complete
from vnext_source_guard import (
    MAX_OPERATIONAL_BUDGET_AGE_DAYS,
    current_source_request_context,
    record_source_transport_success,
)
import budget_storage
from budget_storage import preserve_raw
from vnext_store import get_checkpoint, save_checkpoint

DATASET = "budget"
SOURCE_OPERATION = "QWGJK_FULL_V2_SNAPSHOT"
CHECKPOINT_CONTRACT = "QWGJK_SOURCE_IDENTITY_V2_STABLE_PROJECT"
HISTORY_CHECKPOINT_CONTRACT = "QWGJK_HISTORY_SOURCE_IDENTITY_V1"
BUDGET_HISTORY_START_DATE = dt.date(2026, 1, 1)


def _source_key(row, fiscal_year, snapshot_date=""):
    """Build a collision-safe QWGJK identity with code-first dimensions.

    When documented codes are present, the key is byte-for-byte compatible with the
    previous code-only identity. If one structural code is absent, only that
    dimension falls back to its source name so distinct departments/accounts do not
    collapse onto one RAW key.
    """
    year = str(row.get("fyr") or fiscal_year or "").strip()
    # Snapshot date is deliberately excluded from the record identity in v4.
    # The same structural budget project therefore reuses one stable record key;
    # changed payloads become immutable observations instead of duplicate daily rows.
    region = (
        str(row.get("wa_laf_cd") or "").strip()
        or str(row.get("wa_laf_hg_nm") or "").strip()
    )
    local = (
        str(row.get("laf_cd") or "").strip()
        or str(row.get("laf_hg_nm") or "").strip()
    )
    department = (
        str(row.get("dept_cd") or "").strip()
        or str(row.get("dept_nm") or "").strip()
    )
    business = (
        str(row.get("dbiz_cd") or "").strip()
        or str(row.get("dbiz_nm") or "").strip()
    )
    account = (
        str(row.get("acnt_dv_cd") or "").strip()
        or str(row.get("acnt_dv_nm") or "").strip()
    )
    parts = [year, region, local, department, business, account]
    if business:
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    # _scope_problem rejects rows without a business code/name. Keep a payload
    # fallback for direct helper use and diagnostics rather than allowing collisions.
    fallback = parts + [
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ]
    return hashlib.sha1("|".join(fallback).encode("utf-8")).hexdigest()


def preserve_budget_rows(rows, fiscal_year, snapshot_date):
    """Preserve every returned budget row before any lighting classification."""
    saved = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid budget row; refusing silent loss")
        preserve_raw(
            DATASET,
            _source_key(row, fiscal_year, snapshot_date),
            row,
            source_system=SOURCE_NAME,
            source_operation=SOURCE_OPERATION,
            source_date=str(snapshot_date),
        )
        saved += 1
    return saved


def _scope_problem(row, year, stamp, region_code=""):
    if row.get('fyr') not in (None, '') and str(row['fyr']).strip() != str(year):
        return 'BUDGET_FISCAL_YEAR_MISMATCH'
    if row.get('exe_ymd') not in (None, '') and str(row['exe_ymd']).replace('-', '').strip() != stamp.replace('-', ''):
        return 'BUDGET_SNAPSHOT_DATE_MISMATCH'
    region = str(region_code or '').strip()
    if region and row.get('wa_laf_cd') not in (None, '') and str(row.get('wa_laf_cd')).strip() != region:
        return 'BUDGET_REGION_MISMATCH'
    if not str(row.get('dbiz_cd') or row.get('dbiz_nm') or '').strip():
        return 'BUDGET_BUSINESS_IDENTITY_MISSING'
    return ''


def fetch_page(fiscal_year, snapshot_date, page=1, size=1000, region_code=""):
    """Return the LOFIN rows/total pair and bind it to the live request when active."""
    region = str(region_code or "").strip()
    if region:
        result = fetch_budget_page(
            int(fiscal_year), str(snapshot_date), "", page=int(page), size=int(size),
            region_code=region,
        )
    else:
        result = fetch_budget_page(
            int(fiscal_year), str(snapshot_date), "", page=int(page), size=int(size)
        )
    pair = result[:2]
    if current_source_request_context() is not None:
        record_source_transport_success(pair[0], pair[1])
    return pair



def next_historical_snapshot_date(
    *,
    today=None,
    start_date=BUDGET_HISTORY_START_DATE,
    retention_days=365,
):
    """Return the oldest source-safe rolling QWGJK day not marked COMPLETE.

    Collection begins at 2026-01-01. After that date ages beyond the configured
    retention/source-access window, the floor advances automatically.
    """
    if not budget_storage.using_postgres():
        return None
    import budget_pg_store

    current_day = today or dt.datetime.now(ZoneInfo("Asia/Seoul")).date()
    configured_start = (
        start_date if isinstance(start_date, dt.date)
        else dt.date.fromisoformat(str(start_date))
    )
    source_window_days = min(
        max(1, int(retention_days)),
        int(MAX_OPERATIONAL_BUDGET_AGE_DAYS),
    )
    start = max(
        configured_start,
        current_day - dt.timedelta(days=source_window_days),
    )
    latest = current_day - dt.timedelta(days=1)
    if start > latest:
        return None

    completed = set()
    for checkpoint in budget_pg_store.list_checkpoints(DATASET):
        if str(checkpoint.get("status") or "").upper() != "COMPLETE":
            continue
        scope = str(checkpoint.get("scope_key") or "")
        parts = scope.split(":")
        try:
            if len(parts) == 2:
                year = int(parts[0])
                day = dt.date.fromisoformat(parts[1])
            elif len(parts) == 3 and parts[0] == "history":
                year = int(parts[1])
                day = dt.date.fromisoformat(parts[2])
            else:
                continue
        except (TypeError, ValueError):
            continue
        if day.year == year:
            completed.add(day)

    day = start
    while day <= latest:
        if day not in completed:
            return day
        day += dt.timedelta(days=1)
    return None


def pending_nationwide_snapshot_date(*, today=None, max_age_days=365):
    """Return the oldest unresolved nationwide QWGJK snapshot newer than last COMPLETE.

    Incomplete checkpoints older than a later COMPLETE snapshot are obsolete and are
    not replayed. This prevents abandoned daily scopes from consuming quota forever
    while still guaranteeing that the active unresolved snapshot is resumed across
    KST day boundaries.
    """
    if not budget_storage.using_postgres():
        return None
    import budget_pg_store

    current_day = today or dt.datetime.now(ZoneInfo("Asia/Seoul")).date()
    floor = current_day - dt.timedelta(days=max(1, int(max_age_days)))
    complete_days = []
    pending_days = []

    for checkpoint in budget_pg_store.list_checkpoints(DATASET):
        scope = str(checkpoint.get("scope_key") or "")
        parts = scope.split(":")
        if len(parts) != 2:
            continue
        try:
            year = int(parts[0])
            day = dt.date.fromisoformat(parts[1])
        except (TypeError, ValueError):
            continue
        if day.year != year or day < floor or day > current_day:
            continue
        status = str(checkpoint.get("status") or "").upper()
        if status == "COMPLETE":
            complete_days.append(day)
        elif status in {"RUNNING", "FAILED", "INCOMPLETE"}:
            pending_days.append(day)

    latest_complete = max(complete_days) if complete_days else None
    candidates = [
        day for day in pending_days
        if latest_complete is None or day > latest_complete
    ]
    return min(candidates) if candidates else None

def collect_full_budget(fiscal_year=None, snapshot_date=None, *, region_code="", page_size=1000,
                        max_pages=None, resume=True, advance_current=True):
    """Collect one explicit fiscal-year/snapshot scope without any category filter.

    For a past fiscal year, an explicit snapshot is required; do not silently send
    today's execution date with a different year and treat an empty result as success.
    """
    from vnext_collection import collect_pages as sqlite_collect_pages
    import budget_pg_collection
    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date()
    year = int(fiscal_year if fiscal_year is not None else today.year)
    if snapshot_date is None and year != today.year:
        raise ValueError("past fiscal year requires an explicit snapshot_date")
    snapshot = dt.date.fromisoformat(str(snapshot_date or today.isoformat()))
    if snapshot.year != year:
        raise ValueError("fiscal year and snapshot year must match")
    if snapshot > today:
        raise ValueError("future budget snapshot is not collectable")
    stamp = snapshot.isoformat()
    region = str(region_code or "").strip()
    historical_only = not bool(advance_current)
    if historical_only:
        scope = (
            f"history:{year}:{stamp}"
            if not region
            else f"history:{year}:{stamp}:{region}"
        )
        checkpoint_contract = HISTORY_CHECKPOINT_CONTRACT
    else:
        scope = f"{year}:{stamp}" if not region else f"{year}:{stamp}:{region}"
        checkpoint_contract = CHECKPOINT_CONTRACT
    common = dict(
        dataset=DATASET, scope=scope, range_start=str(year), range_end=stamp,
        page_size=min(max(int(page_size), 1), 1000), max_pages=max_pages, resume=resume,
        fetch=lambda page, size: fetch_page(
            year, stamp, page=page, size=size, region_code=region
        ),
        identity=lambda row: _source_key(row, year, stamp),
        source_system=SOURCE_NAME, source_operation=SOURCE_OPERATION,
        source_date=lambda row: stamp,
        validate_row=lambda row: _scope_problem(row, year, stamp, region),
        checkpoint_contract=checkpoint_contract,
    )
    if budget_storage.using_postgres():
        result = budget_pg_collection.collect_pages(
            **common, advance_current=bool(advance_current)
        )
        if result.get("complete") is True and not region and bool(advance_current):
            import budget_pg_store
            result["reconciliation"] = (
                budget_pg_store.reconcile_complete_fiscal_year(
                    DATASET, scope, year
                )
            )
            result["checkpoint_supersession"] = (
                budget_pg_store.supersede_older_nationwide_checkpoints(
                    DATASET, year, scope
                )
            )
        elif result.get("complete") is True and not region:
            result["historical_only"] = True
            result["reconciliation"] = {
                "reconciled": False,
                "reason": "HISTORICAL_BACKFILL_CURRENT_STATE_PROTECTED",
                "removed_current_records": 0,
            }
        return result
    return sqlite_collect_pages(
        **common, preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint
    )


def collect_budget_region_partitions(fiscal_year, snapshot_date, region_codes, *,
                                     page_size=1000, max_pages=None, resume=True):
    """Collect an explicit list of QWGJK wide-area partitions.

    The caller owns the region list. This helper reports completion only for the
    supplied partition plan and never upgrades that to whole-source completeness.
    """
    regions = []
    for value in region_codes or ():
        region = str(value or "").strip()
        if region and region not in regions:
            regions.append(region)
    if not regions:
        raise ValueError("region_codes must contain at least one non-empty region")

    results = []
    for region in regions:
        result = collect_full_budget(
            fiscal_year,
            snapshot_date,
            region_code=region,
            page_size=page_size,
            max_pages=max_pages,
            resume=resume,
        )
        results.append(result)
        if result.get("complete") is not True:
            break
    return {
        "fiscal_year": int(fiscal_year),
        "snapshot_date": str(snapshot_date),
        "region_codes": regions,
        "partition_scope": "EXPLICIT_REGION_LIST",
        "results": results,
        "complete_for_planned_regions": len(results) == len(regions)
        and all(item.get("complete") is True for item in results),
        "source_collection_completeness_verified": False,
    }
