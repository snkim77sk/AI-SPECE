"""Idle-only, bounded G2B memory reclamation inspired by SINSUNG.

Never kills/restarts the web or any worker. Never touches PostgreSQL, source
APIs, checkpoint/receipt files, or live collector admission thresholds.
An idle web can ask glibc to return previously freed heap pages to the kernel,
but memory reclamation is best-effort and may reclaim zero bytes.
"""
from __future__ import annotations

import asyncio
import gc
import math
import os
import threading
import time
from pathlib import Path

import memory_guard
import safe_boot_vnext

# Cheap eligibility checks are independent of actual GC/trim frequency.
# A busy 15-minute tick must not defer an idle pressure retry by 15 minutes.
CHECK_INTERVAL_SECONDS = 60.0
DEFAULT_PERIODIC_HOURS = 72
MIN_PERIODIC_HOURS = 48
MAX_PERIODIC_HOURS = 72
PRESSURE_IDLE_SECONDS = 45.0
NORMAL_IDLE_SECONDS = 180.0
PRESSURE_COOLDOWN_SECONDS = 60 * 60.0
# Strictly optional, best-effort kernel cache reclaim (not a memory gate bypass).
CGROUP_FILE_RECLAIM_CAP_MIB = 64
CGROUP_FILE_RECLAIM_MIN_MIB = 8
_START_MONOTONIC = time.monotonic()
_LOCK = threading.RLock()
_ACTIVE_REQUESTS = 0
_LAST_ACTIVITY = time.monotonic()
_LAST_ATTEMPT = 0.0
_ATTEMPTS = 0
_SKIPS = 0
_LAST_RESULT = {"status": "waiting", "reason": "startup"}
_LAST_CHECK_RESULT = {"status": "waiting", "reason": "startup"}
_LAST_CHECK_MONOTONIC = 0.0
_NEXT_CHECK_MONOTONIC = 0.0
_LAST_ACTIVITY_CATEGORY = "startup"

# Exclude only cheap, read-only health/status refreshes from the idle clock.
# Mutating controls, export, heavy pages and other visits remain counted.
_PROBE_PATHS = frozenset({
    "/live", "/health", "/ready", "/__ai_space_health",
})
# Status HTML can query saved PostgreSQL and render substantial data. Count it
# as an active request so reclaim NEVER overlaps the read. Exclude only its
# completion from the idle clock: an automatic 30s refresh must not starve GC.
_PASSIVE_READ_PATHS = frozenset({
    # The platform root redirects to /login without DB reads once ready.
    # Keep active-request fencing; only its GET/HEAD idle-clock update is skipped.
    "/",
    "/collection-monitor", "/api/collection-status",
    # Browser navigation/refresh can request this even without a favicon link.
    # Count active work, but do not mistake it for a new user interaction.
    "/favicon.ico",
})


def enabled():
    return str(os.getenv("G2B_IDLE_MEMORY_RECOVERY", "1") or "1").strip().lower() not in {
        "0", "false", "no", "off"
    }


def periodic_seconds():
    try:
        hours = int(str(
            os.getenv("G2B_IDLE_MEMORY_RECLAIM_HOURS", DEFAULT_PERIODIC_HOURS)
        ).strip())
    except (TypeError, ValueError):
        hours = DEFAULT_PERIODIC_HOURS
    return max(MIN_PERIODIC_HOURS, min(MAX_PERIODIC_HOURS, hours)) * 3600.0


def mark_request_start():
    global _ACTIVE_REQUESTS
    with _LOCK:
        _ACTIVE_REQUESTS += 1


async def _mark_request_start_async():
    """Wait for idle reclaim without blocking the HTTP event loop.

    The handler stays outside the recovery window. No await occurs between
    acquiring the lock and counting the request, so cancellation while waiting
    cannot leave an active-request count behind.
    """
    global _ACTIVE_REQUESTS
    while not _LOCK.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        _ACTIVE_REQUESTS += 1
    finally:
        _LOCK.release()


def mark_request_end(*, update_idle_clock=True, activity_category="unclassified"):
    global _ACTIVE_REQUESTS, _LAST_ACTIVITY, _LAST_ACTIVITY_CATEGORY
    with _LOCK:
        _ACTIVE_REQUESTS = max(0, _ACTIVE_REQUESTS - 1)
        if update_idle_clock:
            _LAST_ACTIVITY = time.monotonic()
            _LAST_ACTIVITY_CATEGORY = str(activity_category)


