"""Read-only consumer interface for organized G2B vNext budget data.

This module is intentionally small. It does not collect, normalize, classify or
refresh data. It only exposes already-prepared vNext budget state in a stable shape
that the existing AI-SPECE dashboard/API layer can consume later without changing
legacy production files.

Data flow remains:
    source collection -> normalization -> organization/classification -> read
"""
from __future__ import annotations

import datetime as dt

import admin_geography_v41
import budget_collection_status_vnext
import budget_storage
BUDGET_DATASETS = tuple(budget_storage.BUDGET_DATASETS)
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


REGIONS = admin_geography_v41.REGIONS


def canonical_region(value):
    """Map source names to the accepted historical/current top-level region."""
    return admin_geography_v41.canonical_region(value)

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
    if not str(region or "").strip():
        return True
    members = set(admin_geography_v41.region_history_members(region))
    return bool(members and row_region(row) in members)


def _filter_region(rows, region):
    if not str(region or "").strip():
        return list(rows)
    members = set(admin_geography_v41.region_history_members(region))
    if not members:
        return []
    return [row for row in rows if row_region(row) in members]


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


def collected_budget_rows(*, fiscal_year=None, categories=None, region="",
                          limit=200, offset=0):
    """Return canonical stored current budget facts without projection dependency."""
    import budget_normalizer_v41
    import classification_vnext

    rows = budget_storage.current_normalized_rows(
        BUDGET_DATASETS,
        fiscal_year=fiscal_year,
    )
    rows = _filter_region(rows, region)

    selected = None
    if categories is not None:
        selected = {
            str(value).upper()
            for value in categories
            if str(value).strip()
        }
        if not selected:
            return []

    out = []
    for row in rows:
        item = dict(row)
        dataset = str(item.get("dataset") or "")
        payload = budget_normalizer_v41.compat_payload(dataset, item)
        classified = classification_vnext.classify_payload(dataset, payload)
        item["raw_dataset"] = dataset
        item["raw_source_key"] = str(item.get("record_key") or "")
        item["primary_category"] = str(
            classified.get("primary_category") or "UNCLASSIFIED"
        )
        item["subcategory"] = str(classified.get("subcategory") or "")
        item["classification_confidence"] = float(
            classified.get("confidence") or 0
        )
        item["classification_reason"] = str(
            classified.get("reason") or ""
        )
        item["classification_current"] = True
        if selected is not None and item["primary_category"].upper() not in selected:
            continue
        out.append(item)

    layer_priority = {
        "DETAIL_EXECUTION": 0,
        "EDUCATION": 1,
        "APPROPRIATION": 2,
    }
    out.sort(key=lambda row: (
        -int(row.get("fiscal_year") or 0),
        layer_priority.get(str(row.get("source_layer") or ""), 9),
        str(row.get("region_name") or ""),
        str(row.get("org_name") or ""),
        str(row.get("project_name") or ""),
        str(row.get("record_key") or ""),
    ))
    return _page(out, limit=limit, offset=offset)


def _region_search_terms(region):
    members = admin_geography_v41.region_history_members(region)
    if not members:
        return ()
    result = []
    for selected in members:
        short = (
            selected.replace("특별자치도", "")
            .replace("특별자치시", "")
            .replace("광역시", "")
            .replace("특별시", "")
            .replace("도", "")
        )
        if selected not in result:
            result.append(selected)
        if short and short not in result:
            result.append(short)
    return tuple(result)


