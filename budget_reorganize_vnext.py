"""Offline re-organization of already-collected vNext budget RAW.

This module performs no LOFIN/education source requests. It rebuilds the canonical
budget projection and exact-current-payload classifications from RAW already stored
for QWGJK, AIDFA and education-budget datasets.
"""
from __future__ import annotations

from db import connect
import budget_organization_vnext
import budget_targets_vnext
from vnext_schema import ensure_vnext_schema

BUDGET_DATASETS = budget_targets_vnext.BUDGET_DATASETS


def _preservation_counts():
    """Count current RAW and immutable revisions without changing either store."""
    with connect() as conn:
        ensure_vnext_schema(conn)
        return {
            "raw": {
                dataset: int(conn.execute(
                    "SELECT COUNT(*) FROM raw_records WHERE dataset=?",
                    (dataset,),
                ).fetchone()[0] or 0)
                for dataset in BUDGET_DATASETS
            },
            "revisions": {
                dataset: int(conn.execute(
                    "SELECT COUNT(*) FROM raw_record_revisions WHERE dataset=?",
                    (dataset,),
                ).fetchone()[0] or 0)
                for dataset in BUDGET_DATASETS
            },
        }


def reorganize_existing_budget_raw(*, batch_size=1000, fiscal_year=None):
    """Rebuild budget projection/classification using current stored RAW only."""
    before = _preservation_counts()
    prepared = budget_targets_vnext.prepare_budget_analysis(batch_size=batch_size)
    after = _preservation_counts()
    coverage = budget_organization_vnext.projection_coverage()
    current = budget_targets_vnext.current_budget_analysis(fiscal_year=fiscal_year)
    target_summary = budget_targets_vnext.target_summary(fiscal_year=fiscal_year)

    projection_current = all(
        bool(row.get("projection_complete_for_current_raw"))
        for row in coverage
    )
    classification_current = all(
        bool(row.get("classification_current"))
        for row in current
    )
    raw_counts_unchanged = before["raw"] == after["raw"]
    revision_counts_unchanged = before["revisions"] == after["revisions"]

    return {
        "mode": "EXISTING_RAW_REORGANIZATION_ONLY",
        "source_traffic": False,
        "raw_counts_before": before["raw"],
        "raw_counts_after": after["raw"],
        "raw_counts_unchanged": raw_counts_unchanged,
        "revision_counts_before": before["revisions"],
        "revision_counts_after": after["revisions"],
        "revision_counts_unchanged": revision_counts_unchanged,
        "projection": prepared["projection"],
        "classification": prepared["classification"],
        "projection_coverage": coverage,
        "current_projects": len(current),
        "classification_current_for_organized_rows": classification_current,
        "target_summary": target_summary,
        "complete": bool(
            raw_counts_unchanged and revision_counts_unchanged
            and projection_current and classification_current
        ),
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason": "NOT_EVALUATED_BY_OFFLINE_REORGANIZATION",
    }
