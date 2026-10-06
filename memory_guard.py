"""Cgroup-aware memory guard for G2B Cafe24 workers.

The original 4.1.141.1 guard blocks new heavy work when this Python process RSS
reaches its soft limit. This module keeps that conservative process guard and adds
an RSK-style container-wide cgroup pressure guard, so memory used by sibling
processes/kernel/shmem cannot push the 256 MiB container into OOM unnoticed.
"""
from __future__ import annotations

import gc
import os
import resource
from pathlib import Path

MIB = 1024 * 1024

DEFAULT_SOFT_LIMIT_MIB = 160
MIN_SOFT_LIMIT_MIB = 96
MAX_SOFT_LIMIT_MIB = 4096

# G2B is materially heavier than the RSK catalogue runtime. In a 256 MiB
# UNIFIED/RESULT_SERVER web container, a single DB/API/read-model phase can jump
# tens of MiB before the next cooperative guard check. Until heavy work is moved
# to a separate worker or every phase is batch-checkpointed, fail closed for heavy
# work in small web cgroups so the HTTP process survives.
LOW_MEMORY_WEB_LIMIT_MIB = 320

MIN_RECLAIMABLE_FILE_FLOOR = 32 * MIB
HEAVY_WAIT_RESERVE_MIN = 48 * MIB
HEAVY_WAIT_RESERVE_RATIO = 0.15
HEAVY_BLOCK_RESERVE_MIN = 32 * MIB
HEAVY_BLOCK_RESERVE_RATIO = 0.10


def _env_int(name, default, *, lower, upper):
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(lower), min(int(upper), value))


def _runtime_role():
    return str(os.getenv("G2B_RUNTIME_ROLE", "UNIFIED") or "UNIFIED").strip().upper()


def soft_limit_mib():
    return _env_int(
        "G2B_MEMORY_SOFT_LIMIT_MB",
        DEFAULT_SOFT_LIMIT_MIB,
        lower=MIN_SOFT_LIMIT_MIB,
        upper=MAX_SOFT_LIMIT_MIB,
    )


