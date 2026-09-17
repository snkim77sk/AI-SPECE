"""Fail-closed guard before DB-wide historical normalization/classification.

The current normalizers and classifier scan the whole vNext RAW database. Therefore
an explicit historical plan may be finalized only when every current RAW row in the
DB belongs to one of that plan's fresh-stable receipt generations. Otherwise partial,
manual, or out-of-plan RAW could be derived as if it were part of the audited plan.
"""
from __future__ import annotations

import json

from db import connect
from vnext_collection import verified_checkpoint
from vnext_stability import stability_fresh_checkpoint
from vnext_store import get_checkpoint


class FinalizeCoverageError(RuntimeError):
    pass


def _generation(cp):
    try:
        meta = json.loads(str(cp.get("cursor_value") or "{}"))
    except (TypeError, ValueError):
        return ""
    return str(meta.get("generation") or "") if isinstance(meta, dict) else ""


def require_plan_raw_coverage(audit):
    """Require current DB RAW to be exactly covered by audited fresh-stable units."""
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

    placeholders = ",".join("?" for _ in planned_datasets)
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

        extra = [str(row["dataset"]) for row in conn.execute(
            f"SELECT DISTINCT dataset FROM raw_records WHERE dataset NOT IN ({placeholders}) ORDER BY dataset",
            tuple(sorted(planned_datasets)),
        ).fetchall()]
        if extra:
            raise FinalizeCoverageError(
                "FINALIZE_RAW_DATASET_OUTSIDE_PLAN:" + ",".join(extra[:10])
            )

        missing = [dict(row) for row in conn.execute(
            f"""SELECT r.dataset,r.source_key,r.payload_sha256
                FROM raw_records r
                WHERE r.dataset IN ({placeholders})
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
            tuple(sorted(planned_datasets)),
        ).fetchall()]
        if missing:
            sample = ",".join(f"{row['dataset']}:{row['source_key']}" for row in missing[:5])
            raise FinalizeCoverageError("FINALIZE_CURRENT_RAW_OUTSIDE_PLAN:" + sample)

        current_count = int(conn.execute(
            f"SELECT COUNT(*) AS n FROM raw_records WHERE dataset IN ({placeholders})",
            tuple(sorted(planned_datasets)),
        ).fetchone()["n"])

    return {
        "planned_datasets": sorted(planned_datasets),
        "trusted_units": len(trusted),
        "current_raw_rows": current_count,
        "all_current_raw_covered_by_plan": True,
    }
