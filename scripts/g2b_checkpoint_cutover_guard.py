"""Read-only 4.1.238 -> stacked G2B checkpoint-schema cutover audit.

Operator-only. This does not shut down old processes, run migrations, change
release markers, recalculate business rows or inspect the entire checkpoint
history. Run alongside the independent cold-process/advisory-lease guard.
"""
from __future__ import annotations

import json
import os
from contextlib import closing
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import g2b_database

EXPECTED_MARKER = "NORMALIZED_NO_RAW_V1"
MARKER_TABLE = "release_bootstrap"
MARKER_SCHEMA = "g2b_meta"
CHECKPOINT_COLUMNS = frozenset({
    "dataset", "scope_key", "cursor_value", "range_start", "range_end",
    "page_no", "page_size", "last_page_fingerprint", "source_total",
    "fetched_count", "saved_count", "status", "last_error", "updated_at",
})
EXPECTED_PK = ("dataset", "scope_key")
_TRUE = frozenset({"1", "true", "yes", "on"})


def _enabled(name):
    return str(os.getenv(name, "0") or "0").strip().lower() in _TRUE


def _open_read_only_connection():
    # A one-use direct connection does not consume the web's 1+1 SQLAlchemy
    # checkout pool. Never reuse an active collector lease session.
    return g2b_database._operational_lease_connection()


def _table_columns(conn, schema, table):
    result = conn.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema=%s AND table_name=%s""",
        (schema, table),
    )
    return {str(row[0]) for row in result.fetchall()}


def _primary_key(conn, schema, table):
    result = conn.execute(
        """SELECT kcu.column_name
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage kcu
             ON tc.constraint_catalog=kcu.constraint_catalog
            AND tc.constraint_schema=kcu.constraint_schema
            AND tc.constraint_name=kcu.constraint_name
            AND tc.table_name=kcu.table_name
           WHERE tc.table_schema=%s AND tc.table_name=%s
             AND tc.constraint_type='PRIMARY KEY'
           ORDER BY kcu.ordinal_position""",
        (schema, table),
    )
    return tuple(str(row[0]) for row in result.fetchall())


def _one_checkpoint_sample(conn, schema, table):
    # Validated identifiers come from g2b_database.validate_schema_layout()
    # plus fixed, source-controlled table names. psycopg quotes identifiers.
    from psycopg import sql

    query = sql.SQL("SELECT page_no, status FROM {}.{} LIMIT 1").format(
        sql.Identifier(schema), sql.Identifier(table),
    )
    row = conn.execute(query).fetchone()
    if row is None:
        return False
    page_no, status = row
    if page_no is None or int(page_no) < 0 or not str(status or ""):
        raise ValueError("G2B_CHECKPOINT_SAMPLE_INVALID")
    return True


def inspect_checkpoint_cutover():
    """Check checkpoint ABI without DDL/DML, source HTTP or full-row scans."""
    report = {
        "gate": "G2B_V41_READ_ONLY_CHECKPOINT_SCHEMA_CUTOVER",
        "read_only": True,
        "business_rows_modified": 0,
        "schema_ddl_performed": False,
        "source_api_calls": 0,
        "schema_checkpoint_compatible": False,
        "marker_ok": False,
        "checkpoint_tables_checked": 0,
        "checkpoint_sample_rows": 0,
        "samples_are_not_full_data_integrity_proof": True,
        "issues": [],
    }
    issues = report["issues"]
    if _enabled("G2B_TEST_MODE"):
        issues.append("TEST_MODE_ENABLED")
        return report
    role = str(os.getenv("G2B_RUNTIME_ROLE", "UNIFIED") or "").strip().upper()
    if role != "UNIFIED":
        issues.append("UNIFIED_RUNTIME_REQUIRED")
        return report
    if _enabled("G2B_V41_FRESH_START") or _enabled(
        "G2B_DESTRUCTIVE_RESET_CONFIRM"
    ):
        issues.append("DISABLE_HISTORICAL_RESET_FLAGS")
        return report

    try:
        app_schema, budget_schema = g2b_database.validate_schema_layout()
        with closing(_open_read_only_connection()) as conn:
            # Prevent accidental writes even if another developer modifies the
            # diagnostic SQL later. Set only limits and read-only session mode.
            conn.execute("SET default_transaction_read_only TO on")
            conn.execute("SET statement_timeout TO 2500")
            conn.execute("SET lock_timeout TO 1000")

            meta_cols = _table_columns(conn, MARKER_SCHEMA, MARKER_TABLE)
            if not {"key", "value"} <= meta_cols:
                issues.append("RELEASE_MARKER_TABLE_MISSING_OR_INVALID")
            else:
                row = conn.execute(
                    'SELECT value FROM "g2b_meta"."release_bootstrap" WHERE key=%s',
                    ("fresh_start_4_1_0",),
                ).fetchone()
                report["marker_ok"] = bool(row and str(row[0]) == EXPECTED_MARKER)
                if not report["marker_ok"]:
                    issues.append("RELEASE_MARKER_MISMATCH_OR_MISSING")

            for label, schema in (("APP", app_schema), ("BUDGET", budget_schema)):
                table = (
                    "collection_checkpoints" if label == "APP"
                    else "budget_collection_checkpoints"
                )
                columns = _table_columns(conn, schema, table)
                if not columns:
                    issues.append(f"{label}_CHECKPOINT_TABLE_MISSING")
                    continue
                missing = CHECKPOINT_COLUMNS - columns
                if missing:
                    issues.append(f"{label}_CHECKPOINT_COLUMNS_INCOMPATIBLE")
                    continue
                if _primary_key(conn, schema, table) != EXPECTED_PK:
                    issues.append(f"{label}_CHECKPOINT_PK_INCOMPATIBLE")
                    continue
                report["checkpoint_tables_checked"] += 1
                if _one_checkpoint_sample(conn, schema, table):
                    report["checkpoint_sample_rows"] += 1
    except Exception as exc:
        # Never expose passwords, hostnames, SQL, or source keys in diagnostics.
        issues.append("POSTGRES_CHECK_FAILED_" + type(exc).__name__)

    report["schema_checkpoint_compatible"] = bool(
        not issues and report["marker_ok"]
        and report["checkpoint_tables_checked"] == 2
    )
    return report


def main():
    result = inspect_checkpoint_cutover()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["schema_checkpoint_compatible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
