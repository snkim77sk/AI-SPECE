"""Read-only G2B 4.x execution/readiness audit.

Active data domains are intentionally limited to:
- shopping_delivery: nationwide source scan from 2026-10-01, lighting/pole storage only
- budget / budget_appropriation / education_budget: budget domain

Bid/service/award/contract lifecycles belong to NO1 and are not G2B readiness inputs.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter

import budget_appropriation_vnext
import budget_storage
import budget_vnext
import education_budget_vnext
import shopping_vnext
import shopping_store_v41
import vnext_stability
from db import connect, source_credential_configured
from vnext_schema import CLASSIFIER_VERSION

BUDGET_RAW_DATASETS = frozenset({
    budget_vnext.DATASET,
    budget_appropriation_vnext.DATASET,
    education_budget_vnext.DATASET,
})
DATE_RANGE_G2B_DATASETS = frozenset({shopping_vnext.DATASET})
EXPECTED_RAW_DATASETS = BUDGET_RAW_DATASETS | DATE_RANGE_G2B_DATASETS


def collector_datasets():
    return frozenset(EXPECTED_RAW_DATASETS)


def static_coverage():
    collectors = collector_datasets()
    return {
        "expected_raw_datasets": sorted(EXPECTED_RAW_DATASETS),
        "budget_raw_datasets": sorted(BUDGET_RAW_DATASETS),
        "collector_datasets": sorted(collectors),
        "missing_collectors": sorted(EXPECTED_RAW_DATASETS - collectors),
        "unexpected_collectors": sorted(collectors - EXPECTED_RAW_DATASETS),
        # G2B 4.x has no broad historical or service canary plan.
        "historical_datasets": [],
        "missing_historical": [],
        "unexpected_historical": [],
        "canary_datasets": [],
        "missing_canary": [],
        "unexpected_canary": [],
        "service_collection_removed": True,
        "goods_bid_collection_removed": True,
    }


def credential_readiness():
    return {
        "g2b_service_key_configured": source_credential_configured("g2b_service_key"),
        "lofin_api_key_configured": source_credential_configured("lofin_api_key"),
        "eduinfo_api_key_configured": source_credential_configured("eduinfo_api_key"),
    }


def _checkpoint_counts(conn, dataset):
    rows = conn.execute(
        "SELECT status,COUNT(*) AS n FROM collection_checkpoints WHERE dataset=? GROUP BY status",
        (dataset,),
    ).fetchall()
    return dict(sorted(
        Counter({str(row["status"] or "OTHER"): int(row["n"] or 0) for row in rows}).items()
    ))


def _stability_summary(conn, dataset):
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM collection_checkpoints WHERE dataset=?", (dataset,)
    ).fetchall()]
    structural_verified = 0
    fresh_verified = 0
    stale_verified = 0
    without_timestamp = 0
    invalid_claims = 0
    timestamps = []
    recollect_required = 0
    now = dt.datetime.now(dt.timezone.utc)
    for row in rows:
        try:
            meta = json.loads(str(row.get("cursor_value") or "{}"))
            if not isinstance(meta, dict):
                meta = {}
        except (TypeError, ValueError):
            meta = {}
        if meta.get("stability_recollect_required"):
            recollect_required += 1
        stable_meta = meta.get("stability") if isinstance(meta.get("stability"), dict) else {}
        metadata_claim = (
            str(row.get("status") or "") == "COMPLETE"
            and stable_meta.get("status") == "VERIFIED"
            and stable_meta.get("generation") == meta.get("generation")
            and not meta.get("stability_recollect_required")
        )
        verified = vnext_stability.stability_verified_checkpoint(row)
        if metadata_claim and not verified:
            invalid_claims += 1
        if not verified:
            continue
        structural_verified += 1
        stamp = str(stable_meta.get("verified_at_utc") or "")
        if not stamp:
            without_timestamp += 1
            continue
        timestamps.append(stamp)
        if vnext_stability.stability_fresh_checkpoint(row, now=now):
            fresh_verified += 1
        else:
            stale_verified += 1
    return {
        "stability_verified_checkpoints": structural_verified,
        "stability_structural_verified_checkpoints": structural_verified,
        "stability_fresh_verified_checkpoints": fresh_verified,
        "stability_stale_verified_checkpoints": stale_verified,
        "stability_verified_without_timestamp": without_timestamp,
        "stability_invalid_metadata_claims": invalid_claims,
        "stability_recollect_required": recollect_required,
        "oldest_stability_verified_at_utc": min(timestamps) if timestamps else "",
        "newest_stability_verified_at_utc": max(timestamps) if timestamps else "",
    }


def _shopping_storage_readiness():
    dataset = shopping_vnext.DATASET
    if str(os.getenv("G2B_TEST_MODE", "0") or "").lower() in {"1", "true", "yes", "on"}:
        with connect() as conn:
            latest = int(conn.execute(
                "SELECT COUNT(*) AS n FROM raw_records WHERE dataset=?", (dataset,)
            ).fetchone()["n"] or 0)
            revisions = int(conn.execute(
                "SELECT COUNT(*) AS n FROM raw_record_revisions WHERE dataset=?", (dataset,)
            ).fetchone()["n"] or 0)
            current = int(conn.execute(
                """SELECT COUNT(*) AS n
                   FROM raw_records r JOIN classifications c
                     ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                    AND c.classifier_version=?
                    AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')
                   WHERE r.dataset=?""",
                (CLASSIFIER_VERSION, dataset),
            ).fetchone()["n"] or 0)
            checkpoints = _checkpoint_counts(conn, dataset)
            stability = _stability_summary(conn, dataset)
        return {
            "readiness_scope": "TEST_RAW_FIXTURE_SHOPPING_STORAGE",
            "storage_mode": "TEST_ONLY_RAW_FIXTURE",
            "normalized_rows": 0,
            "raw_backend": "SQLITE_TEST",
            "latest_raw_rows": latest,
            "revision_rows": revisions,
            "current_classified_rows": current,
            "unclassified_or_stale_rows": max(0, latest - current),
            "checkpoint_status_counts": checkpoints,
            "source_collection_completeness_verified": False,
            "source_collection_completeness_reason":
                "TARGET_STORAGE_AND_RECEIPTS_DO_NOT_PROVE_WHOLE_SOURCE_COVERAGE",
            **stability,
        }

    shopping_store_v41.ensure_schema()
    with connect() as conn:
        latest = int(conn.execute(
            "SELECT COUNT(*) AS n FROM shopping_records"
        ).fetchone()["n"] or 0)
        current = int(conn.execute(
            """SELECT COUNT(*) AS n FROM shopping_records
               WHERE primary_category IN ('LIGHTING','POLE')"""
        ).fetchone()["n"] or 0)
        checkpoints = _checkpoint_counts(conn, dataset)
        stability = _stability_summary(conn, dataset)
    return {
        "readiness_scope": "CURRENT_NORMALIZED_SHOPPING_STORAGE_ONLY",
        "storage_mode": "NORMALIZED_FIELDS_NO_SOURCE_JSON",
        "normalized_rows": latest,
        "raw_backend": "NORMALIZED_POSTGRESQL",
        "latest_raw_rows": latest,
        "revision_rows": 0,
        "current_classified_rows": current,
        "unclassified_or_stale_rows": max(0, latest - current),
        "checkpoint_status_counts": checkpoints,
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason":
            "TARGET_STORAGE_AND_RECEIPTS_DO_NOT_PROVE_WHOLE_SOURCE_COVERAGE",
        **stability,
    }

def _budget_storage_readiness(
    dataset, *, counts=None, current_hashes=None, checkpoint_counts=None
):
    empty_stability = {
        "stability_verified_checkpoints": 0,
        "stability_structural_verified_checkpoints": 0,
        "stability_fresh_verified_checkpoints": 0,
        "stability_stale_verified_checkpoints": 0,
        "stability_verified_without_timestamp": 0,
        "stability_invalid_metadata_claims": 0,
        "stability_recollect_required": 0,
        "oldest_stability_verified_at_utc": "",
        "newest_stability_verified_at_utc": "",
    }
    try:
        counts = counts or budget_storage.dataset_counts(dataset)
        current_hashes = (
            current_hashes
            if current_hashes is not None
            else budget_storage.current_payload_hashes([dataset])
        )
        hashes = {
            source_key: payload_sha256
            for (row_dataset, source_key), payload_sha256 in current_hashes.items()
            if row_dataset == dataset
        }
        classified = 0
        with connect() as conn:
            cursor = conn.execute(
                """SELECT entity_key,source_payload_sha256
                   FROM classifications
                   WHERE entity_type=? AND classifier_version=?""",
                (dataset, CLASSIFIER_VERSION),
            )
            while True:
                rows = cursor.fetchmany(2000)
                if not rows:
                    break
                classified += sum(
                    1 for row in rows
                    if hashes.get(str(row["entity_key"]))
                    == str(row["source_payload_sha256"] or "")
                )
        if checkpoint_counts is None:
            try:
                import budget_collection_status_vnext
                status = next(
                    row for row in budget_collection_status_vnext.budget_collection_status()["datasets"]
                    if str(row["dataset"]) == dataset
                )
                checkpoint_counts = dict(
                    status.get("checkpoint_status_counts") or {}
                )
            except Exception:
                checkpoint_counts = {}
        else:
            checkpoint_counts = dict(checkpoint_counts)
        latest = int(counts.get("current_records") or 0)
        revisions = int(counts.get("observations") or 0)
        return {
            "readiness_scope": "CURRENT_BUDGET_STORAGE_ONLY",
            "raw_backend": budget_storage.backend_name(),
            "latest_raw_rows": latest,
            "revision_rows": revisions,
            "current_classified_rows": classified,
            "unclassified_or_stale_rows": max(0, latest - classified),
            "checkpoint_status_counts": checkpoint_counts,
            "source_collection_completeness_verified": False,
            "source_collection_completeness_reason":
                "STORED_BUDGET_STATE_DOES_NOT_PROVE_WHOLE_SOURCE_COVERAGE",
            **empty_stability,
        }
    except Exception as exc:
        return {
            "readiness_scope": "CURRENT_BUDGET_STORAGE_ONLY",
            "raw_backend": budget_storage.backend_name(),
            "storage_error": type(exc).__name__,
            "latest_raw_rows": 0,
            "revision_rows": 0,
            "current_classified_rows": 0,
            "unclassified_or_stale_rows": 0,
            "checkpoint_status_counts": {},
            "source_collection_completeness_verified": False,
            "source_collection_completeness_reason": "BUDGET_STORAGE_UNAVAILABLE",
            **empty_stability,
        }


def storage_readiness():
    result = {shopping_vnext.DATASET: _shopping_storage_readiness()}
    datasets = tuple(sorted(BUDGET_RAW_DATASETS))
    bulk_counts = {}
    bulk_hashes = None
    checkpoint_by_dataset = {}

    if budget_storage.using_postgres():
        try:
            bulk_counts = budget_storage.dataset_counts_all(datasets)
            bulk_hashes = budget_storage.current_payload_hashes(datasets)
            import budget_collection_status_vnext
            collection = budget_collection_status_vnext.budget_collection_status()
            checkpoint_by_dataset = {
                str(row["dataset"]): dict(
                    row.get("checkpoint_status_counts") or {}
                )
                for row in collection.get("datasets") or []
            }
        except Exception:
            bulk_counts = {}
            bulk_hashes = None
            checkpoint_by_dataset = {}

    for dataset in datasets:
        result[dataset] = _budget_storage_readiness(
            dataset,
            counts=bulk_counts.get(dataset),
            current_hashes=bulk_hashes,
            checkpoint_counts=(
                checkpoint_by_dataset.get(dataset)
                if dataset in checkpoint_by_dataset
                else None
            ),
        )
    return result


def build_readiness_report():
    coverage = static_coverage()
    credentials = credential_readiness()
    storage = storage_readiness()
    static_ok = not any((
        coverage["missing_collectors"], coverage["unexpected_collectors"],
        coverage["missing_historical"], coverage["unexpected_historical"],
        coverage["missing_canary"], coverage["unexpected_canary"],
    ))

    budget_backend_ready = budget_storage.storage_ready()
    shopping_storage_ready = bool(
        storage.get(shopping_vnext.DATASET)
        and not storage.get(shopping_vnext.DATASET, {}).get("storage_error")
    )
    shopping_operational_ready = bool(
        static_ok
        and shopping_storage_ready
        and credentials["g2b_service_key_configured"]
    )
    budget_operational_ready = bool(
        static_ok
        and budget_backend_ready
        and credentials["lofin_api_key_configured"]
    )
    if not static_ok:
        status = "STATIC_COVERAGE_ERROR"
    elif not budget_backend_ready:
        status = "BUDGET_POSTGRES_WAITING"
    elif not credentials["lofin_api_key_configured"]:
        status = "BUDGET_KEY_WAITING"
    elif not credentials["g2b_service_key_configured"]:
        status = "SHOPPING_KEY_WAITING"
    else:
        status = "OPERATIONAL_READY"

    return {
        "status": status,
        "budget_storage_error_code": budget_storage.storage_error_code(),
        "status_scope": "EXECUTION_READINESS_NOT_SOURCE_COMPLETENESS",
        "classifier_version": CLASSIFIER_VERSION,
        "static_coverage_ok": static_ok,
        "shopping_storage_ready": shopping_storage_ready,
        "shopping_operational_ready": shopping_operational_ready,
        "budget_operational_ready": budget_operational_ready,
        "credentials": credentials,
        "coverage": coverage,
        "storage": storage,
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason":
            "READINESS_DOES_NOT_PROVE_FULL_SOURCE_COVERAGE",
        "budget_source_collection_completeness_verified": False,
        "deployment_state": "V4_BUDGET_CENTERED",
        "main_merge_hold": False,
        "live_collection_mode": "NORMALIZED_BUDGET_PLUS_NORMALIZED_TARGET_SHOPPING",
        "production_scheduler_enabled": (
            str(os.getenv("G2B_AUTO_SYNC", "1") or "1").lower().strip()
            not in ("0", "false", "no", "off")
            and str(os.getenv("G2B_TEST_MODE", "0") or "0").lower().strip()
            not in ("1", "true", "yes", "on")
        ),
        "shopping_recent_collection": {
            "enabled_capability": True,
            "order": "FORWARD",
            "start_date": "2026-09-01",
            "latest_boundary": "D-1",
            "one_day_scopes": True,
            "stored_scope": "LIGHTING_AND_POLE_ONLY",
            "bulk_historical_unlocked": False,
        },
        "future_budget_collection": {
            "enabled_capability": True,
            "source": "AIDFA",
            "fiscal_year_scope": "NEXT_FISCAL_YEAR",
            "priority": "BEFORE_CURRENT_YEAR_AIDFA_AND_QWGJK",
            "refresh": "RECHECK_COMPLETE_SCOPE_ON_NEW_DATE",
            "direct_sales_target": False,
        },
        "budget_storage": {
            "backend": budget_storage.backend_name(),
            "ready": bool(budget_backend_ready),
            "retention_days": 365,
            "full_raw": False,
            "normalized_only": True,
            "dedupe": "SEMANTIC_STATE_HASH",
        },
        "bulk_historical_hold": True,
        "approved_historical_context_available": False,
        "historical_live_collection_locked_by_default": True,
        "stability_max_age_hours": vnext_stability.stability_max_age_hours(),
        "education_budget_key_status": (
            "KEY_CONFIGURED_LIVE_HOLD"
            if credentials["eduinfo_api_key_configured"]
            else "BLOCKED_KEY_MISSING"
        ),
        "education_budget_live_transport_hold": True,
        "service_collection_removed": True,
        "goods_bid_collection_removed": True,
        "notes": {
            "budget_source": (
                "QWGJK/AIDFA/education are the only budget domains; production stores canonical "
                "budget fields and source hashes without source JSON."
            ),
            "budget_source_limits": {
                "budget": "QWGJK operational collection normalizes the explicit current snapshot",
                "budget_appropriation": "AIDFA collection remains explicit and does not claim unseen partitions",
                "education_budget": "education live transport remains HOLD pending validation",
            },
            "shopping_operational_recent": (
                "the source is scanned from 2026-09-01 forward; only normalized exact lighting/pole "
                "detail-item records are retained"
            ),
            "no1_boundary": "goods bidding and service/award/contract lifecycles are removed from G2B and remain NO1 responsibilities",
        },
    }
