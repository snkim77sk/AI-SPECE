"""Source-free small-cgroup admission policy for G2B Cafe24 safe boot."""
from __future__ import annotations
import math
import time

BOOT_GRACE_SECONDS = 300
LOW_MEMORY_LIMIT_MIB = 320.0
CHILD_MIN_SOFT_MIB = 96.0
CHILD_MAX_SOFT_MIB = 160.0
CHILD_HEADROOM_RESERVE_MIB = 64.0

def boot_grace_remaining(started_monotonic, *, low_memory, test_mode=False, now=None):
    if test_mode or not low_memory:
        return 0
    current = time.monotonic() if now is None else float(now)
    elapsed = max(0.0, current - float(started_monotonic))
    return max(0, int(math.ceil(BOOT_GRACE_SECONDS - elapsed)))

def child_admission(state, *, worker_soft_limit_mib=112):
    """Return (allowed, reason) without database access or source API requests."""
    snapshot = state or {}
    if not snapshot.get("guard_ok", False):
        return False, "MEMORY_GUARD_HELD"
    if int(snapshot.get("cgroup_oom_group") or 0) == 1:
        return False, "CGROUP_OOM_GROUP"
    limit = float(snapshot.get("cgroup_limit_mib") or 0)
    if limit <= 0:
        return False, "CGROUP_LIMIT_UNAVAILABLE"
    if limit > LOW_MEMORY_LIMIT_MIB:
        return True, "LARGE_CGROUP_EXISTING_GUARD"
    effective = float(snapshot.get("cgroup_effective_mib") or 0)
    if effective <= 0:
        return False, "CGRP_PRESSURE_UNAVAILABLE"
    try:
        worker = float(worker_soft_limit_mib)
    except (TypeError, ValueError):
        worker = 112.0
    expected = max(CHILD_MIN_SOFT_MIB, min(CHILD_MAX_SOFT_MIB, worker))
    if effective + expected + CHILD_HEADROOM_RESERVE_MIB >= limit:
        return False, "CGRP_CHILD_HEADROOM_UNSAFE"
    return True, "CGRP_CHILD_HEADROOM_OK"
