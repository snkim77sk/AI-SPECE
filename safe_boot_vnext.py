"""Source-free small-cgroup admission policy for G2B Cafe24 safe boot."""
from __future__ import annotations
import math
import time

BOOT_GRACE_SECONDS = 300
LOW_MEMORY_LIMIT_MIB = 320.0
CHILD_MIN_SOFT_MIB = 96.0
CHILD_MAX_SOFT_MIB = 160.0
CHILD_HEADROOM_RESERVE_MIB = 64.0

# V4.1.297: opt-in diagnostic forecast for SOURCE-ONLY workers in a proven
# 256MiB cgroup. This is a forecast of extra cgroup occupancy, not process
# RSS or a lowered 96MiB process-soft-limit. In disposable CI the sampled
# peak delta from a genuine 113.3MiB baseline was ~46MiB, so 76MiB retains
# ~30MiB forecast margin. Production remains on the legacy 112MiB forecast
# until an operator explicitly enables this narrow canary.
SOURCE_INCREMENTAL_LIMIT_MIB = 256.0
SOURCE_INCREMENTAL_FORECAST_MIB = 76.0
SOURCE_INCREMENTAL_MODES = frozenset({"shopping", "budget"})


def source_incremental_forecast(state, *, mode="", enabled=False):
    """Return the optional cgroup INCREMENT forecast, or None for legacy."""
    snapshot = state or {}
    if not bool(enabled) or str(mode or "") not in SOURCE_INCREMENTAL_MODES:
        return None
    try:
        limit = float(snapshot.get("cgroup_limit_mib") or 0.0)
    except (TypeError, ValueError):
        return None
    # 512MiB/match/unknown-tier workloads ALWAYS retain legacy admission.
    if limit != SOURCE_INCREMENTAL_LIMIT_MIB:
        return None
    return SOURCE_INCREMENTAL_FORECAST_MIB


def boot_grace_remaining(started_monotonic, *, low_memory, test_mode=False, now=None):
    if test_mode or not low_memory:
        return 0
    current = time.monotonic() if now is None else float(now)
    elapsed = max(0.0, current - float(started_monotonic))
    return max(0, int(math.ceil(BOOT_GRACE_SECONDS - elapsed)))

def child_admission(state, *, worker_soft_limit_mib=112, source_mode="", incremental_source_enabled=False):
    """Return (allowed, reason) without database access or source API requests."""
    snapshot = state or {}
    if not snapshot.get("guard_ok", False):
        return False, "MEMORY_GUARD_HELD"
    if int(snapshot.get("cgroup_oom_group") or 0) == 1:
        return False, "CGROUP_OOM_GROUP"
    limit = float(snapshot.get("cgroup_limit_mib") or 0)
    if limit <= 0:
        return False, "CGROUP_LIMIT_UNAVAILABLE"
    # The admission forecast is required at both 256MiB and 512MiB. Previous
    # versions bypassed the child RSS + 64MiB reserve above 320MiB and ran
    # source work in the web process. Neither behavior is safe after upsizing.
    effective = float(snapshot.get("cgroup_effective_mib") or 0)
    if effective <= 0:
        return False, "CGRP_PRESSURE_UNAVAILABLE"
    try:
        worker = float(worker_soft_limit_mib)
    except (TypeError, ValueError):
        worker = 112.0
    expected = max(CHILD_MIN_SOFT_MIB, min(CHILD_MAX_SOFT_MIB, worker))
    increment = source_incremental_forecast(
        snapshot, mode=source_mode, enabled=incremental_source_enabled
    )
    if increment is not None:
        # A source-only cgroup-increment forecast is safe only when the same
        # kernel cgroup-v2 reports real, current raw AND effective occupancy.
        # Missing/currently unbounded readings must never be an allow signal.
        if snapshot.get("cgroup_source") != "cgroup-v2":
            return False, "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE"
        if snapshot.get("process_guard_ok") is not True:
            return False, "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE"
        try:
            raw_current = float(snapshot.get("cgroup_current_mib") or 0)
        except (TypeError, ValueError):
            raw_current = 0
        if (
            not math.isfinite(raw_current)
            or not math.isfinite(effective)
            or raw_current <= 0
            or raw_current > limit
            or effective > limit
        ):
            return False, "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE"
        # Reclaimable cache MAY lower the effective number, but for admission
        # use the MORE conservative raw current reading if it is higher.
        # Web headroom remains the original 64MiB and >= comparisons fail.
        if max(raw_current, effective) + increment + CHILD_HEADROOM_RESERVE_MIB >= limit:
            return False, "CGRP_SOURCE_INCREMENTAL_HEADROOM_UNSAFE"
        return True, "CGRP_SOURCE_INCREMENTAL_HEADROOM_OK"
    if effective + expected + CHILD_HEADROOM_RESERVE_MIB >= limit:
        return False, "CGRP_CHILD_HEADROOM_UNSAFE"
    if limit > LOW_MEMORY_LIMIT_MIB:
        return True, "LARGE_CGROUP_CHILD_HEADROOM_OK"
    return True, "CGRP_CHILD_HEADROOM_OK"
