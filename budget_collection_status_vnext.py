"""Read-only local collection status for G2B vNext budget sources.

This module reports what is currently stored locally for QWGJK, AIDFA and education
budget datasets.  It does not run collectors, repair checkpoints, normalize rows or
claim that an official source has been completely collected.

A checkpoint with status=COMPLETE is counted as verified only when the vNext page/
item receipts still pass `vnext_collection.verified_checkpoint`.
"""
from __future__ import annotations

import json

from db import connect
from vnext_collection import verified_checkpoint

BUDGET_DATASETS = ("budget", "budget_appropriation", "education_budget")


def _generation(checkpoint):
    try:
        meta = json.loads(str(checkpoint.get("cursor_value") or "{}"))
        return str(meta.get("generation") or "") if isinstance(meta, dict) else ""
    except (TypeError, ValueError):
        return ""


def _dataset_counts(dataset):
    with connect() as conn:
        raw_rows = int(conn.execute(
            "SELECT COUNT(*) FROM raw_records WHERE dataset=?",
            (dataset,),
        ).fetchone()[0] or 0)
        raw_revisions = int(conn.execute(
            "SELECT COUNT(*) FROM raw_record_revisions WHERE dataset=?",
            (dataset,),
        ).fetchone()[0] or 0)
        checkpoints = [
            dict(row) for row in conn.execute(
                """SELECT dataset,scope_key,cursor_value,range_start,range_end,
                          page_no,page_size,last_page_fingerprint,
                          source_total,fetched_count,saved_count,status,last_error,updated_at
                   FROM collection_checkpoints
                   WHERE dataset=?
                   ORDER BY scope_key""",
                (dataset,),
            ).fetchall()
        ]

    scopes = []
    status_counts = {}
    verified_complete = 0
    unverified_complete = 0
    for checkpoint in checkpoints:
        status = str(checkpoint.get("status") or "IDLE")
        status_counts[status] = status_counts.get(status, 0) + 1
        receipt_verified = bool(
            status == "COMPLETE" and verified_checkpoint(checkpoint)
        )
        if status == "COMPLETE":
            if receipt_verified:
                verified_complete += 1
            else:
                unverified_complete += 1
        scopes.append({
            "scope_key": str(checkpoint.get("scope_key") or ""),
            "range_start": str(checkpoint.get("range_start") or ""),
            "range_end": str(checkpoint.get("range_end") or ""),
            "page_no": int(checkpoint.get("page_no") or 0),
            "page_size": int(checkpoint.get("page_size") or 0),
            "source_total": int(checkpoint.get("source_total") or 0),
            "fetched_count": int(checkpoint.get("fetched_count") or 0),
            "saved_count": int(checkpoint.get("saved_count") or 0),
            "status": status,
            "last_error": str(checkpoint.get("last_error") or ""),
            "updated_at": str(checkpoint.get("updated_at") or ""),
            "receipt_verified": receipt_verified,
        })

    verified_current_keys = set()
    for checkpoint, scope in zip(checkpoints, scopes):
        if not scope["receipt_verified"]:
            continue
        generation = _generation(checkpoint)
        if not generation:
            continue
        with connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT i.source_key
                   FROM vnext_collection_items i
                   JOIN raw_records r
                     ON r.dataset=i.dataset AND r.source_key=i.source_key
                    AND r.payload_sha256=i.payload_sha256
                   WHERE i.dataset=? AND i.scope_key=? AND i.generation=?""",
                (dataset, checkpoint["scope_key"], generation),
            ).fetchall()
        verified_current_keys.update(str(row["source_key"]) for row in rows)

    verified_current_raw = len(verified_current_keys)
    unverified_current_raw = max(0, raw_rows - verified_current_raw)

    return {
        "dataset": dataset,
        "raw_rows": raw_rows,
        "raw_revisions": raw_revisions,
        "checkpoint_count": len(checkpoints),
        "checkpoint_status_counts": dict(sorted(status_counts.items())),
        "verified_complete_scopes": verified_complete,
        "unverified_complete_scopes": unverified_complete,
        "current_raw_with_verified_complete_scope": verified_current_raw,
        "current_raw_without_verified_complete_scope": unverified_current_raw,
        "scopes": scopes,
    }


def budget_collection_status():
    """Return CURRENT_STORED_RAW_AND_CHECKPOINTS_ONLY budget collection status."""
    datasets = [_dataset_counts(dataset) for dataset in BUDGET_DATASETS]
    return {
        "scope": "CURRENT_STORED_RAW_AND_CHECKPOINTS_ONLY",
        "datasets": datasets,
        "totals": {
            "raw_rows": sum(row["raw_rows"] for row in datasets),
            "raw_revisions": sum(row["raw_revisions"] for row in datasets),
            "checkpoints": sum(row["checkpoint_count"] for row in datasets),
            "verified_complete_scopes": sum(
                row["verified_complete_scopes"] for row in datasets
            ),
            "unverified_complete_scopes": sum(
                row["unverified_complete_scopes"] for row in datasets
            ),
            "current_raw_with_verified_complete_scope": sum(
                row["current_raw_with_verified_complete_scope"] for row in datasets
            ),
            "current_raw_without_verified_complete_scope": sum(
                row["current_raw_without_verified_complete_scope"] for row in datasets
            ),
        },
        "read_only": True,
        "source_traffic": False,
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason":
            "NOT_EVALUATED_BY_LOCAL_COLLECTION_STATUS",
    }