def qwgjk_history_rows(
    *,
    start_date,
    end_date,
    region="",
    institution_scope="",
    query="",
    categories=None,
    limit=300,
    offset=0,
    allow_match_backfill=False,
):
    """Return read-only QWGJK normalized revisions for an inclusive date range."""
    start = dt.date.fromisoformat(str(start_date))
    end = dt.date.fromisoformat(str(end_date))
    floor = (
        dt.date(2025, 1, 1)
        if bool(allow_match_backfill)
        else dt.date(2026, 1, 1)
    )
    if start < floor:
        start = floor
    if end < floor or start > end:
        return []

    rows = budget_storage.revision_project_rows(
        "budget",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        region_terms=_region_search_terms(region),
        query=str(query or "").strip(),
        limit=5000,
        offset=0,
    )
    rows = _filter_region(rows, region)
    if (
        canonical_region(region) == "인천광역시"
        and str(institution_scope or "").strip()
    ):
        import incheon_budget_scope_vnext
        rows = [
            row for row in rows
            if incheon_budget_scope_vnext.matches_row(
                row, institution_scope
            )
        ]

    selected = None
    if categories is not None:
        selected = {
            str(value).upper()
            for value in categories
            if str(value).strip()
        }
        if not selected:
            return []

    import budget_normalizer_v41
    import classification_vnext

    classified_rows = []
    for row in rows:
        item = dict(row)
        payload = budget_normalizer_v41.compat_payload("budget", item)
        classified = classification_vnext.classify_payload("budget", payload)
        item["raw_dataset"] = "budget"
        item["raw_source_key"] = str(item.get("record_key") or "")
        item["primary_category"] = str(
            classified.get("primary_category") or "UNCLASSIFIED"
        )
        item["subcategory"] = str(classified.get("subcategory") or "")
        import budget_organization_vnext
        item["project_identity"] = budget_organization_vnext._identity_from_fact(
            item,
            raw_source_key=item["raw_source_key"],
            source_operation=str(item.get("source_operation") or ""),
            source_system=str(item.get("source_system") or ""),
        )
        if (
            selected is not None
            and item["primary_category"].upper() not in selected
        ):
            continue
        classified_rows.append(item)

    grouped_previous = {}
    enriched = []
    for item in reversed(classified_rows):
        item = dict(item)
        stable_key = "|".join((
            str(item.get("org_code") or item.get("org_name") or ""),
            str(item.get("dept_code") or item.get("dept_name") or ""),
            str(item.get("project_code") or item.get("project_name") or ""),
            str(item.get("account_code") or item.get("account_name") or ""),
        ))
        previous = grouped_previous.get(stable_key)
        current_amounts = (
            int(item.get("budget_amount") or 0),
            int(item.get("executed_amount") or 0),
            int(item.get("remaining_amount") or 0),
        )
        if previous is None:
            item["budget_change"] = None
            item["executed_change"] = None
            item["remaining_change"] = None
        else:
            item["budget_change"] = current_amounts[0] - previous[0]
            item["executed_change"] = current_amounts[1] - previous[1]
            item["remaining_change"] = current_amounts[2] - previous[2]
        grouped_previous[stable_key] = current_amounts
        enriched.append(item)

    enriched.reverse()
    start_at = max(0, int(offset or 0))
    size = max(1, min(int(limit), 5000))
    return enriched[start_at:start_at + size]


def screen_budget_rows(
    *,
    fiscal_year,
    source_layers,
    categories=None,
    region="",
    institution_scope="",
    query="",
    execution_status="",
    limit=200,
    offset=0,
):
    """Return a bounded budget-page slice without pre-classification truncation.

    PostgreSQL category filters join the persisted exact-current classification
    before LIMIT/OFFSET. The test-only SQLite fallback may scan its tiny isolated
    fixture, but production never scans an arbitrary first 2,000 rows and then
    decides category membership.
    """
    import budget_normalizer_v41
    import budget_organization_vnext
    import classification_vnext

    size = max(1, min(int(limit), 500))
    start_at = max(0, int(offset or 0))
    selected = None
    if categories is not None:
        selected = {
            str(value).upper()
            for value in categories
            if str(value).strip()
        }
        if not selected:
            return []

    import incheon_budget_scope_vnext

    scope_spec = (
        incheon_budget_scope_vnext.scope_filter(institution_scope)
        if canonical_region(region) == "인천광역시"
        else {
            "exact_names": (),
            "contains_terms": (),
        }
    )
    storage_kwargs = dict(
        fiscal_year=int(fiscal_year),
        source_layers=tuple(source_layers or ()),
        region_terms=_region_search_terms(region),
        organization_exact_names=tuple(scope_spec.get("exact_names") or ()),
        organization_contains_terms=tuple(
            scope_spec.get("contains_terms") or ()
        ),
        query=str(query or "").strip(),
        execution_status=str(execution_status or "").strip(),
    )

    if budget_storage.using_postgres():
        rows = budget_storage.current_normalized_rows(
            BUDGET_DATASETS,
            categories=(tuple(sorted(selected)) if selected is not None else None),
            classifier_version=classification_vnext.CLASSIFIER_VERSION,
            limit=size,
            offset=start_at,
            **storage_kwargs,
        )
    else:
        # Isolated regression fixtures are intentionally small. Apply paging only
        # after in-memory classification so tests exercise the same semantics.
        rows = budget_storage.current_normalized_rows(
            BUDGET_DATASETS,
            limit=None,
            offset=0,
            **storage_kwargs,
        )

    rows = _filter_region(rows, region)
    result = []
    for row in rows:
        item = dict(row)
        dataset = str(item.get("dataset") or "")
        stored_category = str(item.get("primary_category") or "")
        stored_subcategory = str(item.get("subcategory") or "")
        if (
            budget_storage.using_postgres()
            and selected is not None
            and stored_category
        ):
            item["raw_dataset"] = dataset
            item["raw_source_key"] = str(item.get("record_key") or "")
            item["primary_category"] = stored_category
            item["subcategory"] = stored_subcategory
            item["classification_confidence"] = float(
                item.get("classification_confidence") or 0
            )
            item["classification_reason"] = str(
                item.get("classification_reason") or ""
            )
            item["classification_current"] = True
        else:
            payload = budget_normalizer_v41.compat_payload(dataset, item)
            classified = classification_vnext.classify_payload(dataset, payload)
            item["raw_dataset"] = dataset
            item["raw_source_key"] = str(item.get("record_key") or "")
            item["primary_category"] = str(
                classified.get("primary_category") or "UNCLASSIFIED"
            )
            item["subcategory"] = str(classified.get("subcategory") or "")
            item["classification_confidence"] = float(
                classified.get("confidence") or 0
            )
            item["classification_reason"] = str(
                classified.get("reason") or ""
            )
            item["classification_current"] = True

        item["project_identity"] = budget_organization_vnext._identity_from_fact(
            item,
            raw_source_key=item["raw_source_key"],
            source_operation=str(item.get("source_operation") or ""),
            source_system=str(item.get("source_system") or ""),
        )
        if (
            selected is not None
            and item["primary_category"].upper() not in selected
        ):
            continue
        result.append(item)

    if budget_storage.using_postgres():
        return result[:size]
    return result[start_at:start_at + size]


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

