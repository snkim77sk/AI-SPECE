"""Full 지방교육재정알리미 RAW collection shape for G2B vNext.

The existing education-budget module remains the production API adapter and serving-
table integration. This additive module defines full-RAW identity/collection semantics
before any lighting keyword classification, but its live transport is intentionally
HOLD until a dedicated vNext source-traffic validation path is approved.
"""
from __future__ import annotations

import hashlib
import json

import education_budget_sync as legacy
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET = "education_budget"
SOURCE_OPERATION_PREFIX = "EDUINFO_FULL_RAW_V1"


def _norm_key(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _pick(row, *names):
    if not isinstance(row, dict):
        return ""
    for name in names:
        if name in row and row[name] not in (None, ""):
            return str(row[name]).strip()
    norm = {_norm_key(k): v for k, v in row.items()}
    for name in names:
        value = norm.get(_norm_key(name))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _source_key(row, fiscal_year, request_type):
    """Build a stable education-budget identity without using lighting keywords."""
    year = _pick(row, "YMQ", "year", "fiscalYear", "회계연도") or str(int(fiscal_year))
    parts = [
        year,
        str(request_type or "").strip(),
        _pick(row, "officeCode", "eduOfficeCode", "ATPT_OFCDC_SC_CODE", "교육청코드", "org_code"),
        _pick(row, "schoolCode", "institutionCode", "기관코드"),
        _pick(row, "projectCode", "businessCode", "사업코드", "세부사업코드"),
        _pick(row, "accountCode", "itemCode", "세목코드", "과목코드"),
    ]
    if any(parts[2:]):
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    names = parts + [
        _pick(row, "office_name", "officeName", "eduOfficeNm", "ATPT_OFCDC_SC_NM", "시도교육청명", "교육청명", "기관명"),
        _pick(row, "project_name", "projectName", "business_name", "businessName", "bizNm", "bsnsNm", "사업명", "세부사업명", "단위사업명", "정책사업명"),
        _pick(row, "accountName", "itemName", "세목명", "과목명"),
    ]
    if not any(names[2:]):
        names.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()


def _scope_problem(row, year):
    raw_year = _pick(row, "YMQ", "year", "fiscalYear", "회계연도")
    if raw_year and str(raw_year).strip() != str(year):
        return "EDUCATION_BUDGET_FISCAL_YEAR_MISMATCH"
    return ""


def fetch_page(fiscal_year, page=1, size=1000):
    """Live education source transport is intentionally HOLD.

    The full-RAW adapter is ready for offline regression/injected source-page tests,
    but must not open a second un-attested network path around the vNext source gate.
    """
    raise RuntimeError("EDUCATION_BUDGET_VNEXT_LIVE_TRANSPORT_HOLD")


def collect_full_education_budget(fiscal_year, *, page_size=1000, max_pages=None,
                                  resume=True, allow_live=False):
    """Preserve every education-budget row before post-RAW classification.

    Production scheduling remains disabled. Even with ``allow_live=True``, the
    default transport stays fail-closed until the dedicated education source gate is
    implemented and validated; tests may inject/monkeypatch ``fetch_page``.
    """
    if allow_live is not True:
        raise RuntimeError("EDUCATION_BUDGET_LIVE_COLLECTION_REQUIRES_EXPLICIT_ALLOW")

    from vnext_collection import collect_pages

    year = int(fiscal_year)
    request_type = legacy.get_request_type()
    scope = f"{year}:{request_type}"
    source_system = f"{legacy.SOURCE_PREFIX}({request_type})"
    source_operation = f"{SOURCE_OPERATION_PREFIX}:{request_type}"
    return collect_pages(
        dataset=DATASET,
        scope=scope,
        range_start=str(year),
        range_end=str(request_type),
        page_size=min(max(int(page_size), 1), 1000),
        max_pages=max_pages,
        resume=bool(resume),
        fetch=lambda page, size: fetch_page(year, page=page, size=size),
        identity=lambda row: _source_key(row, year, request_type),
        source_system=source_system,
        source_operation=source_operation,
        source_date=lambda row: str(year),
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
        validate_row=lambda row: _scope_problem(row, year),
    )