def _activity_category(scope):
    """Fixed labels only: never retain request URLs, query strings or identifiers."""
    if str(scope.get("method", "GET")).upper() not in {"GET", "HEAD"}:
        return "mutation"
    return {
        "/login": "login",
        "/dashboard": "dashboard",
        "/dashboard-loader.js": "dashboard-loader",
        "/api/dashboard-summary": "dashboard-summary",
        "/api/status": "status-api",
    }.get(scope.get("path"), "other-read")


class ActivityMiddleware:
    """Account for user requests through complete streaming response/exception."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") in _PROBE_PATHS:
            return await self.app(scope, receive, send)
        passive = (scope.get("path") in _PASSIVE_READ_PATHS
                   and str(scope.get("method", "GET")).upper() in {"GET", "HEAD"})
        await _mark_request_start_async()
        try:
            return await self.app(scope, receive, send)
        finally:
            mark_request_end(
                update_idle_clock=not passive,
                activity_category=_activity_category(scope),
            )


def _trim_allocator():
    """Request glibc heap trim; not a service restart or guaranteed reduction."""
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        trim = getattr(libc, "malloc_trim", None)
        if trim is None:
            return False
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        return bool(trim(0))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _record_check(result):
    global _LAST_CHECK_RESULT, _LAST_CHECK_MONOTONIC
    with _LOCK:
        _LAST_CHECK_RESULT = dict(result)
        _LAST_CHECK_MONOTONIC = time.monotonic()
    return dict(result)


def record_skipped_cycle(reason):
    """Also used when the outer worker-launch barrier cannot be acquired."""
    global _SKIPS
    with _LOCK:
        _SKIPS += 1
        return _record_check({"status": "skipped", "reason": str(reason)})


_skip = record_skipped_cycle


def recovery_status():
    with _LOCK:
        reference = _LAST_ATTEMPT if _LAST_ATTEMPT > 0 else _START_MONOTONIC
        return {
            "enabled": enabled(),
            "active_requests": int(_ACTIVE_REQUESTS),
            "seconds_since_last_activity": round(
                max(0.0, time.monotonic() - _LAST_ACTIVITY), 1
            ),
            "attempts": int(_ATTEMPTS),
            "skips": int(_SKIPS),
            "check_interval_seconds": CHECK_INTERVAL_SECONDS,
            "next_check_due_seconds": round(max(
                0.0, _NEXT_CHECK_MONOTONIC - time.monotonic()
            ), 1) if _NEXT_CHECK_MONOTONIC > 0 else None,
            "seconds_since_last_check": round(max(
                0.0, time.monotonic() - _LAST_CHECK_MONOTONIC
            ), 1) if _LAST_CHECK_MONOTONIC > 0 else None,
            "last_check": dict(_LAST_CHECK_RESULT),
            "last_activity_category": _LAST_ACTIVITY_CATEGORY,
            "normal_periodic_hours": periodic_seconds() / 3600.0,
            "normal_periodic_due_seconds": round(max(
                0.0, periodic_seconds() - (time.monotonic() - reference)
            ), 1),
            "last_result": dict(_LAST_RESULT),
            "does_not_restart_web": True,
            "persistent_data_untouched": True,
        }



def _source_start_25_active(snapshot):
    """Match the production UNIFIED start policy, never invent headroom."""
    return bool(
        os.getenv("G2B_TEST_MODE", "0").strip().lower() not in
        {"1", "true", "yes", "on"}
        and os.getenv("G2B_RUNTIME_ROLE", "UNIFIED").strip().upper() == "UNIFIED"
        and str((snapshot or {}).get("cgroup_source") or "") == "cgroup-v2"
        and float((snapshot or {}).get("cgroup_limit_mib") or 0) == 256.0
        and os.getenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "25").strip() == "25"
        and os.getenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "0").strip().lower()
        not in {"1", "true", "yes", "on"}
    )


def _write_cgroup_file_reclaim(size_bytes):
    """One bounded cgroup-v2 write. Never use drop_caches, swap or kill."""
    with Path("/sys/fs/cgroup/memory.reclaim").open(
        "w", encoding="ascii"
    ) as stream:
        # swappiness=0 avoids proactive anonymous-memory swapping.
        stream.write(f"{int(size_bytes)} swappiness=0")


def _idle_cgroup_cache_reclaim(after_gc):
    """Try freeing cold cgroup file cache only after idle GC and allocator trim.

    This is NOT an admission permit. The regular source launcher always
    resamples the authoritative raw memory.current before starting.
    No source API, PostgreSQL, DDL, process restart, or quota action occurs.
    Kernel support/permissions are not assumed; errors fail closed.
    """
    no = {"status": "not-eligible", "requested_mib": 0.0,
          "released_mib": 0.0, "error_code": ""}
    if os.getenv("G2B_IDLE_CGROUP_FILE_RECLAIM", "1").strip().lower() in {
        "0", "false", "no", "off"
    }:
        return {**no, "status": "disabled"}, after_gc
    import g2b_dynamic_source_budget as dynamic
    dynamic_active = bool(
        dynamic.enabled("budget")
        and str(after_gc.get("cgroup_source") or "") == "cgroup-v2"
        and float(after_gc.get("cgroup_limit_mib") or 0) == 256.0
        and os.getenv("G2B_RUNTIME_ROLE", "UNIFIED").upper() == "UNIFIED"
    )
    if not (dynamic_active or _source_start_25_active(after_gc)):
        return no, after_gc
    if after_gc.get("guard_ok") is not True or (
        after_gc.get("process_guard_ok") is not True
    ):
        return no, after_gc
    allowed, reason = safe_boot_vnext.child_admission(
        after_gc, worker_soft_limit_mib=_worker_forecast(),
        source_mode="shopping", source_start_25_enabled=not dynamic_active,
        dynamic_source_enabled=dynamic_active,
    )
    if allowed or reason != (
        "CGRP_DYNAMIC_START_HOLD" if dynamic_active
        else "CGRP_SOURCE_START_HEADROOM_UNSAFE"
    ):
        return no, after_gc

    # A different cgroup stat sample from a different tier must never
    # authorize memory.reclaim. Prefer a smaller file-only request to any
    # estimate based on the difference between raw and effective alone.
    try:
        profile = memory_guard.container_budget_snapshot()
        if (str(profile.get("source") or "") != "cgroup-v2"
                or float(profile.get("limit_mib") or 0) != 256.0):
            return no, after_gc
        raw = float(profile.get("current_mib") or 0)
        effective = float(profile.get("effective_mib") or 0)
        if not (math.isfinite(raw) and math.isfinite(effective)
                and raw > 0 and effective > 0 and raw <= 256.0):
            return no, after_gc
        cold_file = max(0, int(profile.get("inactive_file_bytes") or 0))
        file_bytes = max(0, int(profile.get("file_bytes") or 0))
        # Preserve the same 32MiB file-cache floor used by memory_guard.
        reclaimable = max(0, min(cold_file, file_bytes - 32 * (1 << 20)))
        if dynamic_active:
            # Request only enough cold file cache to satisfy exactly the
            # DECIMAL 220000000-byte cap plus measured provisional startup
            # peak. Never treat effective_mib as raw allocatable headroom.
            need_bytes = max(0,
                int(profile.get("current_bytes") or 0)
                + dynamic.START_RESERVE_BYTES
                - dynamic.SOURCE_CEILING_BYTES
                + 2 * (1 << 20)
            )
            desired_mib = need_bytes / float(1 << 20)
        else:
            desired_mib = max(
                0.0, raw - (256.0 - _worker_forecast() - 25.0) + 2.0
            )
        target_mib = min(
            CGROUP_FILE_RECLAIM_CAP_MIB,
            desired_mib,
            reclaimable / float(1 << 20),
        )
        # A partial dynamic reclaim may leave only a few MiB to admission.
        # Keep bounded small retries eligible instead of permanently discarding
        # a useful request below the legacy 8MiB minimum.
        minimum_mib = 1 if dynamic_active else CGROUP_FILE_RECLAIM_MIN_MIB
        if target_mib < minimum_mib:
            return no, after_gc
        request_bytes = int(target_mib * (1 << 20))
        error = ""
        try:
            _write_cgroup_file_reclaim(request_bytes)
        except OSError as exc:
            # EAGAIN can mean partial reclaim; readonly cgroups lack permission.
            error = type(exc).__name__
        after = dict(memory_guard.snapshot(collect=False))
        final_current = float(after.get("cgroup_current_mib") or 0)
        reclaimed_mib = max(0.0, raw - final_current) if final_current > 0 else 0.0
        result_status = "completed" if not error else "unavailable"
        if dynamic_active and not error:
            released = max(0,
                int(profile.get("current_bytes") or 0)
                - int(after.get("cgroup_current_bytes") or 0)
            )
            if released == 0:
                result_status = "no-effect"
            elif not dynamic.admission(after, worker_soft_limit_mib=_worker_forecast())[0]:
                result_status = "partial"
        return {
            "status": result_status,
            "requested_mib": round(target_mib, 2),
            "released_mib": round(reclaimed_mib, 2),
            "error_code": error,
        }, after
    except (OSError, TypeError, ValueError) as exc:
        return {**no, "status": "unavailable",
                "error_code": type(exc).__name__}, after_gc

def _pressure_reason(snapshot):
    """Detect cgroup pressure *or* an idle collector denied child headroom."""
    limit = float(snapshot.get("cgroup_limit_mib") or 0)
    current = float(snapshot.get("cgroup_current_mib") or 0)
    if limit <= 0:
        return ""
    threshold = (
        200.0 if 250 <= limit <= 320
        else 420.0 if 480 <= limit <= 560
        else min(limit * 0.82, max(0.0, limit - 48.0))
    )
    if current >= threshold > 0:
        return "cgroup-pressure"
    # Follow the same byte-exact dynamic flag as the real source launcher,
    # otherwise keep the established source-start/legacy policy unchanged.
    import g2b_dynamic_source_budget as dynamic
    dynamic_on = bool(
        dynamic.enabled("budget")
        and str(snapshot.get("cgroup_source") or "") == "cgroup-v2"
        and limit == 256.0
        and os.getenv("G2B_RUNTIME_ROLE", "UNIFIED").upper() == "UNIFIED"
    )
    start25 = not dynamic_on and _source_start_25_active(snapshot)
    allowed, reason = safe_boot_vnext.child_admission(
        snapshot, worker_soft_limit_mib=_worker_forecast(),
        source_mode="shopping", source_start_25_enabled=start25,
        dynamic_source_enabled=dynamic_on,
    )
    if dynamic_on and not allowed and reason == "CGRP_DYNAMIC_START_HOLD":
        return "dynamic-source-start-raw-headroom-held"
    if not allowed and reason == "CGRP_SOURCE_START_HEADROOM_UNSAFE":
        return "source-start-raw-headroom-held"
    if not allowed and reason == "CGRP_CHILD_HEADROOM_UNSAFE":
        return "child-headroom-held"
    if not allowed and reason == "MEMORY_GUARD_HELD":
        if snapshot.get("process_guard_ok") is False:
            return "web-rss-held"
        if str(snapshot.get("guard_state") or "") in {
            "PROCESS_RSS_HOLD", "CGROUP_WAIT", "CGROUP_BLOCK"
        }:
            return "web-memory-guard-held"
    return ""


def _worker_forecast():
    try:
        worker = int(str(
            os.getenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB", "112") or "112"
        ).strip())
    except (TypeError, ValueError):
        worker = 112
    return max(96, min(160, worker))


def run_recovery_cycle(*, workers_busy=None, backend_ready=None):
    """Best-effort cache-independent gc+trim on confirmed idle web only.

    No source-admission permission is returned or modified. A failed reclaim
    NEVER launches the worker or bypasses the actual cgroup budget forecast.
    """
    global _LAST_ATTEMPT, _LAST_RESULT, _ATTEMPTS
    if not enabled():
        return _skip("disabled")
    if not callable(workers_busy) or not callable(backend_ready):
        return _skip("worker-or-backend-guard-unavailable")
    try:
        if not backend_ready():
            return _skip("backend-not-ready")
        before = dict(memory_guard.snapshot(collect=False))
    except Exception as exc:
        return _skip("memory-probe-" + type(exc).__name__)
    limit = float(before.get("cgroup_limit_mib") or 0)
    if limit <= 0:
        return _skip("cgroup-limit-unavailable")
    now = time.monotonic()
    reason = _pressure_reason(before)
    high = bool(reason)
    idle_needed = PRESSURE_IDLE_SECONDS if high else NORMAL_IDLE_SECONDS
    regular_interval = periodic_seconds()
    with _LOCK:
        baseline = _LAST_ATTEMPT if _LAST_ATTEMPT > 0 else _START_MONOTONIC
        if not high and now - baseline < regular_interval:
            return _skip("periodic-not-due")
        if _ACTIVE_REQUESTS:
            return _skip("requests-active")
        if now - _LAST_ACTIVITY < idle_needed:
            return _skip("recent-visitor-activity")
        cooldown = PRESSURE_COOLDOWN_SECONDS if high else regular_interval
        if _LAST_ATTEMPT > 0 and now - _LAST_ATTEMPT < cooldown:
            return _skip("cooldown")
    try:
        if workers_busy():
            return _skip("worker-or-supervisor-active")
    except Exception:
        return _skip("worker-state-unavailable")
    with _LOCK:
        now = time.monotonic()
        if _ACTIVE_REQUESTS or now - _LAST_ACTIVITY < idle_needed:
            return _skip("visitor-arrived")
        baseline = _LAST_ATTEMPT if _LAST_ATTEMPT > 0 else _START_MONOTONIC
        if not high and now - baseline < regular_interval:
            return _skip("periodic-not-due")
        if _LAST_ATTEMPT > 0 and now - _LAST_ATTEMPT < cooldown:
            return _skip("cooldown")
        # Recheck under the same activity lock before entering the short trim.
        try:
            if not backend_ready() or workers_busy():
                return _skip("worker-or-backend-became-active")
        except Exception:
            return _skip("worker-state-unavailable")
        _LAST_ATTEMPT = now
        _ATTEMPTS += 1
        try:
            collected = int(gc.collect())
            error = ""
        except Exception as exc:
            collected = 0
            error = type(exc).__name__
        trimmed = _trim_allocator()
        try:
            after = dict(memory_guard.snapshot(collect=False))
        except Exception:
            after = {}
        cache_reclaim = {"status": "not-eligible", "requested_mib": 0.0,
                         "released_mib": 0.0, "error_code": ""}
        if high and after:
            cache_reclaim, after = _idle_cgroup_cache_reclaim(after)
        before_effective = float(before.get("cgroup_effective_mib") or 0)
        after_effective = float(after.get("cgroup_effective_mib") or 0)
        before_rss = float(before.get("rss_mib") or 0)
        after_rss = float(after.get("rss_mib") or 0)
        _LAST_RESULT = {
            "status": "completed" if not error else "partial",
            "mode": "pressure" if high else "periodic-idle",
            "reason": reason or "scheduled-idle",
            "before_effective_mib": round(before_effective, 2),
            "after_effective_mib": round(after_effective, 2),
            "effective_reclaimed_mib": round(max(
                0.0, before_effective - after_effective
            ), 2) if after else 0.0,
            "web_rss_reclaimed_mib": round(max(
                0.0, before_rss - after_rss
            ), 2) if after else 0.0,
            "gc_objects": collected,
            "malloc_trim_called": trimmed,
            "cgroup_file_reclaim": cache_reclaim,
            "raw_cgroup_reclaimed_mib": round(max(
                0.0, float(before.get("cgroup_current_mib") or 0.0)
                - float(after.get("cgroup_current_mib") or 0.0)
            ), 2) if after else 0.0,
            "error_code": error,
        }
        _record_check(_LAST_RESULT)
        return dict(_LAST_RESULT)


async def monitor_idle_memory_recovery(*, run_cycle):
    """Minute eligibility checks; GC keeps its idle, worker and cooldown guards.

    The Web's guarded callback must acquire the same isolated-child launch
    mutex as worker startup before checking idle state and calling malloc_trim.
    The 48–72 hour normal GC schedule and one-hour pressure attempt cooldown
    are unchanged. A skipped check never counts as a reclaim attempt.
    """
    global _NEXT_CHECK_MONOTONIC
    if not callable(run_cycle):
        raise ValueError("SAFE_WORKER_LAUNCH_BARRIER_REQUIRED")
    try:
        while True:
            _NEXT_CHECK_MONOTONIC = time.monotonic() + CHECK_INTERVAL_SECONDS
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            _NEXT_CHECK_MONOTONIC = 0.0
            try:
                await asyncio.to_thread(run_cycle)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _skip("cycle-" + type(exc).__name__)
                # Avoid revealing memory paths, credentials or env in web logs.
                print("G2B_IDLE_MEMORY_RECOVERY_DEGRADED", type(exc).__name__, flush=True)
    finally:
        _NEXT_CHECK_MONOTONIC = 0.0
