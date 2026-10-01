"""One-time destructive scope reset for the owner-approved G2B 4.0 redesign.

The reset intentionally removes old shopping-wide and service/bid lifecycle data.
Budget RAW/projections, admin/auth, API credentials and general settings are retained.
"""
from __future__ import annotations

from db import connect, get_setting, set_setting
from vnext_store import ensure_foundation
import result_snapshot_vnext

MIGRATION_KEY = "g2b_v4_scope_reset_data_complete"
SNAPSHOT_MIGRATION_KEY = "g2b_v4_scope_reset_snapshot_complete"
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
    """Delete obsolete procurement data once; preserve all budget datasets.

    Source-scope deletion and compatibility-snapshot cleanup have separate durable
    markers. If snapshot cleanup fails after the destructive SQLite transaction has
    committed, the next startup retries only snapshot cleanup instead of exposing
    stale service/shopping rows forever.
    """
    ensure_foundation()
    data_complete = get_setting(MIGRATION_KEY, "") == MIGRATION_VALUE
    snapshot_complete = get_setting(SNAPSHOT_MIGRATION_KEY, "") == MIGRATION_VALUE
    if not force and data_complete and snapshot_complete:
        return {
            "status": "SKIPPED",
            "already_applied": True,
            "deleted": {},
            "snapshot_cleared": True,
            "budget_preserved": True,
        }

    deleted = {}
    if force or not data_complete:
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
        data_complete = True

    snapshot_cleared = bool(snapshot_complete and not force)
    if force or not snapshot_complete:
        try:
            result_snapshot_vnext.clear_snapshot()
            set_setting(SNAPSHOT_MIGRATION_KEY, MIGRATION_VALUE)
            snapshot_cleared = True
            snapshot_complete = True
        except Exception:
            # Retry on the next startup. The source-scope deletion marker remains
            # committed so destructive deletion is not unnecessarily repeated.
            snapshot_cleared = False
            snapshot_complete = False

    return {
        "status": "COMPLETE" if data_complete and snapshot_complete else "PARTIAL",
        "already_applied": False,
        "deleted": deleted,
        "snapshot_cleared": snapshot_cleared,
        "budget_preserved": True,
    }
