"""Retired G2B 4.0 scope migration compatibility shim.

The historical scope reset deleted collected shopping, bid/service and
checkpoint records, source settings, and cached result snapshots. G2B 4.1.x
now preserves all existing database content. This module deliberately has NO
database, filesystem, snapshot or source-API imports, and no force override.

Keep public constant/function names for old callers; never silently execute
the historical mutation if a stale boot hook imports this module.
"""
from __future__ import annotations


MIGRATION_KEY = "g2b_v4_scope_reset_data_complete"
SNAPSHOT_MIGRATION_KEY = "g2b_v4_scope_reset_snapshot_complete"
MIGRATION_VALUE = "1"

# Historical identifiers are retained as inert compatibility metadata. They
# do not select rows, authorize data changes, or trigger source re-collection.
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


def apply_v4_scope_reset(*, force=False):
    """Refuse historical reset without DB, filesystem, snapshot or HTTP I/O.

    Even force=True cannot delete 4.1 shopping/LED, budget, checkpoints,
    revisions, receipts, credentials or result snapshots. Do not mark an
    unexecuted migration as complete or report a snapshot as cleared.
    """
    return {
        "status": "RETIRED",
        "reason": "G2B_V4_SCOPE_RESET_RETIRED",
        "already_applied": None,  # Intentionally not inferred without SQL.
        "deleted": {},
        "snapshot_cleared": False,
        "budget_preserved": True,
        "shopping_preserved": True,
        "checkpoints_preserved": True,
        "result_snapshot_preserved": True,
        "source_api_calls": 0,
        "force_ignored": bool(force),
    }
