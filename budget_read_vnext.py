"""Read-only consumer interface for organized G2B vNext budget data.

This module is intentionally small. It does not collect, normalize, classify or
refresh data. It only exposes already-prepared vNext budget state in a stable shape
that the existing AI-SPECE dashboard/API layer can consume later without changing
legacy production files.

Data flow remains:
    full collection -> RAW -> organization/projection -> post-classification -> read
"""
from __future__ import annotations

import budget_notice_links_vnext
from budget_organization_vnext import (
    budget_timeline,
    organization_summary,
    projection_coverage,
)
from budget_targets_vnext import (
    TARGET_CATEGORIES,
    current_budget_analysis,
    target_candidates,
    target_summary,
)


def _page(rows, *, limit=200, offset=0):
    start = max(0, int(offset))
    size = max(1, min(int(limit), 5000))
    return rows[start:start + size]


def current_budget_rows(*, fiscal_year=None, categories=None, limit=200, offset=0,
                        classifier_version=None):
    """Return current organized budget rows, including OTHER by default.

    ``categories=None`` means no analysis filter. Supplying an explicit empty list
    means return no rows. This keeps target filtering downstream from collection.
    """
    rows = current_budget_analysis(
        fiscal_year=fiscal_year, classifier_version=classifier_version
    )
    if categories is not None:
        selected = {str(value).upper() for value in categories if str(value).strip()}
        if not selected:
            return []
        rows = [
            row for row in rows
            if str(row.get("primary_category") or "").upper() in selected
        ]
    return _page(rows, limit=limit, offset=offset)


def target_budget_rows(*, fiscal_year=None, categories=None, minimum_confidence=0.0,
                       limit=200, offset=0, classifier_version=None):
    """Return current LIGHTING/POLE/ELECTRICAL/SOLAR candidates only.

    This is a read-only analysis view. No RAW row is removed by using this function.
    """
    selected = categories if categories is not None else TARGET_CATEGORIES
    if categories is not None and not list(categories):
        return []
    rows = target_candidates(
        fiscal_year=fiscal_year,
        categories=selected,
        minimum_confidence=minimum_confidence,
        classifier_version=classifier_version,
    )
    return _page(rows, limit=limit, offset=offset)


def procurement_candidate_rows(*, fiscal_year=None, categories=None,
                               minimum_classification_confidence=0.0,
                               minimum_match_confidence=0.92,
                               limit=200, offset=0, classifier_version=None):
    """Return conservative budget -> stored G2B notice candidates.

    These are review candidates only. No lifecycle link is persisted and no source
    request is performed here.
    """
    rows = budget_notice_links_vnext.budget_notice_candidates(
        fiscal_year=fiscal_year,
        categories=categories,
        minimum_classification_confidence=minimum_classification_confidence,
        minimum_match_confidence=minimum_match_confidence,
        classifier_version=classifier_version,
        limit=max(1, int(limit)) + max(0, int(offset)),
    )
    return _page(rows, limit=limit, offset=offset)


def budget_history(project_identity, *, limit=500, offset=0):
    """Return preserved organized history for one stable project identity."""
    rows = budget_timeline(str(project_identity or "").strip())
    return _page(rows, limit=limit, offset=offset)


def budget_status(*, fiscal_year=None, classifier_version=None):
    """Return current organization/classification summary without a completeness claim."""
    organization = organization_summary(fiscal_year=fiscal_year)
    targets = target_summary(
        fiscal_year=fiscal_year, classifier_version=classifier_version
    )
    return {
        "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
        "organization": organization,
        "analysis": targets,
        "projection_coverage": projection_coverage(),
        "target_categories": list(TARGET_CATEGORIES),
        "read_only": True,
        "source_traffic": False,
        "selection_stage": "POST_RAW_ANALYSIS_ONLY",
        "source_collection_completeness_verified": False,
    }


def budget_read_model(*, fiscal_year=None, categories=None, minimum_confidence=0.0,
                      minimum_match_confidence=0.92,
                      limit=200, offset=0, classifier_version=None):
    """One-call payload for a future existing-AI-SPECE budget screen/API adapter."""
    return {
        "status": budget_status(
            fiscal_year=fiscal_year, classifier_version=classifier_version
        ),
        "current_rows": current_budget_rows(
            fiscal_year=fiscal_year,
            categories=categories,
            limit=limit,
            offset=offset,
            classifier_version=classifier_version,
        ),
        "target_rows": target_budget_rows(
            fiscal_year=fiscal_year,
            minimum_confidence=minimum_confidence,
            limit=limit,
            offset=offset,
            classifier_version=classifier_version,
        ),
        "procurement_candidates": procurement_candidate_rows(
            fiscal_year=fiscal_year,
            categories=categories,
            minimum_classification_confidence=minimum_confidence,
            minimum_match_confidence=minimum_match_confidence,
            limit=limit,
            offset=offset,
            classifier_version=classifier_version,
        ),
    }
