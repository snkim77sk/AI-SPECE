"""Fail-closed guards before vNext normalization/classification consumers.

A consumer that scans current RAW may derive rows outside the source scope it just
validated. These guards re-read receipt/stability state and require every current RAW
row actually consumed by that path to belong to a trusted fresh-stable generation.
Historical DB-wide finalize additionally rejects every dataset outside its plan.
"""
from __future__ import annotations

import json

from db import connect
from vnext_collection import verified_checkpoint
from vnext_stability import stability_fresh_checkpoint
from vnext_store import get_checkpoint


class FinalizeCoverageError(RuntimeError):
    pass


class TrustedRawRevisionToken:
    """Frozen current-RAW revision set captured from a validated plan boundary.

    The token intentionally stores exact payload digests, not merely datasets or row
    counts. A RAW revision inserted or replaced after capture is therefore rejected
    by downstream normalization/classification even if the row count is unchanged.
    """

    __slots__ = ("_revisions", "consumer_datasets", "current_raw_rows")

    def __init__(self, revisions, consumer_datasets):
        normalized = {
            (str(dataset), str(source_key), str(payload_sha256))
            for dataset, source_key, payload_sha256 in revisions
        }
        self._revisions = frozenset(normalized)
        self.consumer_datasets = frozenset(str(value) for value in consumer_datasets)
        self.current_raw_rows = len(self._revisions)

    def require_revision(self, dataset, source_key, payload_sha256):
        dataset = str(dataset or "")
        source_key = str(source_key or "")
        digest = str(payload_sha256 or "")
        if dataset not in self.consumer_datasets:
            raise FinalizeCoverageError(
                "FINALIZE_RAW_DATASET_NOT_CAPTURED:" + dataset
            )
        if (dataset, source_key, digest) not in self._revisions:
            raise FinalizeCoverageError(
                f"FINALIZE_RAW_REVISION_NOT_CAPTURED:{dataset}:{source_key}"
            )
        with connect() as conn:
            current = conn.execute(
                "SELECT payload_sha256 FROM raw_records WHERE dataset=? AND source_key=?",
                (dataset, source_key),
            ).fetchone()
        if not current or str(current["payload_sha256"] or "") != digest:
            raise FinalizeCoverageError(
                f"FINALIZE_CURRENT_RAW_CHANGED_AFTER_COVERAGE:{dataset}:{source_key}"
            )
        return True


def _generation(cp):
    try:
        meta = json.loads(str(cp.get("cursor_value") or "{}"))
    except (TypeError, ValueError):
        return ""
    return str(meta.get("generation") or "") if isinstance(meta, dict) else ""


