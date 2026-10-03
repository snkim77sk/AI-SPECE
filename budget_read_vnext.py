"""Read-only consumer interface for organized G2B vNext budget data.

This module is intentionally small. It does not collect, normalize, classify or
refresh data. It only exposes already-prepared vNext budget state in a stable shape
that the existing AI-SPECE dashboard/API layer can consume later without changing
legacy production files.

Data flow remains:
    source collection -> normalization -> organization/classification -> read
"""
from __future__ import annotations

import budget_collection_status_vnext
from budget_organization_vnext import (
    budget_timeline,
    exact_appropriation_detail_links,
    exact_appropriation_detail_links_from_rows,
    organization_summary,
    organization_summary_from_rows,
    projection_coverage,
)
from budget_targets_vnext import (
    TARGET_CATEGORIES,
    current_budget_analysis,
    target_candidates,
    target_candidates_from_rows,
    target_summary,
    target_summary_from_rows,
)


REGIONS = (
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
    "경상남도", "제주특별자치도",
)


def canonical_region(value):
    """Map LOFIN and education-office names to one of the 17 top-level regions."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    for region in REGIONS:
        short = (
            region.replace("특별자치도", "")
            .replace("특별자치시", "")
            .replace("광역시", "")
            .replace("특별시", "")
            .replace("도", "")
        )
        if text == region or text.startswith(region):
            return region
        if short and (text == short or text.startswith(short)):
            return region
    return ""


def row_region(row):
    item = row or {}
    for value in (
        item.get("region_name"),
        item.get("org_name"),
        item.get("institution_name"),
    ):
        region = canonical_region(value)
        if region:
            return region
    return ""


def region_matches(row, region):
    selected = canonical_region(region)
    if not str(region or "").strip():
        return True
    return bool(selected and row_region(row) == selected)


def _filter_region(rows, region):
    if not str(region or "").strip():
        return list(rows)
    selected = canonical_region(region)
    if not selected:
        return []
    return [row for row in rows if row_region(row) == selected]


def _page(rows, *, limit=200, offset=0):
    start = max(0, int(offset))
    size = max(1, min(int(limit), 5000))
    return rows[start:start + size]


READ_MODEL_VIEWS = (
    "current_rows",
    "target_rows",
    "appropriation_context",
    "prebid_rows",
)


def _page_spec(limit, offset):
    return {
        "limit": max(1, min(int(limit), 5000)),
        "offset": max(0, int(offset)),
    }


def _read_model_pagination(*, limit=200, offset=0, view_pagination=None):
    """Resolve independent paging for each read-model child view.

    The top-level offset belongs to the primary current_rows list only. Other
    child views start from offset 0 unless the caller explicitly supplies their own
    paging entry. This prevents paging one screen/list from making unrelated child
    views appear empty.
    """
    default_limit = _page_spec(limit, 0)["limit"]
    specs = {
        name: _page_spec(default_limit, offset if name == "current_rows" else 0)
        for name in READ_MODEL_VIEWS
    }
    if view_pagination is None:
        overrides = {}
    elif not isinstance(view_pagination, dict):
        raise TypeError("view_pagination must be a mapping")
    else:
        overrides = view_pagination
    unknown = sorted(set(overrides) - set(READ_MODEL_VIEWS))
    if unknown:
        raise ValueError(
            "unknown budget read-model pagination view: " + ",".join(unknown)
        )
    for name, value in overrides.items():
        if not isinstance(value, dict):
            raise TypeError(f"pagination for {name} must be a mapping")
        specs[name] = _page_spec(
            value.get("limit", specs[name]["limit"]),
            value.get("offset", specs[name]["offset"]),
        )
    return specs


def current_budget_rows(*, fiscal_year=None, categories=None, region="",
                        limit=200, offset=0,
                        classifier_version=None, _analysis_rows=None):
    """Return current organized budget rows, including OTHER by default.

    ``categories=None`` means no analysis filter. Supplying an explicit empty list
    means return no rows. This keeps target filtering downstream from collection.
    """
    rows = (
        list(_analysis_rows)
        if _analysis_rows is not None
        else current_budget_analysis(
            fiscal_year=fiscal_year, classifier_version=classifier_version
        )
    )
    if categories is not None:
        selected = {str(value).upper() for value in categories if str(value).strip()}
        if not selected:
            return []
        rows = [
            row for row in rows
            if str(row.get("primary_category") or "").upper() in selected
        ]
    rows = _filter_region(rows, region)
    return _page(rows, limit=limit, offset=offset)


def _has_sales_project_identity(row):
    return bool(
        str(row.get("project_code") or "").strip()
        or str(row.get("project_name") or "").strip()
    )


def target_budget_rows(*, fiscal_year=None, categories=None, region="",
                       minimum_confidence=0.0, limit=200, offset=0,
                       classifier_version=None,
                       _analysis_rows=None, _candidate_rows=None):
    """Return current procurement-project target candidates only.

    AIDFA APPROPRIATION rows may still be post-classified for structural analysis,
    but they remain context-only and are not exposed as sales/procurement target rows.
    No normalized source row is removed by using this function.
    """
    if categories is None:
        selected = TARGET_CATEGORIES
    else:
        selected = tuple(
            str(value).upper() for value in categories if str(value).strip()
        )
        if not selected:
            return []
    if _candidate_rows is not None:
        rows = list(_candidate_rows)
    elif _analysis_rows is not None:
        rows = target_candidates_from_rows(
            _analysis_rows,
            categories=selected,
            minimum_confidence=minimum_confidence,
        )
    else:
        rows = target_candidates(
            fiscal_year=fiscal_year,
            categories=selected,
            minimum_confidence=minimum_confidence,
            classifier_version=classifier_version,
        )
    rows = [
        row for row in rows
        if str(row.get("source_layer") or "") in {"DETAIL_EXECUTION", "EDUCATION"}
        and _has_sales_project_identity(row)
    ]
    rows = _filter_region(rows, region)
    return _page(rows, limit=limit, offset=offset)


def future_appropriation_rows(*, fiscal_year, categories=None, region="",
                              minimum_confidence=0.0,
                              limit=200, offset=0,
                              classifier_version=None,
                              _analysis_rows=None):
    """Return next-fiscal-year AIDFA target-domain budget signals.

    These are structural appropriation signals, not procurement projects. They are
    deliberately kept separate from target_rows/prebid_rows until a detailed QWGJK
    or education project exists.
    """
    selected = {
        str(value).upper()
        for value in (TARGET_CATEGORIES if categories is None else categories)
        if str(value).strip()
    }
    if not selected:
        return []
    rows = (
        list(_analysis_rows)
        if _analysis_rows is not None
        else current_budget_analysis(
            fiscal_year=int(fiscal_year),
            classifier_version=classifier_version,
        )
    )
    floor = float(minimum_confidence or 0.0)
    result = [
        dict(row)
        for row in rows
        if str(row.get("source_layer") or "") == "APPROPRIATION"
        and bool(row.get("classification_current"))
        and str(row.get("primary_category") or "").upper() in selected
        and float(row.get("classification_confidence") or 0) >= floor
        and int(row.get("fiscal_year") or 0) == int(fiscal_year)
    ]
    result = _filter_region(result, region)
    result.sort(key=lambda row: (
        -int(row.get("budget_amount") or row.get("appropriation_amount") or 0),
        str(row.get("org_name") or ""),
        str(row.get("project_name") or ""),
        str(row.get("raw_source_key") or ""),
    ))
    return _page(result, limit=limit, offset=offset)


def appropriation_context_rows(*, fiscal_year=None, region="",
                               limit=200, offset=0, _analysis_rows=None):
    """Return exact current AIDFA -> QWGJK structural budget context.

    This is context-only: it does not promote AIDFA rows into procurement projects
    and performs no source traffic or persistence.
    """
    if _analysis_rows is not None:
        analysis_rows = _filter_region(_analysis_rows, region)
        rows = exact_appropriation_detail_links_from_rows(
            analysis_rows,
            fiscal_year=fiscal_year,
        )
    elif str(region or "").strip():
        analysis_rows = _filter_region(
            current_budget_analysis(fiscal_year=fiscal_year),
            region,
        )
        rows = exact_appropriation_detail_links_from_rows(
            analysis_rows,
            fiscal_year=fiscal_year,
        )
    else:
        rows = exact_appropriation_detail_links(fiscal_year=fiscal_year)
    return _page(rows, limit=limit, offset=offset)


def prebid_budget_rows(*, fiscal_year=None, categories=None, region="",
                       minimum_classification_confidence=0.0,
                       minimum_match_confidence=0.92,
                       minimum_remaining_amount=0,
                       limit=200, offset=0, classifier_version=None,
                       _analysis_rows=None, _candidate_rows=None):
    """Return current positive-balance sales candidates without bid/service linkage.

    G2B 4.x intentionally stops at budget-derived sales opportunities. Bid notices,
    service awards and contracts belong to NO1 and are not consulted here.
    """
    del minimum_match_confidence
    selected = TARGET_CATEGORIES if categories is None else tuple(
        str(value).upper() for value in categories if str(value).strip()
    )
    if categories is not None and not selected:
        return []
    floor = int(minimum_remaining_amount or 0)
    if _candidate_rows is not None:
        rows = list(_candidate_rows)
    elif _analysis_rows is not None:
        rows = target_candidates_from_rows(
            _analysis_rows,
            categories=selected,
            minimum_confidence=minimum_classification_confidence,
        )
    else:
        rows = target_candidates(
            fiscal_year=fiscal_year,
            categories=selected,
            minimum_confidence=minimum_classification_confidence,
            classifier_version=classifier_version,
        )
    rows = [
        row for row in rows
        if str(row.get("source_layer") or "") in {"DETAIL_EXECUTION", "EDUCATION"}
        and _has_sales_project_identity(row)
        and int(row.get("remaining_amount") or 0) > floor
    ]
    rows = _filter_region(rows, region)
    rows.sort(key=lambda row: (
        -int(row.get("remaining_amount") or 0),
        str(row.get("org_name") or ""),
        str(row.get("project_name") or ""),
        str(row.get("raw_source_key") or ""),
    ))
    return _page(rows, limit=limit, offset=offset)


def budget_history(project_identity, *, limit=500, offset=0):
    """Return preserved organized history for one stable project identity."""
    rows = budget_timeline(str(project_identity or "").strip())
    return _page(rows, limit=limit, offset=offset)


def _status_from_analysis(rows, *, fiscal_year=None, categories=None,
                          minimum_confidence=0.0, coverage=None):
    coverage_rows = list(coverage) if coverage is not None else projection_coverage()
    organization = organization_summary_from_rows(
        rows,
        fiscal_year=fiscal_year,
        coverage=coverage_rows,
    )
    targets = target_summary_from_rows(
        rows,
        fiscal_year=fiscal_year,
        categories=categories,
        minimum_confidence=minimum_confidence,
    )
    return {
        "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
        "collection": budget_collection_status_vnext.budget_collection_status(),
        "organization": organization,
        "analysis": targets,
        "sales_opportunity_scope": "BUDGET_ONLY_NO_BID_SERVICE_LINKAGE",
        "no1_boundary": "bid notices, service awards and contracts are handled by NO1",
        "projection_coverage": coverage_rows,
        "target_categories": list(TARGET_CATEGORIES),
        "read_only": True,
        "source_traffic": False,
        "selection_stage": "POST_NORMALIZATION_ANALYSIS_ONLY",
        "source_collection_completeness_verified": False,
    }


def budget_status(*, fiscal_year=None, categories=None, region="",
                  minimum_confidence=0.0, minimum_match_confidence=0.92,
                  classifier_version=None, _analysis_rows=None, _coverage=None):
    """Return current organization/classification summary with one state scan."""
    del minimum_match_confidence
    rows = (
        list(_analysis_rows)
        if _analysis_rows is not None
        else current_budget_analysis(
            fiscal_year=fiscal_year,
            classifier_version=classifier_version,
        )
    )
    rows = _filter_region(rows, region)
    status = _status_from_analysis(
        rows,
        fiscal_year=fiscal_year,
        categories=categories,
        minimum_confidence=minimum_confidence,
        coverage=_coverage,
    )
    status["selected_region"] = canonical_region(region) if str(region or "").strip() else ""
    return status

def budget_read_model(*, fiscal_year=None, categories=None, region="",
                      minimum_confidence=0.0,
                      minimum_match_confidence=0.92,
                      limit=200, offset=0, view_pagination=None,
                      classifier_version=None):
    """Build all budget screen views from one current-analysis snapshot."""
    selected_categories = None if categories is None else tuple(
        str(value).upper() for value in categories if str(value).strip()
    )
    pages = _read_model_pagination(
        limit=limit,
        offset=offset,
        view_pagination=view_pagination,
    )

    analysis_rows = current_budget_analysis(
        fiscal_year=fiscal_year,
        classifier_version=classifier_version,
    )
    analysis_rows = _filter_region(analysis_rows, region)
    coverage = projection_coverage()
    candidates = (
        []
        if selected_categories is not None and not selected_categories
        else target_candidates_from_rows(
            analysis_rows,
            categories=(
                TARGET_CATEGORIES
                if selected_categories is None
                else selected_categories
            ),
            minimum_confidence=minimum_confidence,
        )
    )

    return {
        "pagination": pages,
        "status": budget_status(
            fiscal_year=fiscal_year,
            categories=selected_categories,
            region=region,
            minimum_confidence=minimum_confidence,
            minimum_match_confidence=minimum_match_confidence,
            classifier_version=classifier_version,
            _analysis_rows=analysis_rows,
            _coverage=coverage,
        ),
        "current_rows": current_budget_rows(
            fiscal_year=fiscal_year,
            categories=selected_categories,
            region=region,
            limit=pages["current_rows"]["limit"],
            offset=pages["current_rows"]["offset"],
            classifier_version=classifier_version,
            _analysis_rows=analysis_rows,
        ),
        "target_rows": target_budget_rows(
            fiscal_year=fiscal_year,
            categories=selected_categories,
            region=region,
            minimum_confidence=minimum_confidence,
            limit=pages["target_rows"]["limit"],
            offset=pages["target_rows"]["offset"],
            classifier_version=classifier_version,
            _analysis_rows=analysis_rows,
            _candidate_rows=candidates,
        ),
        "appropriation_context": appropriation_context_rows(
            fiscal_year=fiscal_year,
            region=region,
            limit=pages["appropriation_context"]["limit"],
            offset=pages["appropriation_context"]["offset"],
            _analysis_rows=analysis_rows,
        ),
        "prebid_rows": prebid_budget_rows(
            fiscal_year=fiscal_year,
            categories=selected_categories,
            region=region,
            minimum_classification_confidence=minimum_confidence,
            minimum_match_confidence=minimum_match_confidence,
            limit=pages["prebid_rows"]["limit"],
            offset=pages["prebid_rows"]["offset"],
            classifier_version=classifier_version,
            _analysis_rows=analysis_rows,
            _candidate_rows=candidates,
        ),
    }
