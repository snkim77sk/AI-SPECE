"""Explicit Cafe24 source-data cleanup after a verified local result snapshot exists."""
from __future__ import annotations

import os
import sqlite3

import result_snapshot_vnext
from db import connect, current_db_path

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
    """Drop source-heavy tables only after an active compact snapshot exists.

    Admin/auth/settings/credential tables are never touched. Source tables are
    recreated empty on the next normal backend schema initialization.
    """
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
