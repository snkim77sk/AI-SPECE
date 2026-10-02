"""Full 지방재정365 appropriation RAW collection for G2B vNext.

AIDFA is collected independently from QWGJK. No lighting/product keyword is applied
at collection time; classification happens only after the source rows are preserved.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

from lofin_vnext_http import APPROPRIATION_SOURCE_NAME, fetch_appropriation_page
import budget_storage
from budget_storage import preserve_raw
from vnext_store import get_checkpoint, save_checkpoint

DATASET = "budget_appropriation"
SOURCE_OPERATION = "AIDFA_FULL_V1"
CHECKPOINT_CONTRACT = "AIDFA_SOURCE_IDENTITY_V1"


def _text(row, *names):
    for name in names:
        value = row.get(name) if isinstance(row, dict) else None
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _source_key(row, fiscal_year, region_code=""):
    """Build identity from documented AIDFA structural fields.

    AIDFA rows are grouped by fiscal year, region/local-government, field, section
    and account division. Each structural dimension prefers its stable code and
    falls back to its name independently, preventing name-only rows from colliding
    merely because another dimension (such as local-government code) is present.
    """
    year = _text(row, "fyr") or str(int(fiscal_year))
    region_identity = (
        _text(row, "wa_laf_cd")
        or str(region_code or "").strip()
        or _text(row, "wa_laf_hg_nm")
    )
    local_identity = _text(row, "laf_cd") or _text(row, "laf_hg_nm")
    field_identity = _text(row, "fld_cd") or _text(row, "fld_nm")
    section_identity = _text(row, "sect_cd") or _text(row, "sect_nm")
    account_identity = _text(row, "acnt_dv_cd") or _text(row, "acnt_dv_nm")
    parts = [
        year,
        region_identity,
        local_identity,
        field_identity,
        section_identity,
        account_identity,
    ]
    # A local-government code alone is not enough to identify an AIDFA row.
    # When all structural dimensions are missing, include the canonical payload so
    # multiple partial/malformed source rows cannot overwrite one another in RAW.
    if any((field_identity, section_identity, account_identity)):
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    fallback = list(parts)
    fallback.append(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return hashlib.sha1("|".join(fallback).encode("utf-8")).hexdigest()


def _scope_problem(row, year, region_code):
    if row.get("fyr") not in (None, "") and str(row.get("fyr")).strip() != str(year):
        return "APPROPRIATION_FISCAL_YEAR_MISMATCH"
    region = str(region_code or "").strip()
    if region and row.get("wa_laf_cd") not in (None, "") and str(row.get("wa_laf_cd")).strip() != region:
        return "APPROPRIATION_REGION_MISMATCH"
    return ""


def fetch_page(fiscal_year, region_code="", page=1, size=1000):
    result = fetch_appropriation_page(
        int(fiscal_year), str(region_code or ""), page=int(page), size=int(size)
    )
    return result[:2]


def _checkpoint_for_scope(scope):
    if budget_storage.using_postgres():
        import budget_pg_store
        return budget_pg_store.get_checkpoint(DATASET, scope)
    return get_checkpoint(DATASET, scope)


def _refresh_resume(scope, *, resume, refresh_date=""):
    """Replay a COMPLETE AIDFA scope once per newer refresh date.

    Incomplete scopes always resume so a large nationwide source can span cycles.
    A zero-row COMPLETE result is therefore checked again on the next date instead
    of becoming a permanent no-data checkpoint.
    """
    if not resume or not str(refresh_date or "").strip():
        return bool(resume)
    checkpoint = _checkpoint_for_scope(scope)
    if not checkpoint or str(checkpoint.get("status") or "") != "COMPLETE":
        return True

    refresh_day = dt.date.fromisoformat(str(refresh_date)[:10])
    stamp = str(checkpoint.get("updated_at") or "").strip()
    if not stamp:
        return False
    try:
        parsed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        # Unknown legacy timestamp format: replay rather than silently treating it
        # as today's verified refresh.
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    last_kst_day = parsed.astimezone(ZoneInfo("Asia/Seoul")).date()
    return last_kst_day >= refresh_day


def collect_full_appropriation(fiscal_year, *, region_code="", page_size=1000,
                               max_pages=None, resume=True, refresh_date=""):
    """Collect AIDFA, optionally replaying a completed scope once per new date."""
    from vnext_collection import collect_pages as sqlite_collect_pages
    import budget_pg_collection

    year = int(fiscal_year)
    region = str(region_code or "").strip()
    refresh_stamp = str(refresh_date or "").strip()
    scope = f"{year}:{region or 'ALL'}"
    effective_resume = _refresh_resume(
        scope,
        resume=bool(resume),
        refresh_date=refresh_stamp,
    )
    common = dict(
        dataset=DATASET,
        scope=scope,
        range_start=str(year),
        range_end=region or "ALL",
        page_size=min(max(int(page_size), 1), 1000),
        max_pages=max_pages,
        resume=effective_resume,
        fetch=lambda page, size: fetch_page(year, region, page=page, size=size),
        identity=lambda row: _source_key(row, year, region),
        source_system=APPROPRIATION_SOURCE_NAME,
        source_operation=SOURCE_OPERATION,
        source_date=lambda row: refresh_stamp or str(year),
        validate_row=lambda row: _scope_problem(row, year, region),
        checkpoint_contract=CHECKPOINT_CONTRACT,
    )
    if budget_storage.using_postgres():
        result = budget_pg_collection.collect_pages(**common)
        if result.get("complete") is True and not region:
            import budget_pg_store
            result["reconciliation"] = (
                budget_pg_store.reconcile_complete_fiscal_year(
                    DATASET, scope, year
                )
            )
        return result
    return sqlite_collect_pages(
        **common, preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint
    )


def collect_appropriation_region_partitions(fiscal_year, region_codes, *,
                                            page_size=1000, max_pages=None,
                                            resume=True):
    """Collect an explicit list of AIDFA wide-area partitions.

    Completion is scoped only to the supplied region plan. The helper deliberately
    does not claim that the caller's list covers the whole official source.
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
        result = collect_full_appropriation(
            fiscal_year,
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
        "region_codes": regions,
        "partition_scope": "EXPLICIT_REGION_LIST",
        "results": results,
        "complete_for_planned_regions": len(results) == len(regions)
        and all(item.get("complete") is True for item in results),
        "source_collection_completeness_verified": False,
    }
