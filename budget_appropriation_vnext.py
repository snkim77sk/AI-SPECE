"""Full 지방재정365 appropriation RAW collection for G2B vNext.

AIDFA is collected independently from QWGJK. No lighting/product keyword is applied
at collection time; classification happens only after the source rows are preserved.
"""
from __future__ import annotations

import hashlib
import json

from lofin_vnext_http import APPROPRIATION_SOURCE_NAME, fetch_appropriation_page
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET = "budget_appropriation"
SOURCE_OPERATION = "AIDFA_FULL_V1"


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
    if any(parts[2:]):
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


def collect_full_appropriation(fiscal_year, *, region_code="", page_size=1000,
                               max_pages=None, resume=True):
    """Collect all AIDFA rows for a fiscal year/optional documented region partition."""
    from vnext_collection import collect_pages

    year = int(fiscal_year)
    region = str(region_code or "").strip()
    scope = f"{year}:{region or 'ALL'}"
    return collect_pages(
        dataset=DATASET,
        scope=scope,
        range_start=str(year),
        range_end=region or "ALL",
        page_size=min(max(int(page_size), 1), 1000),
        max_pages=max_pages,
        resume=bool(resume),
        fetch=lambda page, size: fetch_page(year, region, page=page, size=size),
        identity=lambda row: _source_key(row, year, region),
        source_system=APPROPRIATION_SOURCE_NAME,
        source_operation=SOURCE_OPERATION,
        source_date=lambda row: str(year),
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
        validate_row=lambda row: _scope_problem(row, year, region),
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