def require_plan_raw_coverage(
    audit,
    *,
    consumer_datasets=None,
    reject_unplanned_datasets=True,
):
    """Require current consumer RAW to be covered by audited fresh-stable units.

    ``consumer_datasets=None`` means the consumer may use every dataset in the plan.
    Historical DB-wide finalize keeps ``reject_unplanned_datasets=True`` so any RAW
    dataset outside the plan blocks derivation. A narrower consumer may set it false,
    but its declared consumer datasets must still be a subset of audited datasets and
    every current RAW row in those datasets must have an exact trusted receipt item.
    """
    if not isinstance(audit, dict) or audit.get("all_complete") is not True:
        raise FinalizeCoverageError("FINALIZE_AUDIT_NOT_COMPLETE")
    records = audit.get("records") if isinstance(audit.get("records"), list) else []
    if not records:
        raise FinalizeCoverageError("FINALIZE_AUDIT_RECORDS_REQUIRED")

    trusted = []
    planned_datasets = set()
    for record in records:
        if not isinstance(record, dict):
            raise FinalizeCoverageError("FINALIZE_AUDIT_RECORD_INVALID")
        dataset = str(record.get("dataset") or "")
        scope = str(record.get("scope") or "")
        if not dataset or not scope or record.get("complete") is not True:
            raise FinalizeCoverageError("FINALIZE_AUDIT_UNIT_INCOMPLETE")
        planned_datasets.add(dataset)
        cp = get_checkpoint(dataset, scope)
        if not cp or not verified_checkpoint(cp) or not stability_fresh_checkpoint(cp):
            raise FinalizeCoverageError("FINALIZE_AUDIT_UNIT_NOT_FRESH_STABLE")
        generation = _generation(cp)
        if not generation:
            raise FinalizeCoverageError("FINALIZE_AUDIT_GENERATION_MISSING")
        trusted.append((dataset, scope, generation))

    if consumer_datasets is None:
        consumers = set(planned_datasets)
    else:
        consumers = {str(value) for value in consumer_datasets if str(value)}
    if not consumers:
        raise FinalizeCoverageError("FINALIZE_CONSUMER_DATASETS_REQUIRED")
    if not consumers.issubset(planned_datasets):
        extra_consumers = sorted(consumers - planned_datasets)
        raise FinalizeCoverageError(
            "FINALIZE_CONSUMER_DATASET_NOT_AUDITED:" + ",".join(extra_consumers[:10])
        )

    consumer_placeholders = ",".join("?" for _ in consumers)
    plan_placeholders = ",".join("?" for _ in planned_datasets)
    with connect() as conn:
        conn.execute(
            """CREATE TEMP TABLE IF NOT EXISTS vnext_finalize_trusted_generations(
                   dataset TEXT NOT NULL, scope_key TEXT NOT NULL, generation TEXT NOT NULL,
                   PRIMARY KEY(dataset,scope_key,generation))"""
        )
        conn.execute("DELETE FROM vnext_finalize_trusted_generations")
        conn.executemany(
            "INSERT INTO vnext_finalize_trusted_generations(dataset,scope_key,generation) VALUES(?,?,?)",
            trusted,
        )

        if reject_unplanned_datasets:
            extra = [str(row["dataset"]) for row in conn.execute(
                f"SELECT DISTINCT dataset FROM raw_records WHERE dataset NOT IN ({plan_placeholders}) ORDER BY dataset",
                tuple(sorted(planned_datasets)),
            ).fetchall()]
            if extra:
                raise FinalizeCoverageError(
                    "FINALIZE_RAW_DATASET_OUTSIDE_PLAN:" + ",".join(extra[:10])
                )

        missing = [dict(row) for row in conn.execute(
            f"""SELECT r.dataset,r.source_key,r.payload_sha256
                FROM raw_records r
                WHERE r.dataset IN ({consumer_placeholders})
                  AND NOT EXISTS (
                    SELECT 1
                    FROM vnext_collection_items i
                    JOIN vnext_finalize_trusted_generations g
                      ON g.dataset=i.dataset AND g.scope_key=i.scope_key
                     AND g.generation=i.generation
                    WHERE i.dataset=r.dataset AND i.source_key=r.source_key
                      AND i.payload_sha256=r.payload_sha256
                  )
                ORDER BY r.dataset,r.source_key
                LIMIT 20""",
            tuple(sorted(consumers)),
        ).fetchall()]
        if missing:
            sample = ",".join(f"{row['dataset']}:{row['source_key']}" for row in missing[:5])
            raise FinalizeCoverageError("FINALIZE_CURRENT_RAW_OUTSIDE_PLAN:" + sample)

        current_count = int(conn.execute(
            f"SELECT COUNT(*) AS n FROM raw_records WHERE dataset IN ({consumer_placeholders})",
            tuple(sorted(consumers)),
        ).fetchone()["n"])

    return {
        "planned_datasets": sorted(planned_datasets),
        "consumer_datasets": sorted(consumers),
        "reject_unplanned_datasets": bool(reject_unplanned_datasets),
        "trusted_units": len(trusted),
        "current_raw_rows": current_count,
        "all_current_raw_covered_by_plan": True,
    }


def capture_plan_raw_coverage(
    audit,
    *,
    consumer_datasets=None,
    reject_unplanned_datasets=True,
):
    """Validate coverage and freeze the exact RAW revisions consumers may derive.

    Coverage is validated both before and after the snapshot. If RAW changes between
    those checks, the second validation rejects untrusted drift; if it changes later,
    ``TrustedRawRevisionToken.require_revision`` rejects the new revision per row.
    """
    require_plan_raw_coverage(
        audit,
        consumer_datasets=consumer_datasets,
        reject_unplanned_datasets=reject_unplanned_datasets,
    )
    if consumer_datasets is None:
        records = audit.get("records") if isinstance(audit, dict) else []
        consumers = sorted({str(row.get("dataset") or "") for row in records if isinstance(row, dict) and row.get("dataset")})
    else:
        consumers = sorted({str(value) for value in consumer_datasets if str(value)})
    if not consumers:
        raise FinalizeCoverageError("FINALIZE_CONSUMER_DATASETS_REQUIRED")
    placeholders = ",".join("?" for _ in consumers)
    with connect() as conn:
        revisions = [
            (str(row["dataset"]), str(row["source_key"]), str(row["payload_sha256"] or ""))
            for row in conn.execute(
                f"SELECT dataset,source_key,payload_sha256 FROM raw_records WHERE dataset IN ({placeholders}) ORDER BY dataset,source_key",
                tuple(consumers),
            ).fetchall()
        ]
    summary = require_plan_raw_coverage(
        audit,
        consumer_datasets=consumers,
        reject_unplanned_datasets=reject_unplanned_datasets,
    )
    token = TrustedRawRevisionToken(revisions, consumers)
    if token.current_raw_rows != int(summary.get("current_raw_rows") or 0):
        raise FinalizeCoverageError("FINALIZE_RAW_CHANGED_DURING_COVERAGE_CAPTURE")
    return summary, token
