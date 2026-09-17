"""Read-only coverage/readiness audit for the independent G2B vNext foundation.

No source API is called here and no credential value is returned. The report answers:
- are all expected RAW collectors represented?
- are all date-range collectors covered by canary and historical plans?
- are credentials configured enough to execute live probes?
- how many latest RAW rows, immutable revisions, stale classifications, incomplete
  checkpoints, and replay-stability proofs currently exist?
"""
from __future__ import annotations

import json
from collections import Counter

import award_vnext
import bid_vnext
import budget_vnext
import contract_vnext
import g2b_vnext_canary
import historical_vnext
import shopping_vnext
from db import connect, get_service_key
from lofin_vnext_http import get_lofin_key
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

EXPECTED_RAW_DATASETS = frozenset({
    "budget",
    "shopping_delivery",
    "bid_notice_goods",
    "bid_notice_service",
    "opening_result_service",
    "award_result_service",
    "contract_service",
})
DATE_RANGE_G2B_DATASETS = frozenset(EXPECTED_RAW_DATASETS - {"budget"})


def collector_datasets():
    datasets = {budget_vnext.DATASET, shopping_vnext.DATASET, contract_vnext.DATASET}
    datasets.update(spec["dataset"] for spec in bid_vnext.BUSINESS_TYPES.values())
    datasets.update(spec[0] for spec in award_vnext.STAGES.values())
    return frozenset(datasets)


def static_coverage():
    collectors = collector_datasets()
    historical = frozenset(name for name, _runner in historical_vnext.STAGES)
    canary = frozenset(g2b_vnext_canary.CANARY_DATASETS.values())
    return {
        "expected_raw_datasets": sorted(EXPECTED_RAW_DATASETS),
        "collector_datasets": sorted(collectors),
        "missing_collectors": sorted(EXPECTED_RAW_DATASETS - collectors),
        "unexpected_collectors": sorted(collectors - EXPECTED_RAW_DATASETS),
        "historical_datasets": sorted(historical),
        "missing_historical": sorted(DATE_RANGE_G2B_DATASETS - historical),
        "unexpected_historical": sorted(historical - DATE_RANGE_G2B_DATASETS),
        "canary_datasets": sorted(canary),
        "missing_canary": sorted(DATE_RANGE_G2B_DATASETS - canary),
        "unexpected_canary": sorted(canary - DATE_RANGE_G2B_DATASETS),
    }


def credential_readiness():
    """Return booleans only; credential values are never surfaced."""
    return {
        "g2b_service_key_configured": bool(str(get_service_key("") or "").strip()),
        "lofin_api_key_configured": bool(str(get_lofin_key() or "").strip()),
    }


def _checkpoint_counts(conn, dataset):
    rows = conn.execute(
        "SELECT status,COUNT(*) AS n FROM collection_checkpoints WHERE dataset=? GROUP BY status",
        (dataset,),
    ).fetchall()
    counts = Counter({str(row["status"] or "OTHER"): int(row["n"] or 0) for row in rows})
    return dict(sorted(counts.items()))


def _stability_summary(conn, dataset):
    """Summarize replay proofs without exposing source rows or query values."""
    rows = conn.execute(
        "SELECT status,cursor_value FROM collection_checkpoints WHERE dataset=?",
        (dataset,),
    ).fetchall()
    verified = 0
    without_timestamp = 0
    timestamps = []
    recollect_required = 0
    for row in rows:
        try:
            meta = json.loads(str(row["cursor_value"] or "{}"))
            if not isinstance(meta, dict):
                meta = {}
        except (TypeError, ValueError):
            meta = {}
        if meta.get("stability_recollect_required"):
            recollect_required += 1
        stable = meta.get("stability") if isinstance(meta.get("stability"), dict) else {}
        if (
            str(row["status"] or "") == "COMPLETE"
            and stable.get("status") == "VERIFIED"
            and stable.get("generation") == meta.get("generation")
            and not meta.get("stability_recollect_required")
        ):
            verified += 1
            stamp = str(stable.get("verified_at_utc") or "")
            if stamp:
                timestamps.append(stamp)
            else:
                without_timestamp += 1
    return {
        "stability_verified_checkpoints": verified,
        "stability_verified_without_timestamp": without_timestamp,
        "stability_recollect_required": recollect_required,
        "oldest_stability_verified_at_utc": min(timestamps) if timestamps else "",
        "newest_stability_verified_at_utc": max(timestamps) if timestamps else "",
    }


def storage_readiness():
    with connect() as conn:
        ensure_vnext_schema(conn)
        datasets = {}
        for dataset in sorted(EXPECTED_RAW_DATASETS):
            latest = conn.execute(
                "SELECT COUNT(*) AS n FROM raw_records WHERE dataset=?", (dataset,)
            ).fetchone()["n"]
            revisions = conn.execute(
                "SELECT COUNT(*) AS n FROM raw_record_revisions WHERE dataset=?", (dataset,)
            ).fetchone()["n"]
            current = conn.execute(
                """SELECT COUNT(*) AS n
                   FROM raw_records r JOIN classifications c
                     ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                    AND c.classifier_version=?
                    AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')
                   WHERE r.dataset=?""",
                (CLASSIFIER_VERSION, dataset),
            ).fetchone()["n"]
            datasets[dataset] = {
                "latest_raw_rows": int(latest or 0),
                "revision_rows": int(revisions or 0),
                "current_classified_rows": int(current or 0),
                "unclassified_or_stale_rows": max(0, int(latest or 0) - int(current or 0)),
                "checkpoint_status_counts": _checkpoint_counts(conn, dataset),
                **_stability_summary(conn, dataset),
            }
        return datasets


def build_readiness_report():
    coverage = static_coverage()
    credentials = credential_readiness()
    storage = storage_readiness()
    static_ok = not any((
        coverage["missing_collectors"], coverage["unexpected_collectors"],
        coverage["missing_historical"], coverage["unexpected_historical"],
        coverage["missing_canary"], coverage["unexpected_canary"],
    ))
    if not static_ok:
        status = "STATIC_COVERAGE_ERROR"
    elif not credentials["g2b_service_key_configured"]:
        status = "G2B_CANARY_BLOCKED"
    else:
        status = "G2B_CANARY_READY"
    return {
        "status": status,
        "classifier_version": CLASSIFIER_VERSION,
        "static_coverage_ok": static_ok,
        "credentials": credentials,
        "coverage": coverage,
        "storage": storage,
        "historical_live_collection_locked_by_default": True,
        "budget_canary_status": "READY_TO_PROBE" if credentials["lofin_api_key_configured"] else "BLOCKED",
        "budget_canary_module": "budget_snapshot_vnext.run_budget_canary",
        "budget_snapshot_audit_module": "budget_snapshot_vnext.audit_snapshots",
        "budget_scope": "explicit QWGJK fiscal-year/snapshot dates, not every budget API",
        "main_merge_hold": True,
        "notes": {
            "budget_source": "LOFIN/QWGJK snapshot collection uses LOFIN_API_KEY independently",
            "g2b_canary": "six G2B date-range datasets require a successful sanitized canary before historical live unlock",
            "stability_timestamp": "verified_at_utc is audit metadata only; no fixed hard-expiry is imposed on long historical runs",
        },
    }
