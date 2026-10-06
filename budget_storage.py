"""Budget storage router for G2B 4.1.

Production is PostgreSQL-only and shares the canonical G2B database.  SQLite is
available only in G2B_TEST_MODE for regression fixtures.
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
        test_mode = str(os.getenv("G2B_TEST_MODE", "0") or "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if not test_mode:
            raise RuntimeError("G2B_BUDGET_STORAGE_SQLITE_TEST_ONLY")
        return "SQLITE"
    if mode not in {"postgres", "postgresql"}:
        raise RuntimeError("G2B_BUDGET_STORAGE_INVALID")
    return "POSTGRESQL"


def using_postgres():
    return backend_name() == "POSTGRESQL"


def storage_configured():
    if not using_postgres():
        return True
    return budget_pg_store.postgres_url_present()


def storage_error_code():
    if not using_postgres():
        return ""
    return budget_pg_store.postgres_last_error_code()


def storage_ready():
    if not using_postgres():
        return True
    return budget_pg_store.postgres_ready()


def operational_cycle_lease(name="g2b_v41_operational_cycle", *, shared=False):
    return budget_pg_store.operational_cycle_lease(
        name=name,
        shared=bool(shared),
    )


def require_storage():
    if not using_postgres():
        return
    if not budget_pg_store.postgres_url_present():
        raise RuntimeError("BUDGET_POSTGRES_NOT_CONFIGURED")
    budget_pg_store.resolve_database_url()


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


def _payload_dict(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_pg_current_row(row):
    return {
        "dataset": str(row["dataset"]),
        "source_system": str(row.get("source_system") or ""),
        "source_operation": str(row.get("source_operation") or ""),
        "source_key": str(row["record_key"]),
        "source_date": str(row.get("source_date") or ""),
        "fetched_at": str(row.get("last_seen_at") or row.get("observed_at") or ""),
        "payload": _payload_dict(row.get("payload")),
        "payload_sha256": str(row.get("payload_sha256") or ""),
    }


def current_raw_batches(datasets=None, *, batch_size=1000):
    """Yield normalized current budget RAW without full-set materialization.

    Batch consumers receive payload as a dict. The legacy current_raw_rows adapter
    below still exposes payload_json for existing callers.
    """
    selected = tuple(datasets or BUDGET_DATASETS)
    size = max(1, min(int(batch_size), 5000))
    if not using_postgres():
        ensure_vnext_schema_for_read()
        placeholders = ",".join("?" for _ in selected)
        last_id = 0
        while True:
            # Close the SQLite read connection before yielding so projection writes
            # from the consumer cannot be blocked by a long-lived read cursor.
            with connect() as conn:
                rows = conn.execute(
                    f"""SELECT id,dataset,source_system,source_operation,source_key,source_date,
                               fetched_at,payload_json,payload_sha256
                        FROM raw_records
                        WHERE dataset IN ({placeholders}) AND id>?
                        ORDER BY id LIMIT ?""",
                    (*selected, last_id, size),
                ).fetchall()
            if not rows:
                break
            last_id = max(int(row["id"]) for row in rows)
            batch = []
            for row in rows:
                item = dict(row)
                item.pop("id", None)
                item["payload"] = _payload_dict(item.get("payload_json"))
                batch.append(item)
            yield batch
        return

    require_storage()
    for rows in budget_pg_store.current_row_batches(selected, batch_size=size):
        yield [_normalize_pg_current_row(row) for row in rows]


def current_raw_for_keys(dataset, source_keys, *, batch_size=1000):
    """Yield payload rows only for explicit current keys."""
    name = str(dataset)
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    keys = list(dict.fromkeys(str(key) for key in source_keys if str(key)))
    size = max(1, min(int(batch_size), 2000))
    if not keys:
        return

    if using_postgres():
        require_storage()
        for rows in budget_pg_store.current_rows_for_keys(
            name, keys, batch_size=size
        ):
            yield [_normalize_pg_current_row(row) for row in rows]
        return

    ensure_vnext_schema_for_read()
    for start in range(0, len(keys), min(size, 400)):
        chunk = keys[start:start + min(size, 400)]
        placeholders = ",".join("?" for _ in chunk)
        with connect() as conn:
            rows = conn.execute(
                f"""SELECT dataset,source_system,source_operation,source_key,source_date,
                           fetched_at,payload_json,payload_sha256
                    FROM raw_records
                    WHERE dataset=? AND source_key IN ({placeholders})
                    ORDER BY id""",
                (name, *chunk),
            ).fetchall()
        batch = []
        for row in rows:
            item = dict(row)
            item["payload"] = _payload_dict(item.get("payload_json"))
            batch.append(item)
        if batch:
            yield batch


def current_raw_rows(datasets=None):
    result = []
    for batch in current_raw_batches(datasets, batch_size=1000):
        for row in batch:
            item = dict(row)
            if "payload_json" not in item:
                item["payload_json"] = json.dumps(
                    item.get("payload") or {},
                    ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), default=str,
                )
            item.pop("payload", None)
            result.append(item)
    return result


def current_normalized_rows(
    datasets=None,
    *,
    fiscal_year=None,
    source_layers=None,
    region_terms=None,
    categories=None,
    classifier_version="",
    organization_exact_names=None,
    organization_contains_terms=None,
    query="",
    execution_status="",
    budget_change_status="",
    limit=None,
    offset=0,
):
    """Read canonical normalized current budget facts without source I/O."""
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - set(BUDGET_DATASETS)
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if using_postgres():
        require_storage()
        return budget_pg_store.current_project_rows(
            selected,
            fiscal_year=fiscal_year,
            source_layers=source_layers,
            region_terms=region_terms,
            categories=categories,
            classifier_version=classifier_version,
            organization_exact_names=organization_exact_names,
            organization_contains_terms=organization_contains_terms,
            query=query,
            execution_status=execution_status,
            budget_change_status=budget_change_status,
            limit=limit,
            offset=offset,
        )

    from budget_normalizer_v41 import normalize_record
    layers = {
        str(value or "").strip()
        for value in (source_layers or ())
        if str(value or "").strip()
    }
    terms = [
        str(value or "").strip()
        for value in (region_terms or ())
        if str(value or "").strip()
    ]
    result = []
    for row in current_raw_rows(selected):
        payload = _payload_dict(row.get("payload_json"))
        fact = normalize_record(
            str(row["dataset"]),
            payload,
            source_date=str(row.get("source_date") or ""),
        )
        if fiscal_year is not None and int(fact.get("fiscal_year") or 0) != int(fiscal_year):
            continue
        if layers and str(fact.get("source_layer") or "") not in layers:
            continue
        if terms:
            candidates = (
                str(fact.get("region_name") or ""),
                str(fact.get("org_name") or ""),
                str(fact.get("institution_name") or ""),
            )
            if not any(
                any(candidate.startswith(term) for candidate in candidates)
                for term in terms
            ):
                continue
        exact_names = {
            str(value or "").strip()
            for value in (organization_exact_names or ())
            if str(value or "").strip()
        }
        contains_terms = tuple(
            str(value or "").strip().casefold()
            for value in (organization_contains_terms or ())
            if str(value or "").strip()
        )
        if exact_names or contains_terms:
            organization_values = (
                str(fact.get("org_name") or ""),
                str(fact.get("institution_name") or ""),
                str(fact.get("dept_name") or ""),
            )
            exact_match = any(
                value in exact_names
                for value in organization_values[:2]
                if value
            )
            contains_match = any(
                term in value.casefold()
                for value in organization_values
                for term in contains_terms
                if value and term
            )
            if not (exact_match or contains_match):
                continue
        search = str(query or "").strip().casefold()
        if search:
            haystack = " ".join(
                str(fact.get(name) or "")
                for name in (
                    "project_name", "org_name", "dept_name",
                    "institution_name", "field_name",
                    "section_name", "account_name",
                )
            ).casefold()
            if search not in haystack:
                continue
        status = str(execution_status or "").strip().upper()
        executed = int(fact.get("executed_amount") or 0)
        remaining = int(fact.get("remaining_amount") or 0)
        if status == "UNEXECUTED" and executed > 0:
            continue
        if status == "PARTIAL" and not (executed > 0 and remaining > 0):
            continue
        if status == "FULL" and not (executed > 0 and remaining <= 0):
            continue
        if status not in {"", "UNEXECUTED", "PARTIAL", "FULL"}:
            raise ValueError("INVALID_BUDGET_EXECUTION_STATUS")
        result.append({
            "dataset": str(row["dataset"]),
            "record_key": str(row["source_key"]),
            "source_system": str(row.get("source_system") or ""),
            "source_operation": str(row.get("source_operation") or ""),
            "source_date": str(row.get("source_date") or ""),
            "last_seen_at": str(row.get("fetched_at") or ""),
            "payload_sha256": str(row.get("payload_sha256") or ""),
            **fact,
        })

    result.sort(
        key=lambda item: (
            str(item.get("source_date") or ""),
            str(item.get("record_key") or ""),
        ),
        reverse=True,
    )
    start = max(0, int(offset or 0))
    if limit is None:
        return result[start:]
    return result[start:start + max(1, min(int(limit), 5000))]


def current_payload_hashes(datasets=None):
    selected = tuple(datasets or BUDGET_DATASETS)
    if using_postgres():
        require_storage()
        return budget_pg_store.current_payload_hashes(selected)

    ensure_vnext_schema_for_read()
    placeholders = ",".join("?" for _ in selected)
    result = {}
    with connect() as conn:
        cursor = conn.execute(
            f"""SELECT dataset,source_key,payload_sha256
                FROM raw_records WHERE dataset IN ({placeholders})
                ORDER BY id""",
            selected,
        )
        while True:
            rows = cursor.fetchmany(2000)
            if not rows:
                break
            for row in rows:
                result[(str(row["dataset"]), str(row["source_key"]))] = str(
                    row["payload_sha256"] or ""
                )
    return result


def revision_project_rows(
    dataset,
    *,
    start_date="",
    end_date="",
    fiscal_year=None,
    region_terms=None,
    query="",
    limit=300,
    offset=0,
):
    """Read normalized revision history without source I/O."""
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if using_postgres():
        require_storage()
        return budget_pg_store.revision_project_rows(
            dataset,
            start_date=start_date,
            end_date=end_date,
            fiscal_year=fiscal_year,
            region_terms=region_terms,
            query=query,
            limit=limit,
            offset=offset,
        )

    ensure_vnext_schema_for_read()
    where = ["r.dataset=?"]
    params = [dataset]
    if str(start_date or "").strip():
        where.append("r.source_date>=?")
        params.append(str(start_date).strip())
    if str(end_date or "").strip():
        where.append("r.source_date<=?")
        params.append(str(end_date).strip())

    sql = (
        "SELECT r.id,r.source_system,r.source_operation,r.source_key,"
        "r.source_date,r.fetched_at,r.payload_json,r.payload_sha256 "
        "FROM raw_record_revisions r WHERE "
        + " AND ".join(where)
        + " ORDER BY r.source_date DESC,r.fetched_at DESC,r.id DESC"
    )
    with connect() as conn:
        raw_rows = conn.execute(sql, tuple(params)).fetchall()

    from budget_normalizer_v41 import normalize_record
    terms = [
        str(value or "").strip()
        for value in (region_terms or ())
        if str(value or "").strip()
    ]
    search = str(query or "").strip().casefold()
    out = []
    for row in raw_rows:
        payload = _payload_dict(row["payload_json"])
        fact = normalize_record(
            dataset,
            payload,
            source_date=str(row["source_date"] or ""),
        )
        if (
            fiscal_year is not None
            and int(fact.get("fiscal_year") or 0) != int(fiscal_year)
        ):
            continue
        if terms:
            candidates = (
                str(fact.get("region_name") or ""),
                str(fact.get("org_name") or ""),
                str(fact.get("institution_name") or ""),
            )
            if not any(
                any(candidate.startswith(term) for candidate in candidates)
                for term in terms
            ):
                continue
        if search:
            haystack = " ".join(str(fact.get(name) or "") for name in (
                "org_name", "dept_name", "institution_name", "project_name",
                "field_name", "section_name", "account_name",
            )).casefold()
            if search not in haystack:
                continue
        out.append({
            "dataset": str(dataset),
            "record_key": str(row["source_key"]),
            "observation_id": str(row["id"]),
            "source_system": str(row["source_system"] or ""),
            "source_operation": str(row["source_operation"] or ""),
            "source_date": str(row["source_date"] or ""),
            "observed_at": str(row["fetched_at"] or ""),
            "payload_sha256": str(row["payload_sha256"] or ""),
            **fact,
        })

    start = max(0, int(offset or 0))
    if limit is None:
        return out[start:]
    size = max(1, min(int(limit), 5000))
    return out[start:start + size]


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
    current_sha = budget_pg_store.current_payload_hash(dataset, source_key)
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
    for index, row in enumerate(
        budget_pg_store.all_revision_rows(
            dataset,
            source_date_prefix=source_date_prefix,
        ),
        1,
    ):
        rows.append({
            "id": str(row["id"]),
            "source_system": str(row.get("source_system") or ""),
            "source_operation": str(row.get("source_operation") or ""),
            "source_key": str(row["record_key"]),
            "source_date": str(row.get("source_date") or ""),
            "fetched_at": str(row.get("observed_at") or ""),
            "payload_json": json.dumps(
                row.get("payload") or {},
                ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            ),
            "payload_sha256": str(row.get("sha256") or ""),
            "current_payload_sha256": str(
                row.get("current_payload_sha256") or ""
            ),
            "ordinal": index,
        })
    return rows


def ensure_vnext_schema_for_read():
    with connect() as conn:
        ensure_vnext_schema(conn)


def purge_history(retention_days=365, *, receipt_retention_days=3):
    if not using_postgres():
        return {
            "expired_current_records": 0,
            "deleted_observations": 0,
            "retention_days": max(30, int(retention_days)),
            "receipt_retention_days": max(
                1, min(int(receipt_retention_days), 30)
            ),
            "backend": "SQLITE",
        }
    require_storage()
    result = dict(budget_pg_store.purge_history(
        retention_days,
        receipt_retention_days=receipt_retention_days,
    ))
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



def dataset_counts_all(datasets=None):
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - set(BUDGET_DATASETS)
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")

    if using_postgres():
        require_storage()
        return budget_pg_store.dataset_counts_all(selected)

    ensure_vnext_schema_for_read()
    raw_counts = {name: 0 for name in selected}
    revision_counts = {name: 0 for name in selected}
    last_seen = {name: "" for name in selected}
    placeholders = ",".join("?" for _ in selected)
    with connect() as conn:
        for row in conn.execute(
            f"""SELECT dataset,COUNT(*) AS n,MAX(fetched_at) AS last_at
                FROM raw_records
                WHERE dataset IN ({placeholders})
                GROUP BY dataset""",
            selected,
        ).fetchall():
            name = str(row["dataset"])
            raw_counts[name] = int(row["n"] or 0)
            last_seen[name] = str(row["last_at"] or "")
        for row in conn.execute(
            f"""SELECT dataset,COUNT(*) AS n
                FROM raw_record_revisions
                WHERE dataset IN ({placeholders})
                GROUP BY dataset""",
            selected,
        ).fetchall():
            revision_counts[str(row["dataset"])] = int(row["n"] or 0)

    return {
        name: {
            "dataset": name,
            "current_records": raw_counts[name],
            "observations": revision_counts[name],
            "superseded_observations": max(
                0, revision_counts[name] - raw_counts[name]
            ),
            "last_seen_at": last_seen[name],
        }
        for name in selected
    }


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
