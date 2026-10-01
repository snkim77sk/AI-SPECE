"""Budget storage router.

Production defaults to PostgreSQL for budget RAW. Regression/development may opt into
legacy SQLite explicitly with G2B_BUDGET_STORAGE=sqlite.
"""
from __future__ import annotations

import json
import os

import budget_pg_store
from db import connect
from vnext_schema import ensure_vnext_schema
import vnext_store

BUDGET_DATASETS = budget_pg_store.BUDGET_DATASETS


def backend_name():
    mode = str(os.getenv("G2B_BUDGET_STORAGE", "postgresql") or "postgresql").strip().lower()
    if mode in {"sqlite", "legacy", "legacy_sqlite"}:
        return "SQLITE"
    if mode not in {"postgres", "postgresql"}:
        raise RuntimeError("G2B_BUDGET_STORAGE_INVALID")
    return "POSTGRESQL"


def using_postgres():
    return backend_name() == "POSTGRESQL"


def storage_configured():
    if not using_postgres():
        return True
    return budget_pg_store.postgres_configured()


def storage_ready():
    if not using_postgres():
        return True
    return budget_pg_store.postgres_ready()


def require_storage():
    if using_postgres() and not budget_pg_store.postgres_configured():
        raise RuntimeError("BUDGET_POSTGRES_NOT_CONFIGURED")


def preserve_raw(dataset, source_key, payload, *, source_system="", source_operation="",
                 source_date="", _conn=None):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not using_postgres():
        return vnext_store.preserve_raw(
            dataset, source_key, payload,
            source_system=source_system, source_operation=source_operation,
            source_date=source_date, _conn=_conn,
        )
    result = budget_pg_store.preserve_observation(
        dataset, source_key, payload,
        source_system=source_system, source_operation=source_operation,
        source_date=source_date,
    )
    return result["sha256"]


