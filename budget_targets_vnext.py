"""Post-RAW budget target analysis for G2B vNext.

This module deliberately starts only after collection. It organizes current budget
rows, attaches the existing vNext classification for the exact current RAW payload,
and exposes target-domain candidates without deleting or hiding OTHER source rows.

No source traffic occurs here.
"""
from __future__ import annotations

from db import connect
import budget_organization_vnext
import budget_projection_vnext
import classification_vnext
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

BUDGET_DATASETS = ("budget", "budget_appropriation", "education_budget")
TARGET_CATEGORIES = ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")


def prepare_budget_analysis(*, batch_size=1000):
    """Organize current stored budget RAW and run post-RAW classification.

    This is an offline/database-only operation. It never calls LOFIN, G2B or the
    education-budget source.
    """
    projection = budget_projection_vnext.refresh_budget_projection(datasets=BUDGET_DATASETS)
    classified = []
    for dataset in BUDGET_DATASETS:
        classified.append(classification_vnext.classify_dataset(dataset, batch_size=batch_size))
    return {
        "projection": projection,
        "classification": classified,
        "source_traffic": False,
        "source_collection_completeness_verified": False,
    }


def _classification_map(current_rows, classifier_version):
    keys = [(str(row["raw_dataset"]), str(row["raw_source_key"])) for row in current_rows]
    if not keys:
        return {}
    wanted = set(keys)
    datasets = sorted({dataset for dataset, _ in keys})
    placeholders = ",".join("?" for _ in datasets)
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = conn.execute(
            f"""SELECT entity_type,entity_key,primary_category,subcategory,confidence,reason,
                       classifier_version,source_payload_sha256,classified_at
                FROM classifications
                WHERE classifier_version=? AND entity_type IN ({placeholders})""",
            (str(classifier_version), *datasets),
        ).fetchall()
    return {
        (str(row["entity_type"]), str(row["entity_key"])): dict(row)
        for row in rows
        if (str(row["entity_type"]), str(row["entity_key"])) in wanted
    }


def current_budget_analysis(*, fiscal_year=None, classifier_version=None):
    """Return every current budget project with exact-current-payload classification.

    Rows without a current matching classification are returned as UNCLASSIFIED rather
    than being omitted. ``OTHER`` rows are also returned.
    """
    version = classifier_version or CLASSIFIER_VERSION
    current = budget_organization_vnext.current_budget_state(fiscal_year=fiscal_year)
    cmap = _classification_map(current, version)
    result = []
    for row in current:
        item = dict(row)
        cls = cmap.get((str(item["raw_dataset"]), str(item["raw_source_key"])))
        if cls and str(cls.get("source_payload_sha256") or "") == str(item.get("payload_sha256") or ""):
            item.update({
                "primary_category": str(cls.get("primary_category") or "UNCLASSIFIED"),
                "subcategory": str(cls.get("subcategory") or ""),
                "classification_confidence": float(cls.get("confidence") or 0),
                "classification_reason": str(cls.get("reason") or ""),
                "classifier_version": str(cls.get("classifier_version") or version),
                "classification_current": True,
            })
        else:
            item.update({
                "primary_category": "UNCLASSIFIED",
                "subcategory": "",
                "classification_confidence": 0.0,
                "classification_reason": "no classification for exact current RAW payload",
                "classifier_version": str(version),
                "classification_current": False,
            })
        result.append(item)
    return result


def target_candidates(*, fiscal_year=None, categories=None, minimum_confidence=0.0,
                      classifier_version=None):
    """Return analysis-time target candidates from current organized budget rows.

    Collection remains complete/unfiltered; this function is only a downstream view.
    """
    selected = {str(value).upper() for value in (categories or TARGET_CATEGORIES)}
    floor = float(minimum_confidence or 0.0)
    rows = [
        row for row in current_budget_analysis(
            fiscal_year=fiscal_year, classifier_version=classifier_version
        )
        if row["classification_current"]
        and str(row["primary_category"]).upper() in selected
        and float(row["classification_confidence"] or 0) >= floor
    ]
    rows.sort(key=lambda row: (
        -int(row.get("fiscal_year") or 0),
        -int(row.get("remaining_amount") or row.get("budget_amount") or 0),
        str(row.get("org_name") or ""),
        str(row.get("project_name") or ""),
    ))
    return rows


def target_summary(*, fiscal_year=None, classifier_version=None):
    rows = current_budget_analysis(fiscal_year=fiscal_year, classifier_version=classifier_version)
    summary = {}
    for row in rows:
        category = str(row.get("primary_category") or "UNCLASSIFIED")
        item = summary.setdefault(category, {
            "projects": 0,
            "budget_amount": 0,
            "executed_amount": 0,
            "remaining_amount": 0,
        })
        item["projects"] += 1
        item["budget_amount"] += int(row.get("budget_amount") or 0)
        item["executed_amount"] += int(row.get("executed_amount") or 0)
        item["remaining_amount"] += int(row.get("remaining_amount") or 0)
    return {
        "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
        "current_projects": len(rows),
        "by_category": summary,
        "target_categories": list(TARGET_CATEGORIES),
        "selection_stage": "POST_RAW_ANALYSIS_ONLY",
        "source_collection_completeness_verified": False,
    }
