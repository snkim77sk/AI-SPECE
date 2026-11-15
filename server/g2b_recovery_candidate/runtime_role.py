"""Runtime role separation for AI-SPECE hybrid deployment."""
from __future__ import annotations

import os

RESULT_SERVER = "RESULT_SERVER"
LOCAL_COLLECTOR = "LOCAL_COLLECTOR"
_ALLOWED = frozenset({RESULT_SERVER, LOCAL_COLLECTOR})


def runtime_role():
    value = str(os.getenv("G2B_RUNTIME_ROLE", RESULT_SERVER) or RESULT_SERVER).strip().upper()
    return value if value in _ALLOWED else RESULT_SERVER


def is_result_server():
    return runtime_role() == RESULT_SERVER


def is_local_collector():
    return runtime_role() == LOCAL_COLLECTOR
