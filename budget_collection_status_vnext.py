"""Read-only local collection status for G2B vNext budget sources.

This module reports what is currently stored locally for QWGJK, AIDFA and education
budget datasets.  It does not run collectors, repair checkpoints, normalize rows or
claim that an official source has been completely collected.

A checkpoint with status=COMPLETE is counted as verified only when the vNext page/
item receipts still pass `vnext_collection.verified_checkpoint`.
"""
from __future__ import annotations

import os
import time

import budget_pg_store
import budget_storage
from db import connect
from vnext_collection import verified_checkpoint as sqlite_verified_checkpoint

BUDGET_DATASETS = ("budget", "budget_appropriation", "education_budget")
_STATUS_CACHE = {"at": 0.0, "value": None}
_MONITOR_STATUS_CACHE = {"at": 0.0, "value": None}


def _status_cache_seconds():
    if str(os.getenv("G2B_TEST_MODE", "0")).lower() in {"1", "true", "yes", "on"}:
        return 0
    try:
        value = int(str(os.getenv("G2B_BUDGET_STATUS_CACHE_SECONDS", "15") or "15"))
    except (TypeError, ValueError):
        value = 15
    return max(0, min(value, 60))


def _monitor_status_cache_seconds():
    """Keep the auto-refresh monitor cheap without changing collector semantics."""
    if str(os.getenv("G2B_TEST_MODE", "0")).lower() in {"1", "true", "yes", "on"}:
        return 0
    try:
        value = int(
            str(os.getenv("G2B_MONITOR_STATUS_CACHE_SECONDS", "30") or "30")
        )
    except (TypeError, ValueError):
        value = 30
    return max(5, min(value, 120))


def _dataset_counts(dataset, *, storage=None, verify_receipts=True):
    if storage is None:
        storage = budget_storage.dataset_counts(dataset)
    raw_rows = int(storage["current_records"])
    raw_revisions = int(storage["observations"])
    using_postgres = budget_storage.using_postgres()
    if using_postgres:
        checkpoints = budget_pg_store.list_checkpoints(dataset)
        if verify_receipts:
            from budget_pg_collection import verified_checkpoint as pg_verified_checkpoint
            receipt_check = lambda checkpoint: pg_verified_checkpoint(
                checkpoint, require_current=False
            )
        else:
            receipt_check = lambda checkpoint: False
    else:
        with connect() as conn:
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
        receipt_check = sqlite_verified_checkpoint if verify_receipts else (lambda checkpoint: False)

    scopes = []
    status_counts = {}
    verified_complete = 0
    compacted_complete = 0
    unverified_complete = 0
    for checkpoint in checkpoints:
        status = str(checkpoint.get("status") or "IDLE")
        status_counts[status] = status_counts.get(status, 0) + 1
        receipt_verified = bool(
            status == "COMPLETE" and receipt_check(checkpoint)
        )
        scope_key = str(checkpoint.get("scope_key") or "")
        receipts_compacted = bool(
            using_postgres
            and dataset == "budget"
            and status == "COMPLETE"
            and scope_key.startswith("history:")
            and not receipt_verified
        )
        if status == "COMPLETE":
            if receipt_verified:
                verified_complete += 1
            elif receipts_compacted:
                compacted_complete += 1
            else:
                unverified_complete += 1
        scopes.append({
            "scope_key": scope_key,
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
            "receipts_compacted": receipts_compacted,
        })

    return {
        "dataset": dataset,
        "scope": "CURRENT_BUDGET_STORAGE_ONLY",
        "raw_backend": budget_storage.backend_name(),
        "raw_rows": raw_rows,
        "raw_revisions": raw_revisions,
        "checkpoint_count": len(checkpoints),
        "checkpoint_status_counts": dict(sorted(status_counts.items())),
        "verified_complete_scopes": verified_complete,
        "compacted_complete_scopes": compacted_complete,
        "unverified_complete_scopes": unverified_complete,
        "local_receipt_verified_complete_scopes": verified_complete,
        "receipt_verification_performed": bool(verify_receipts),
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason":
            "LOCAL_RECEIPT_VERIFICATION_IS_NOT_SOURCE_COVERAGE_PROOF",
        "scopes": scopes,
    }


