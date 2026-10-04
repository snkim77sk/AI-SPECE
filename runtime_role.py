"""Runtime roles for AI-SPECE G2B 4.1.

Cafe24 runs as UNIFIED by default: normalized shopping/business collection, current
QWGJK and future AIDFA budget flows share one application and one PostgreSQL source
of truth. RESULT_SERVER/LOCAL_COLLECTOR remain rollback/compatibility roles only.
"""
from __future__ import annotations

import os

UNIFIED = "UNIFIED"
RESULT_SERVER = "RESULT_SERVER"
LOCAL_COLLECTOR = "LOCAL_COLLECTOR"
_ALLOWED = frozenset({UNIFIED, RESULT_SERVER, LOCAL_COLLECTOR})


def runtime_role():
    value = str(os.getenv("G2B_RUNTIME_ROLE", UNIFIED) or UNIFIED).strip().upper()
    return value if value in _ALLOWED else UNIFIED


def is_unified():
    return runtime_role() == UNIFIED


def is_result_server():
    return runtime_role() == RESULT_SERVER


def is_local_collector():
    return runtime_role() == LOCAL_COLLECTOR


def can_collect_sources():
    return runtime_role() in {UNIFIED, LOCAL_COLLECTOR}


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def automatic_collection_enabled(*, test_mode=None):
    """Return whether the recurring source worker should run.

    4.1.138 owner policy:
    - Cafe24/default UNIFIED production runs source collection automatically.
    - The legacy G2B_AUTO_SYNC=0 value is ignored for UNIFIED because previous
      deployment instructions explicitly set it to 0; otherwise a code-only
      redeploy could never activate automatic collection.
    - Emergency shutdown uses G2B_AUTO_SYNC_DISABLE=1.
    - RESULT_SERVER and tests never collect sources.
    - LOCAL_COLLECTOR remains opt-in through G2B_AUTO_SYNC=1 for rollback/
      compatibility safety.
    """
    if test_mode is None:
        test_mode = (
            str(os.getenv("G2B_TEST_MODE", "0") or "0").strip().lower()
            in _TRUE
        )
    if bool(test_mode):
        return False

    role = runtime_role()
    if role == RESULT_SERVER:
        return False

    disabled = (
        str(os.getenv("G2B_AUTO_SYNC_DISABLE", "0") or "0")
        .strip().lower()
    )
    if disabled in _TRUE:
        return False

    if role == UNIFIED:
        return True

    # LOCAL_COLLECTOR stays explicitly opt-in to avoid surprise source I/O on
    # compatibility/local machines.
    requested = (
        str(os.getenv("G2B_AUTO_SYNC", "0") or "0")
        .strip().lower()
    )
    return requested in _TRUE
