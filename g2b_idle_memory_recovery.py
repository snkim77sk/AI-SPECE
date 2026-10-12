"""Idle-only, bounded G2B memory reclamation inspired by SINSUNG.

Never kills/restarts the web or any worker. Never touches PostgreSQL, source
APIs, checkpoint/receipt files, or live collector admission thresholds.
An idle web can ask glibc to return previously freed heap pages to the kernel,
but memory reclamation is best-effort and may reclaim zero bytes.
"""
from __future__ import annotations

import asyncio
import gc
import os
import threading
import time

import memory_guard
import safe_boot_vnext

CHECK_INTERVAL_SECONDS = 15 * 60.0
DEFAULT_PERIODIC_HOURS = 72
MIN_PERIODIC_HOURS = 48
MAX_PERIODIC_HOURS = 72
PRESSURE_IDLE_SECONDS = 45.0
NORMAL_IDLE_SECONDS = 180.0
PRESSURE_COOLDOWN_SECONDS = 60 * 60.0
_START_MONOTONIC = time.monotonic()
_LOCK = threading.RLock()
_ACTIVE_REQUESTS = 0
_LAST_ACTIVITY = time.monotonic()
_LAST_ATTEMPT = 0.0
_ATTEMPTS = 0
_SKIPS = 0
_LAST_RESULT = {"status": "waiting", "reason": "startup"}

# Exclude only cheap, read-only health/status refreshes from the idle clock.
# Mutating controls, export, heavy pages and other visits remain counted.
_PASSIVE_PATHS = frozenset({
    "/live", "/health", "/ready", "/__ai_space_health",
    "/collection-monitor", "/api/collection-status",
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


def mark_request_end():
    global _ACTIVE_REQUESTS, _LAST_ACTIVITY
    with _LOCK:
        _ACTIVE_REQUESTS = max(0, _ACTIVE_REQUESTS - 1)
        _LAST_ACTIVITY = time.monotonic()


class ActivityMiddleware:
    """Account for user requests through complete streaming response/exception."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") in _PASSIVE_PATHS:
            return await self.app(scope, receive, send)
        mark_request_start()
        try:
            return await self.app(scope, receive, send)
        finally:
            mark_request_end()


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


def _skip(reason):
    global _SKIPS
    with _LOCK:
        _SKIPS += 1
    return {"status": "skipped", "reason": str(reason)}


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
            "normal_periodic_hours": periodic_seconds() / 3600.0,
            "normal_periodic_due_seconds": round(max(
                0.0, periodic_seconds() - (time.monotonic() - reference)
            ), 1),
            "last_result": dict(_LAST_RESULT),
            "does_not_restart_web": True,
            "persistent_data_untouched": True,
        }


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
    # The SAME 112+64 policy used by the real child gate: we never relax it.
    allowed, reason = safe_boot_vnext.child_admission(
        snapshot, worker_soft_limit_mib=_worker_forecast()
    )
    if not allowed and reason == "CGRP_CHILD_HEADROOM_UNSAFE":
        return "child-headroom-held"
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
            "error_code": error,
        }
        return dict(_LAST_RESULT)


async def monitor_idle_memory_recovery(*, workers_busy, backend_ready):
    """15-minute low-work monitor; cancellable with FastAPI lifespan."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            await asyncio.to_thread(
                run_recovery_cycle,
                workers_busy=workers_busy,
                backend_ready=backend_ready,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Avoid revealing memory paths, credentials or env in web logs.
            print("G2B_IDLE_MEMORY_RECOVERY_DEGRADED", type(exc).__name__, flush=True)
