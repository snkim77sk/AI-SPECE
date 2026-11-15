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
from vnext_source_guard import current_source_request_context, record_source_transport_success
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET = "budget"
SOURCE_OPERATION = "QWGJK_FULL_V2_SNAPSHOT"
CHECKPOINT_CONTRACT = "QWGJK_SOURCE_IDENTITY_V1"


def _source_key(row, fiscal_year, snapshot_date=""):
    """Build a collision-safe QWGJK identity with code-first dimensions.

    When documented codes are present, the key is byte-for-byte compatible with the
    previous code-only identity. If one structural code is absent, only that
    dimension falls back to its source name so distinct departments/accounts do not
    collapse onto one RAW key.
    """
    year = str(row.get("fyr") or fiscal_year or "").strip()
    snapshot = str(row.get("exe_ymd") or snapshot_date or "").replace("-", "").strip()
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
    parts = [year, snapshot, region, local, department, business, account]
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


def collect_full_budget(fiscal_year=None, snapshot_date=None, *, region_code="", page_size=1000, max_pages=None, resume=True):
    """Collect one explicit fiscal-year/snapshot scope without any category filter.

    For a past fiscal year, an explicit snapshot is required; do not silently send
    today's execution date with a different year and treat an empty result as success.
    """
    from vnext_collection import collect_pages
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
    # Keep the historical nationwide scope key unchanged for checkpoint compatibility.
    scope = f"{year}:{stamp}" if not region else f"{year}:{stamp}:{region}"
    return collect_pages(
        dataset=DATASET, scope=scope, range_start=str(year), range_end=stamp,
        page_size=min(max(int(page_size), 1), 1000), max_pages=max_pages, resume=resume,
        fetch=lambda page, size: fetch_page(
            year, stamp, page=page, size=size, region_code=region
        ),
        identity=lambda row: _source_key(row, year, stamp),
        source_system=SOURCE_NAME, source_operation=SOURCE_OPERATION,
        source_date=lambda row: stamp,
        preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint,
        validate_row=lambda row: _scope_problem(row, year, stamp, region),
        checkpoint_contract=CHECKPOINT_CONTRACT,
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