def bounded_budget_api_model(*, fiscal_year=None, region="", limit=500):
    """Build the HTTP API model from storage-bounded slices only.

    The legacy budget_read_model intentionally builds a complete in-memory
    analysis snapshot for analytical callers. That is inappropriate for a small
    web process because /api/budget may be called during ordinary operations.
    This API-specific model pushes LIMIT/filtering into PostgreSQL before rows
    enter Python and keeps every returned collection bounded.
    """
    year = int(fiscal_year or dt.date.today().year)
    size = max(1, min(int(limit), 500))
    selected_region = (
        canonical_region(region)
        if str(region or "").strip()
        else ""
    )

    current_rows = screen_budget_rows(
        fiscal_year=year,
        source_layers=("DETAIL_EXECUTION", "EDUCATION", "APPROPRIATION"),
        region=selected_region,
        limit=size,
        offset=0,
    )
    target_rows = screen_budget_rows(
        fiscal_year=year,
        source_layers=("DETAIL_EXECUTION", "EDUCATION"),
        categories=TARGET_CATEGORIES,
        region=selected_region,
        limit=size,
        offset=0,
    )
    prebid_rows = sorted(
        (
            row for row in target_rows
            if int(row.get("remaining_amount") or 0) > 0
        ),
        key=lambda row: (
            -int(row.get("remaining_amount") or 0),
            str(row.get("org_name") or ""),
            str(row.get("project_name") or ""),
            str(row.get("raw_source_key") or ""),
        ),
    )[:size]
    appropriation_context = exact_appropriation_detail_links_from_rows(
        current_rows,
        fiscal_year=year,
    )[:size]

    counts = budget_storage.dataset_counts_all(BUDGET_DATASETS)
    current_record_count = sum(
        int(item.get("current_records") or 0)
        for item in counts.values()
    )

    return {
        "pagination": {
            name: {"limit": size, "offset": 0}
            for name in READ_MODEL_VIEWS
        },
        "status": {
            "read_only": True,
            "source_traffic": False,
            "source_collection_completeness_verified": False,
            "selection_stage": "STORAGE_BOUNDED_API_READ",
            "selected_region": selected_region,
            "selected_fiscal_year": year,
            "api_read_mode": "BOUNDED_STORAGE_PAGE",
            "page_limit": size,
            "stored_current_records": current_record_count,
            "dataset_counts": counts,
        },
        "current_rows": current_rows,
        "target_rows": target_rows,
        "appropriation_context": appropriation_context,
        "prebid_rows": prebid_rows,
        "selected_region": selected_region,
        "selected_fiscal_year": year,
        "source": "POSTGRESQL_BOUNDED_READ" if budget_storage.using_postgres() else "SQLITE_BOUNDED_READ",
    }


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
