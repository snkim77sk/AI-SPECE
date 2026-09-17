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
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET = "budget"
SOURCE_OPERATION = "QWGJK_FULL_V2_SNAPSHOT"


def _source_key(row, fiscal_year, snapshot_date=""):
    """Build a stable budget identity from codes, not mutable business names."""
    year = str(row.get("fyr") or fiscal_year or "").strip()
    code_parts = [
        year,
        str(row.get("exe_ymd") or snapshot_date or "").replace("-", ""),
        str(row.get("wa_laf_cd") or "").strip(),
        str(row.get("laf_cd") or "").strip(),
        str(row.get("dept_cd") or "").strip(),
        str(row.get("dbiz_cd") or "").strip(),
        str(row.get("acnt_dv_cd") or "").strip(),
    ]
    # With a business code, title/name changes must remain revisions of the same row.
    if str(row.get("dbiz_cd") or "").strip():
        return hashlib.sha1("|".join(code_parts).encode("utf-8")).hexdigest()
    # Some linked/local rows may omit dbiz_cd. In that case retain the descriptive
    # name as a collision-avoidance fallback rather than merging unrelated projects.
    fallback = code_parts + [str(row.get("dbiz_nm") or "").strip()]
    if not str(row.get("dbiz_nm") or "").strip():
        fallback.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
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


def _scope_problem(row, year, stamp):
    if row.get('fyr') not in (None, '') and str(row['fyr']).strip() != str(year):
        return 'BUDGET_FISCAL_YEAR_MISMATCH'
    if row.get('exe_ymd') not in (None, '') and str(row['exe_ymd']).replace('-', '').strip() != stamp.replace('-', ''):
        return 'BUDGET_SNAPSHOT_DATE_MISMATCH'
    if not str(row.get('dbiz_cd') or row.get('dbiz_nm') or '').strip():
        return 'BUDGET_BUSINESS_IDENTITY_MISSING'
    return ''


def collect_full_budget(fiscal_year=None, snapshot_date=None, *, page_size=1000, max_pages=None, resume=True):
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
    return collect_pages(
        dataset=DATASET, scope=f"{year}:{stamp}", range_start=str(year), range_end=stamp,
        page_size=min(max(int(page_size), 1), 1000), max_pages=max_pages, resume=resume,
        fetch=lambda page, size: fetch_budget_page(year, stamp, "", page=page, size=size)[:2],
        identity=lambda row: _source_key(row, year, stamp),
        source_system=SOURCE_NAME, source_operation=SOURCE_OPERATION,
        source_date=lambda row: stamp,
        preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint,
        validate_row=lambda row: _scope_problem(row, year, stamp),
    )
