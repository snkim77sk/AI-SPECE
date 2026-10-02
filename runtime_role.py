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
