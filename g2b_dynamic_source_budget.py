"""Opt-in 256MiB G2B source admission using measured raw cgroup bytes.

220,000,000 bytes is decimal, NOT 220MiB. Web and isolated source are
charged to the same cgroup, so worker RSS alone is never admission evidence.

The 32MiB start reserve is PROVISIONAL and MUST be verified by disposable
256MiB web+worker/container tests before any production rollout.
"""
from __future__ import annotations

import math
import os

MIB = 1 << 20
CGROUP_LIMIT_BYTES = 256 * MIB
SOURCE_CEILING_BYTES = 220_000_000
START_RESERVE_BYTES = 32 * MIB

MIN_STAGE_RESERVES = {
    "worker": 8 * MIB, "request": 4 * MIB,
    "read": 16 * MIB, "parse": 12 * MIB,
    "hash": 8 * MIB, "save": 12 * MIB,
    "classify": 8 * MIB,
}


def enabled(mode=""):
    """Flag inherited by worker; cannot be changed via source request."""
    return bool(
        str(mode or "") in {"shopping", "budget"}
        and str(os.getenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "0")).strip().lower()
        in {"1", "true", "on", "yes"}
    )


def _positive_int(value):
    if isinstance(value, bool):
        return 0
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return 0
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return result if result > 0 else 0


def _probe(snapshot):
    """Return raw bytes; reject estimated, rounded, or stale-format samples."""
    state = snapshot or {}
    source = state.get("source", state.get("cgroup_source", ""))
    limit = _positive_int(state.get("limit_bytes", state.get("cgroup_limit_bytes")))
    raw = _positive_int(state.get("current_bytes", state.get("cgroup_current_bytes")))
    if source != "cgroup-v2" or limit != CGROUP_LIMIT_BYTES or raw <= 0:
        return 0, "CGRP_DYNAMIC_PROBE_UNAVAILABLE"
    if _positive_int(state.get("oom_group", state.get("cgroup_oom_group"))):
        return 0, "CGRP_DYNAMIC_OOM_GROUP"
    if raw > limit:
        return 0, "CGRP_DYNAMIC_PROBE_UNAVAILABLE"
    return raw, ""


def headroom(snapshot, required_bytes, *, phase="worker"):
    """Fail closed BEFORE next allocation, using full raw cgroup memory.current."""
    raw, reason = _probe(snapshot)
    if reason:
        return False, reason, 0
    required = _positive_int(required_bytes)
    if required <= 0:
        return False, "CGRP_DYNAMIC_INVALID_RESERVE", 0
    free = max(0, SOURCE_CEILING_BYTES - raw)
    if required >= free:
        return False, "CGRP_DYNAMIC_" + str(phase).upper() + "_HOLD", free
    return True, "CGRP_DYNAMIC_" + str(phase).upper() + "_OK", free


def admission(snapshot, *, worker_soft_limit_mib=112):
    """Provisional pre-fork reserve; real launch peak remains to be proven."""
    try:
        soft = float(worker_soft_limit_mib)
    except (TypeError, ValueError):
        soft = 0.0
    if not math.isfinite(soft) or soft < 96 or soft > 112:
        return False, "CGRP_DYNAMIC_SOFT_LIMIT_UNSAFE"
    if snapshot.get("guard_ok") is not True or snapshot.get("process_guard_ok") is not True:
        return False, "CGRP_DYNAMIC_PARENT_GUARD_HELD"
    try:
        valid_rss = math.isfinite(float(snapshot.get("rss_mib"))) and float(snapshot.get("rss_mib")) > 0
    except (TypeError, ValueError):
        valid_rss = False
    if not valid_rss:
        return False, "CGRP_DYNAMIC_PARENT_RSS_UNAVAILABLE"
    allowed, reason, _ = headroom(snapshot, START_RESERVE_BYTES, phase="start")
    return allowed, reason


def required_for_phase(phase, *, payload_bytes=0):
    """Bound likely allocation peak; size-sensitive allowance overrides floor."""
    if phase not in MIN_STAGE_RESERVES:
        raise ValueError("UNSUPPORTED_CGROUP_SOURCE_PHASE")
    size = _positive_int(payload_bytes)
    multiple = {"read": 2, "parse": 4, "hash": 2, "save": 2, "classify": 3}.get(phase, 1)
    return max(MIN_STAGE_RESERVES[phase], size * multiple + 4 * MIB)


def assert_worker_phase(phase, *, payload_bytes=0):
    """Only disposable SOURCE_ONLY child may apply the opted-in phase guard."""
    if not enabled("budget"):
        return None
    if (
        str(os.getenv("G2B_RUNTIME_ROLE", "")).upper() != "LOCAL_COLLECTOR"
        or os.getenv("G2B_SOURCE_ONLY_RUNTIME") != "1"
        or str(os.getenv("G2B_TEST_MODE", "")).lower() in {"1", "true", "yes", "on"}
    ):
        return None
    import memory_guard
    state = memory_guard.container_budget_snapshot()
    allowed, reason, _ = headroom(
        state, required_for_phase(phase, payload_bytes=payload_bytes), phase=phase
    )
    if not allowed:
        raise memory_guard.MemoryPressureError(reason)
    return state


def bounded_read(response, max_bytes):
    """Read in 64KiB pieces and resample shared cgroup BEFORE each allocation."""
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("INVALID_SOURCE_RESPONSE_LIMIT")
    body = bytearray()
    chunk_size = 64 * 1024
    while True:
        size = min(chunk_size, max_bytes + 1 - len(body))
        if size <= 0:
            raise ValueError("SOURCE_RESPONSE_TOO_LARGE")
        assert_worker_phase("read", payload_bytes=len(body) + size)
        part = response.read(size)
        if not part:
            break
        if not isinstance(part, bytes):
            raise ValueError("SOURCE_RESPONSE_NOT_BYTES")
        if len(part) > size or len(body) + len(part) > max_bytes:
            raise ValueError("SOURCE_RESPONSE_TOO_LARGE")
        body.extend(part)
    assert_worker_phase("read", payload_bytes=len(body))
    return bytes(body)


def record_estimated_bytes(items):
    """Allocation-free order-of-magnitude estimate for ALREADY resident rows."""
    if not isinstance(items, (list, tuple)):
        return 0
    size = 0
    for row in items:
        size += 512
        if isinstance(row, dict):
            for key, value in row.items():
                size += 64 + (len(key) * 4 if isinstance(key, str) else 128)
                if isinstance(value, str):
                    size += len(value) * 4
                elif isinstance(value, (bytes, bytearray)):
                    size += len(value)
                elif isinstance(value, (list, tuple, dict)):
                    size += 4096
                else:
                    size += 128
        if size >= 64 * MIB:
            return size
    return size


def parent_abort_reason(snapshot):
    """Read web+worker raw usage even when child RSS appears small."""
    raw, reason = _probe(snapshot)
    if reason:
        return reason
    return "CGRP_DYNAMIC_220000000_BYTE_HOLD" if raw >= SOURCE_CEILING_BYTES else ""
