"""Offline re-organization of already-collected G2B vNext service RAW.

This module intentionally performs no source collection. It exists so previously
stored notice/opening/final-award/contract RAW can be re-normalized and reclassified
after deterministic normalization rules improve, without spending API calls.

Order:
    opening RAW -> final-award RAW -> contract RAW -> classification -> status
"""
from __future__ import annotations

from db import connect
import award_projection
import classification_vnext
import contract_projection
import service_lifecycle_status_vnext
from vnext_schema import ensure_vnext_schema

SERVICE_CLASSIFICATION_DATASETS = (
    "bid_notice_service",
    "opening_result_service",
    "award_result_service",
    "contract_service",
)


def _raw_counts():
    with connect() as conn:
        ensure_vnext_schema(conn)
        return {
            dataset: int(conn.execute(
                "SELECT COUNT(*) FROM raw_records WHERE dataset=?",
                (dataset,),
            ).fetchone()[0] or 0)
            for dataset in SERVICE_CLASSIFICATION_DATASETS
        }


def reorganize_existing_service_lifecycle(*, normalize_limit=None,
                                          classify_batch_size=1000,
                                          run_classification=True):
    """Rebuild derived service lifecycle state from current stored RAW only.

    No collector/fetch function is called. RAW rows/revisions are not inserted or
    deleted. Existing RAW rows may receive updated normalization markers because the
    derived projections have been rebuilt from their current payloads.
    """
    before = _raw_counts()

    opening = award_projection.normalize_dataset(
        award_projection.OPENING_DATASET,
        limit=normalize_limit,
    )
    final_award = award_projection.normalize_dataset(
        award_projection.AWARD_DATASET,
        limit=normalize_limit,
    )
    contract = contract_projection.normalize_contracts(limit=normalize_limit)

    normalization_complete = all(
        bool(item.get("complete"))
        for item in (opening, final_award, contract)
    )

    classification = None
    if run_classification and normalization_complete:
        classification = classification_vnext.classify_all(
            datasets=SERVICE_CLASSIFICATION_DATASETS,
            batch_size=classify_batch_size,
        )

    after = _raw_counts()
    raw_counts_unchanged = before == after
    status = service_lifecycle_status_vnext.service_lifecycle_organization_status()

    return {
        "source_traffic": False,
        "mode": "EXISTING_RAW_REORGANIZATION_ONLY",
        "normalizer_only": True,
        "raw_counts_before": before,
        "raw_counts_after": after,
        "raw_counts_unchanged": raw_counts_unchanged,
        "opening": opening,
        "final_award": final_award,
        "contract": contract,
        "classification": classification,
        "organization_status": status,
        "complete": bool(normalization_complete and raw_counts_unchanged),
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason": "NOT_EVALUATED_BY_OFFLINE_REORGANIZATION",
    }
