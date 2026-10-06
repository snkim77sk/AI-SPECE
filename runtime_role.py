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

    4.1.153 recovery policy:
    - Production source collection is explicit opt-in only.
    - UNIFIED and LOCAL_COLLECTOR both require G2B_AUTO_SYNC=1.
    - G2B_AUTO_SYNC_DISABLE=1 remains the emergency kill-switch and always wins.
    - RESULT_SERVER and tests never collect sources.
    - This keeps full-runtime reattachment separate from source I/O.
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

    # Recovery safety: every source-capable production role is explicit opt-in.
    requested = (
        str(os.getenv("G2B_AUTO_SYNC", "0") or "0")
        .strip().lower()
    )
    return requested in _TRUE
