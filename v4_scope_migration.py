"""One-time destructive scope reset for the owner-approved G2B 4.0 redesign.

The reset intentionally removes old shopping-wide and service/bid lifecycle data.
Budget RAW/projections, admin/auth, API credentials and general settings are retained.
"""
from __future__ import annotations

from db import connect, get_setting, set_setting
from vnext_store import ensure_foundation
import result_snapshot_vnext

MIGRATION_KEY = "g2b_v4_scope_reset_complete"
MIGRATION_VALUE = "1"

RESET_DATASETS = (
    "shopping_delivery",
    "bid_notice_goods",
    "bid_notice_service",
    "opening_result_service",
    "award_result_service",
    "contract_service",
)

SHOPPING_STATE_KEYS = (
    "shopping_recent_state",
    "shopping_recent_last_started_at_kst",
    "shopping_recent_last_finished_at_kst",
    "shopping_recent_last_completed_date",
    "shopping_recent_last_error",
    "shopping_recent_last_status",
    "shopping_recent_order",
    "shopping_recent_start_date",
    "shopping_recent_latest_available_date",
)


def _table_exists(conn, name):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),),
    ).fetchone())


def apply_v4_scope_reset(*, force=False):
    """Delete obsolete procurement data once; preserve all budget datasets."""
    ensure_foundation()
    if not force and get_setting(MIGRATION_KEY, "") == MIGRATION_VALUE:
        return {"status": "SKIPPED", "already_applied": True, "deleted": {}}

    deleted = {}
    placeholders = ",".join("?" for _ in RESET_DATASETS)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        for table, column in (
            ("vnext_collection_items", "dataset"),
            ("vnext_collection_pages", "dataset"),
            ("collection_checkpoints", "dataset"),
            ("classifications", "entity_type"),
            ("raw_record_revisions", "dataset"),
            ("raw_records", "dataset"),
        ):
            if not _table_exists(conn, table):
                continue
            before = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders})",
                RESET_DATASETS,
            ).fetchone()[0] or 0)
            conn.execute(
                f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
                RESET_DATASETS,
            )
            deleted[table] = before

        # Lifecycle/award tables belong to the removed NO1-overlapping feature set.
        for table in ("award_results", "lifecycle_links", "vnext_contract_projection"):
            if not _table_exists(conn, table):
                continue
            before = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
            conn.execute(f"DELETE FROM {table}")
            deleted[table] = before

        if _table_exists(conn, "app_settings"):
            for key in SHOPPING_STATE_KEYS:
                conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
            conn.execute(
                """INSERT INTO app_settings(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (MIGRATION_KEY, MIGRATION_VALUE),
            )

    # Old hybrid snapshots may contain shopping/service results from the deleted scope.
    # Clear them as one unit so no stale rows can be exposed after the v4 migration.
    snapshot_cleared = False
    try:
        result_snapshot_vnext.clear_snapshot()
        snapshot_cleared = True
    except Exception:
        # Serving-snapshot cleanup is recoverable and must not roll back the already
        # committed source-scope migration.
        snapshot_cleared = False

    return {
        "status": "COMPLETE",
        "already_applied": False,
        "deleted": deleted,
        "snapshot_cleared": snapshot_cleared,
        "budget_preserved": True,
    }