def current_raw_rows(datasets=None):
    selected = tuple(datasets or BUDGET_DATASETS)
    if not using_postgres():
        ensure_vnext_schema_for_read()
        placeholders = ",".join("?" for _ in selected)
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT dataset,source_system,source_operation,source_key,source_date,
                           fetched_at,payload_json,payload_sha256
                    FROM raw_records WHERE dataset IN ({placeholders}) ORDER BY id""",
                selected,
            ).fetchall()
        return [dict(row) for row in rows]

    require_storage()
    result = []
    for row in budget_pg_store.current_rows(selected):
        result.append({
            "dataset": str(row["dataset"]),
            "source_system": str(row.get("source_system") or ""),
            "source_operation": str(row.get("source_operation") or ""),
            "source_key": str(row["record_key"]),
            "source_date": str(row.get("source_date") or ""),
            "fetched_at": str(row.get("last_seen_at") or row.get("observed_at") or ""),
            "payload_json": json.dumps(
                row.get("payload") or {},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
            ),
            "payload_sha256": str(row.get("payload_sha256") or ""),
        })
    return result


def current_payload_hashes(datasets=None):
    selected = tuple(datasets or BUDGET_DATASETS)
    if using_postgres():
        require_storage()
        return budget_pg_store.current_payload_hashes(selected)
    return {
        (str(row["dataset"]), str(row["source_key"])): str(row["payload_sha256"])
        for row in current_raw_rows(selected)
    }


def revision_rows(dataset, source_key):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not using_postgres():
        ensure_vnext_schema_for_read()
        with connect() as conn:
            current = conn.execute(
                "SELECT payload_sha256 FROM raw_records WHERE dataset=? AND source_key=?",
                (dataset, source_key),
            ).fetchone()
            rows = conn.execute(
                """SELECT id,source_system,source_operation,source_key,source_date,
                          fetched_at,payload_json,payload_sha256
                   FROM raw_record_revisions
                   WHERE dataset=? AND source_key=?
                   ORDER BY source_date,fetched_at,id""",
                (dataset, source_key),
            ).fetchall()
        current_sha = str(current["payload_sha256"] or "") if current else ""
        return [dict(row, current_payload_sha256=current_sha) for row in rows]

    require_storage()
    current = {
        str(row["source_key"]): str(row["payload_sha256"])
        for row in current_raw_rows([dataset])
        if str(row["source_key"]) == str(source_key)
    }
    current_sha = current.get(str(source_key), "")
    result = []
    for index, row in enumerate(budget_pg_store.revision_rows(dataset, source_key), 1):
        result.append({
            "id": str(row["id"]),
            "source_system": str(row.get("source_system") or ""),
            "source_operation": str(row.get("source_operation") or ""),
            "source_key": str(row["record_key"]),
            "source_date": str(row.get("source_date") or ""),
            "fetched_at": str(row.get("observed_at") or ""),
            "payload_json": json.dumps(
                row.get("payload") or {},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
            ),
            "payload_sha256": str(row.get("sha256") or ""),
            "current_payload_sha256": current_sha,
            "ordinal": index,
        })
    return result


def all_revision_rows(dataset, *, source_date_prefix=""):
    """Return immutable revision rows for one budget dataset.

    Used only by timeline/repair analysis. Production history is stored in PostgreSQL.
    """
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not using_postgres():
        ensure_vnext_schema_for_read()
        where = "r.dataset=?"
        params = [dataset]
        if source_date_prefix:
            where += " AND r.source_date LIKE ?"
            params.append(str(source_date_prefix) + "%")
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT r.id,r.source_system,r.source_operation,r.source_key,r.source_date,
                           r.fetched_at,r.payload_json,r.payload_sha256,
                           current.payload_sha256 AS current_payload_sha256
                    FROM raw_record_revisions r
                    LEFT JOIN raw_records current
                      ON current.dataset=r.dataset AND current.source_key=r.source_key
                    WHERE {where} ORDER BY r.source_date,r.fetched_at,r.id""",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    require_storage()
    rows = []
    current = current_payload_hashes([dataset])
    seen_keys = {str(row["source_key"]) for row in current_raw_rows([dataset])}
    for source_key in sorted(seen_keys):
        for row in revision_rows(dataset, source_key):
            if source_date_prefix and not str(row.get("source_date") or "").startswith(str(source_date_prefix)):
                continue
            rows.append(row)
    rows.sort(key=lambda row: (
        str(row.get("source_date") or ""), str(row.get("fetched_at") or ""),
        str(row.get("id") or ""),
    ))
    return rows


def ensure_vnext_schema_for_read():
    with connect() as conn:
        ensure_vnext_schema(conn)


def purge_history(retention_days=365):
    if not using_postgres():
        return {
            "expired_current_records": 0,
            "deleted_observations": 0,
            "retention_days": max(30, int(retention_days)),
            "backend": "SQLITE",
        }
    require_storage()
    result = dict(budget_pg_store.purge_history(retention_days))
    result["backend"] = "POSTGRESQL"
    return result


def status():
    if not using_postgres():
        return {
            "configured": True,
            "backend": "SQLITE",
            "schema": "",
            "observations": 0,
            "current_records": 0,
        }
    return budget_pg_store.storage_status()



def dataset_counts(dataset):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if using_postgres():
        require_storage()
        return budget_pg_store.dataset_counts(dataset)
    ensure_vnext_schema_for_read()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n,MAX(fetched_at) AS last_at FROM raw_records WHERE dataset=?",
            (dataset,),
        ).fetchone()
        current = int(row["n"] or 0)
        last_seen_at = str(row["last_at"] or "")
        revisions = int(conn.execute(
            "SELECT COUNT(*) FROM raw_record_revisions WHERE dataset=?", (dataset,)
        ).fetchone()[0] or 0)
    return {
        "dataset": dataset,
        "current_records": current,
        "observations": revisions,
        "superseded_observations": max(0, revisions - current),
        "last_seen_at": last_seen_at,
    }
