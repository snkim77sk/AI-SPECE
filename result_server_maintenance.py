"""Explicit Cafe24 source-data cleanup after a verified local result snapshot exists."""
from __future__ import annotations

import os
import sqlite3

import result_snapshot_vnext
from db import backend_name, connect, current_db_path

HEAVY_SOURCE_TABLES = (
    "vnext_collection_items",
    "vnext_collection_pages",
    "vnext_contract_projection",
    "vnext_budget_projection",
    "award_results",
    "lifecycle_links",
    "classifications",
    "collection_checkpoints",
    "raw_record_revisions",
    "raw_records",
)


def _file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def compact_result_server_source_data():
    """Compact source-heavy SQLite only in the retired compatibility backend.

    Production 4.1 storage is PostgreSQL and does not store source JSON RAW in a
    local application SQLite file. Never run sqlite_master, DROP TABLE, VACUUM,
    or sqlite3.connect() against the production PostgreSQL logical locator.
    """
    if backend_name() != "SQLITE_TEST":
        return {
            "status": "SKIPPED_POSTGRESQL",
            "reason": "POSTGRESQL_NO_SOURCE_SQLITE_COMPACTION",
            "storage_backend": backend_name(),
            "dropped_tables": [],
            "vacuumed": False,
            "bytes_before": 0,
            "bytes_after": 0,
        }

    if not result_snapshot_vnext.snapshot_available():
        raise RuntimeError("RESULT_SNAPSHOT_REQUIRED_BEFORE_COMPACTION")
    path = current_db_path()
    before = _file_size(path)
    dropped = []
    with connect() as conn:
        existing = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in HEAVY_SOURCE_TABLES:
            if table in existing:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                dropped.append(table)
        conn.execute(
            """INSERT INTO app_settings(key,value)
               VALUES('g2b_result_server_source_compacted','1')
               ON CONFLICT(key) DO UPDATE SET value='1'"""
        )

    # VACUUM must run outside an open transaction. Failure is non-fatal: the logical
    # source tables are already gone and a later maintenance run can reclaim pages.
    vacuumed = False
    try:
        conn = sqlite3.connect(path, timeout=30.0)
        try:
            conn.execute("VACUUM")
            vacuumed = True
        finally:
            conn.close()
    except sqlite3.Error:
        vacuumed = False

    return {
        "dropped_tables": dropped,
        "vacuumed": vacuumed,
        "bytes_before": before,
        "bytes_after": _file_size(path),
    }