def budget_collection_status():
    """Return CURRENT_STORED_RAW_AND_CHECKPOINTS_ONLY budget collection status."""
    ttl = _status_cache_seconds() if budget_storage.using_postgres() else 0
    now = time.monotonic()
    cached = _STATUS_CACHE.get("value")
    if ttl and cached is not None and now - float(_STATUS_CACHE.get("at") or 0) < ttl:
        return cached

    datasets = [_dataset_counts(dataset) for dataset in BUDGET_DATASETS]
    result = {
        "scope": "CURRENT_STORED_RAW_AND_CHECKPOINTS_ONLY",
        "datasets": datasets,
        "totals": {
            "raw_rows": sum(row["raw_rows"] for row in datasets),
            "raw_revisions": sum(row["raw_revisions"] for row in datasets),
            "checkpoints": sum(row["checkpoint_count"] for row in datasets),
            "verified_complete_scopes": sum(
                row["verified_complete_scopes"] for row in datasets
            ),
            "compacted_complete_scopes": sum(
                int(row.get("compacted_complete_scopes") or 0)
                for row in datasets
            ),
            "unverified_complete_scopes": sum(
                row["unverified_complete_scopes"] for row in datasets
            ),
        },
        "read_only": True,
        "source_traffic": False,
        "local_storage_completeness_scope": "REQUESTED_CHECKPOINT_SCOPES_ONLY",
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason":
            "NOT_EVALUATED_BY_LOCAL_COLLECTION_STATUS",
    }
    if ttl:
        _STATUS_CACHE["at"] = now
        _STATUS_CACHE["value"] = result
    return result


def budget_collection_monitor_status():
    """Return a bounded-cost status snapshot for the 5-second monitor page.

    The monitor needs counts, checkpoint states and recent scope metadata, not a
    fresh receipt proof for every historical COMPLETE scope on every refresh.
    Production therefore batches dataset counts in two grouped queries, skips the
    per-checkpoint receipt verification fan-out, and caches this read-only snapshot
    briefly. Full callers keep using budget_collection_status() unchanged.
    """
    using_postgres = budget_storage.using_postgres()
    ttl = _monitor_status_cache_seconds() if using_postgres else 0
    now = time.monotonic()
    cached = _MONITOR_STATUS_CACHE.get("value")
    if (
        ttl
        and cached is not None
        and now - float(_MONITOR_STATUS_CACHE.get("at") or 0) < ttl
    ):
        return cached

    counts_by_dataset = budget_storage.dataset_counts_all(BUDGET_DATASETS)
    datasets = [
        _dataset_counts(
            dataset,
            storage=counts_by_dataset.get(dataset) or {
                "dataset": dataset,
                "current_records": 0,
                "observations": 0,
                "last_seen_at": "",
            },
            verify_receipts=False,
        )
        for dataset in BUDGET_DATASETS
    ]
    result = {
        "scope": "CURRENT_STORED_RAW_AND_CHECKPOINTS_ONLY",
        "datasets": datasets,
        "totals": {
            "raw_rows": sum(row["raw_rows"] for row in datasets),
            "raw_revisions": sum(row["raw_revisions"] for row in datasets),
            "checkpoints": sum(row["checkpoint_count"] for row in datasets),
            "verified_complete_scopes": 0,
            "compacted_complete_scopes": sum(
                int(row.get("compacted_complete_scopes") or 0)
                for row in datasets
            ),
            "unverified_complete_scopes": sum(
                row["unverified_complete_scopes"] for row in datasets
            ),
        },
        "read_only": True,
        "source_traffic": False,
        "monitor_fast_path": True,
        "receipt_verification_performed": False,
        "local_storage_completeness_scope": "REQUESTED_CHECKPOINT_SCOPES_ONLY",
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason":
            "MONITOR_FAST_PATH_DOES_NOT_REVERIFY_RECEIPTS",
    }
    if ttl:
        _MONITOR_STATUS_CACHE["at"] = now
        _MONITOR_STATUS_CACHE["value"] = result
    return result