def current_rss_mib():
    """Return current process RSS in MiB when available."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    kib = int(line.split()[1])
                    return round(kib / 1024.0, 2)
    except (OSError, ValueError, IndexError):
        pass

    try:
        value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if value > 1024 * 1024:
            return round(value / (1024.0 * 1024.0), 2)
        return round(value / 1024.0, 2)
    except Exception:
        return 0.0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_int(path: Path):
    value = _read_text(path)
    if not value or value == "max":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or parsed >= (1 << 60):
        return None
    return parsed


def _memory_stat(path: Path):
    values = {}
    for line in _read_text(path).splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            values[parts[0]] = max(0, int(parts[1]))
        except ValueError:
            continue
    return values


def _cgroup_values():
    root = Path("/sys/fs/cgroup")
    current = _read_int(root / "memory.current")
    limit = _read_int(root / "memory.max")
    peak = _read_int(root / "memory.peak")
    stat = _memory_stat(root / "memory.stat")
    events = _memory_stat(root / "memory.events")
    if current is not None:
        return "cgroup-v2", current, limit, peak, stat, events

    base = root / "memory"
    current = _read_int(base / "memory.usage_in_bytes")
    limit = _read_int(base / "memory.limit_in_bytes")
    peak = _read_int(base / "memory.max_usage_in_bytes")
    stat = _memory_stat(base / "memory.stat")
    failcnt = _read_int(base / "memory.failcnt") or 0
    events = {"failcnt": int(failcnt)}
    return (
        "cgroup-v1" if current is not None else "",
        current,
        limit,
        peak,
        stat,
        events,
    )


def _mib(value):
    return round(float(value or 0) / MIB, 2)


def container_budget_snapshot():
    source, current, limit, peak, stat, events = _cgroup_values()

    anon = int(stat.get("anon") or stat.get("total_rss") or 0)
    file_bytes = int(stat.get("file") or stat.get("total_cache") or 0)
    shmem = int(stat.get("shmem") or stat.get("total_shmem") or 0)
    kernel_total = stat.get("kernel")
    kernel = int(kernel_total or stat.get("total_kernel_stack") or 0)
    slab = int(
        stat.get("slab")
        or (
            int(stat.get("total_slab_reclaimable") or 0)
            + int(stat.get("total_slab_unreclaimable") or 0)
        )
    )

    if limit:
        file_floor = max(MIN_RECLAIMABLE_FILE_FLOOR, int(limit * 0.08))
        wait_reserve = max(
            HEAVY_WAIT_RESERVE_MIN,
            int(limit * HEAVY_WAIT_RESERVE_RATIO),
        )
        block_reserve = max(
            HEAVY_BLOCK_RESERVE_MIN,
            int(limit * HEAVY_BLOCK_RESERVE_RATIO),
        )
        wait_threshold = max(0, limit - wait_reserve)
        block_threshold = max(wait_threshold, limit - block_reserve)
    else:
        file_floor = MIN_RECLAIMABLE_FILE_FLOOR
        wait_reserve = 0
        block_reserve = 0
        wait_threshold = 0
        block_threshold = 0

    kernel_pressure = kernel if kernel_total is not None else kernel + slab
    has_detailed_stat = bool(anon or file_bytes or shmem or kernel_pressure)
    if has_detailed_stat:
        effective = (
            anon
            + shmem
            + kernel_pressure
            + min(file_bytes, file_floor)
        )
    else:
        # If a platform exposes current/limit but hides memory.stat, use the
        # conservative total instead of silently failing open.
        effective = int(current or 0)

    wait_ok = bool(
        not limit
        or int(effective or 0) < int(wait_threshold or 0)
    )
    blocked = bool(
        limit
        and int(effective or 0) >= int(block_threshold or 0)
    )

    return {
        "source": source,
        "limit_bytes": int(limit or 0),
        "current_bytes": int(current or 0),
        "peak_bytes": int(peak or 0),
        "effective_bytes": int(effective or 0),
        "wait_threshold_bytes": int(wait_threshold or 0),
        "block_threshold_bytes": int(block_threshold or 0),
        "anon_bytes": anon,
        "file_bytes": file_bytes,
        "shmem_bytes": shmem,
        "kernel_pressure_bytes": kernel_pressure,
        "limit_mib": _mib(limit),
        "current_mib": _mib(current),
        "peak_mib": _mib(peak),
        "effective_mib": _mib(effective),
        "wait_threshold_mib": _mib(wait_threshold),
        "block_threshold_mib": _mib(block_threshold),
        "wait_ok": wait_ok,
        "blocked": blocked,
        "events": {
            "low": int(events.get("low", 0)),
            "high": int(events.get("high", 0)),
            "max": int(events.get("max", 0)),
            "oom": int(events.get("oom", 0)),
            "oom_kill": int(events.get("oom_kill", 0)),
            "failcnt": int(events.get("failcnt", 0)),
        },
    }


def low_memory_web_hold(container=None):
    """Return True when heavy work must stay out of this small web cgroup."""
    role = _runtime_role()
    if role not in {"UNIFIED", "RESULT_SERVER"}:
        return False
    state = container if container is not None else container_budget_snapshot()
    limit_mib = float(state.get("limit_mib") or 0.0)
    return bool(0 < limit_mib <= float(LOW_MEMORY_WEB_LIMIT_MIB))


def snapshot(*, collect=False):
    if collect:
        gc.collect()

    rss = float(current_rss_mib() or 0.0)
    process_limit = int(soft_limit_mib())
    process_ok = bool(rss <= 0 or rss < process_limit)
    container = container_budget_snapshot()
    small_web_hold = low_memory_web_hold(container)

    if not process_ok:
        guard_state = "PROCESS_RSS_HOLD"
    elif container["blocked"]:
        guard_state = "CGROUP_BLOCK"
    elif not container["wait_ok"]:
        guard_state = "CGROUP_WAIT"
    else:
        guard_state = "SAFE"

    return {
        "rss_mib": round(rss, 2),
        "soft_limit_mib": process_limit,
        "process_guard_ok": process_ok,
        # guard_ok reports instantaneous pressure. heavy_work_ok adds the
        # emergency web-tier admission rule so /health can stay healthy while
        # memory-heavy jobs are deliberately held on a 256 MiB web container.
        "guard_ok": bool(process_ok and container["wait_ok"]),
        "heavy_work_ok": bool(
            process_ok and container["wait_ok"] and not small_web_hold
        ),
        "low_memory_web_hold": bool(small_web_hold),
        "guard_state": guard_state,
        "cgroup_source": container["source"],
        "cgroup_limit_mib": container["limit_mib"],
        "cgroup_current_mib": container["current_mib"],
        "cgroup_peak_mib": container["peak_mib"],
        "cgroup_effective_mib": container["effective_mib"],
        "cgroup_wait_threshold_mib": container["wait_threshold_mib"],
        "cgroup_block_threshold_mib": container["block_threshold_mib"],
        "cgroup_blocked": container["blocked"],
        "cgroup_events": dict(container["events"]),
    }


def heavy_work_allowed(*, collect=True):
    state = snapshot(collect=collect)
    return bool(state.get("heavy_work_ok", state["guard_ok"]))


def apply_default_process_tuning():
    """Fill blank native allocator/thread settings without overriding operators."""
    defaults = {
        "MALLOC_ARENA_MAX": "2",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    for name, value in defaults.items():
        if not str(os.environ.get(name) or "").strip():
            os.environ[name] = value
    return {name: str(os.environ.get(name) or "") for name in defaults}


__all__ = [
    "DEFAULT_SOFT_LIMIT_MIB",
    "MIN_SOFT_LIMIT_MIB",
    "MAX_SOFT_LIMIT_MIB",
    "LOW_MEMORY_WEB_LIMIT_MIB",
    "soft_limit_mib",
    "current_rss_mib",
    "container_budget_snapshot",
    "low_memory_web_hold",
    "snapshot",
    "heavy_work_allowed",
    "apply_default_process_tuning",
]
