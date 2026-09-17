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
    """Prefer structural budget codes; fall back to stable names/full payload."""
    year = _text(row, "fyr") or str(int(fiscal_year))
    parts = [
        year,
        _text(row, "wa_laf_cd") or str(region_code or "").strip(),
        _text(row, "laf_cd"),
        _text(row, "dept_cd"),
        _text(row, "acnt_dv_cd"),
        _text(row, "fld_cd"),
        _text(row, "part_cd", "sect_cd"),
        _text(row, "biz_cd", "dbiz_cd"),
    ]
    if any(parts[2:]):
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    fallback = parts + [
        _text(row, "laf_hg_nm"),
        _text(row, "fld_nm"),
        _text(row, "part_nm", "sect_nm"),
        _text(row, "acnt_dv_nm"),
    ]
    if not any(fallback[2:]):
        fallback.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
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
    """Collect all AIDFA rows for a fiscal year/optional wide-area partition."""
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
