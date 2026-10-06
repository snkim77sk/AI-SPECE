"""Adaptive memory safety for the G2B Cafe24 web process.

This module combines a process-RSS soft cap with a cgroup-aware container budget.
The same code automatically adapts when the hosting plan moves from 256 MiB to
512 MiB while preserving an explicit numeric operator override.

The guard never exits the web process and never mutates application data. Heavy
work is admitted only while both the process and the container have headroom.
"""
from __future__ import annotations

import gc
import os
import resource
import time
from pathlib import Path

MIB = 1024 * 1024

DEFAULT_SOFT_LIMIT_MIB = 160
MIN_SOFT_LIMIT_MIB = 96
MAX_SOFT_LIMIT_MIB = 4096
AUTO_PROCESS_SOFT_RATIO = 0.625

MIN_RECLAIMABLE_FILE_FLOOR = 32 * MIB
HEAVY_WAIT_RESERVE_MIN = 48 * MIB
HEAVY_WAIT_RESERVE_RATIO = 0.15
HEAVY_BLOCK_RESERVE_MIN = 32 * MIB
HEAVY_BLOCK_RESERVE_RATIO = 0.10
HEAVY_WAIT_SECONDS = 30.0


class MemoryPressureError(TimeoutError):
    """Raised before expensive work when safe headroom is unavailable."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_int(path: Path) -> int | None:
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


def _memory_stat(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
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
    if current is not None:
        return (
            "cgroup-v2",
            current,
            limit,
            _memory_stat(root / "memory.stat"),
            _memory_stat(root / "memory.events"),
            0,
        )

    base = root / "memory"
    current = _read_int(base / "memory.usage_in_bytes")
    limit = _read_int(base / "memory.limit_in_bytes")
    return (
        "cgroup-v1" if current is not None else "",
        current,
        limit,
        _memory_stat(base / "memory.stat"),
        {},
        _read_int(base / "memory.failcnt") or 0,
    )


def _mb(value) -> float:
    return round(float(value or 0) / MIB, 2)


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
        # Linux reports KiB. macOS reports bytes.
        if value > 1024 * 1024:
            return round(value / (1024.0 * 1024.0), 2)
        return round(value / 1024.0, 2)
    except Exception:
        return 0.0


def memory_budget_snapshot():
    """Return cgroup pressure with reclaimable file cache discounted."""
    source, current, limit, stat, events, failcnt = _cgroup_values()
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
    effective = anon + shmem + kernel_pressure + min(file_bytes, file_floor)

    return {
        "source": source,
        "limit_bytes": int(limit or 0),
        "current_bytes": int(current or 0),
        "anon_bytes": anon,
        "file_bytes": file_bytes,
        "shmem_bytes": shmem,
        "kernel_pressure_bytes": kernel_pressure,
        "effective_bytes": effective,
        "wait_threshold_bytes": wait_threshold,
        "block_threshold_bytes": block_threshold,
        "wait_reserve_bytes": wait_reserve,
        "block_reserve_bytes": block_reserve,
        "limit_mib": _mb(limit),
        "current_mib": _mb(current),
        "effective_mib": _mb(effective),
        "wait_threshold_mib": _mb(wait_threshold),
        "block_threshold_mib": _mb(block_threshold),
        "wait_reserve_mib": _mb(wait_reserve),
        "block_reserve_mib": _mb(block_reserve),
        "events": {
            "low": int(events.get("low", 0)),
            "high": int(events.get("high", 0)),
            "max": int(events.get("max", 0)),
            "oom": int(events.get("oom", 0)),
            "oom_kill": int(events.get("oom_kill", 0)),
            "failcnt": int(failcnt),
        },
    }


def _explicit_soft_limit_mib():
    raw = str(os.getenv("G2B_MEMORY_SOFT_LIMIT_MB", "") or "").strip().lower()
    if not raw or raw in {"auto", "adaptive"}:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(MIN_SOFT_LIMIT_MIB, min(MAX_SOFT_LIMIT_MIB, value))


def soft_limit_mib(*, limit_bytes=None):
    """Return process RSS soft cap.

    Numeric G2B_MEMORY_SOFT_LIMIT_MB always wins. Otherwise the cap scales with
    the detected cgroup hard limit: 256 MiB -> 160 MiB, 512 MiB -> 320 MiB.
    """
    explicit = _explicit_soft_limit_mib()
    if explicit is not None:
        return explicit

    if limit_bytes is None:
        _source, _current, limit_bytes, _stat, _events, _failcnt = _cgroup_values()
    if limit_bytes:
        limit_mib = float(limit_bytes) / MIB
        adaptive = int(limit_mib * AUTO_PROCESS_SOFT_RATIO)
        return max(
            MIN_SOFT_LIMIT_MIB,
            min(MAX_SOFT_LIMIT_MIB, adaptive),
        )
    return DEFAULT_SOFT_LIMIT_MIB


def snapshot(*, collect=False):
    if collect:
        gc.collect()

    rss = float(current_rss_mib() or 0.0)
    budget = memory_budget_snapshot()
    process_limit = int(
        soft_limit_mib(limit_bytes=int(budget.get("limit_bytes") or 0))
    )
    process_ok = bool(rss <= 0 or rss < process_limit)

    container_limit = int(budget.get("limit_bytes") or 0)
    effective = int(budget.get("effective_bytes") or 0)
    wait_threshold = int(budget.get("wait_threshold_bytes") or 0)
    block_threshold = int(budget.get("block_threshold_bytes") or 0)
    container_ok = bool(
        not container_limit
        or not wait_threshold
        or effective < wait_threshold
    )
    blocked = bool(
        (rss > 0 and rss >= process_limit)
        or (
            container_limit
            and block_threshold
            and effective >= block_threshold
        )
    )

    return {
        "rss_mib": round(rss, 2),
        "soft_limit_mib": process_limit,
        "guard_ok": bool(process_ok and container_ok),
        "blocked": blocked,
        **budget,
    }


def heavy_work_allowed(*, collect=True):
    return bool(snapshot(collect=collect)["guard_ok"])


def wait_for_heavy_work_budget(timeout: float = HEAVY_WAIT_SECONDS):
    """Wait briefly for reclaim/headroom before starting expensive work."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    first = True
    while True:
        state = snapshot(collect=first)
        first = False
        if state["guard_ok"]:
            return state
        if state["blocked"]:
            raise MemoryPressureError(
                "G2B memory guard blocked heavy work: "
                f"rss={state['rss_mib']:.1f}MiB "
                f"process_soft={state['soft_limit_mib']}MiB "
                f"effective={state['effective_mib']:.1f}MiB "
                f"container_block={state['block_threshold_mib']:.1f}MiB "
                f"container_limit={state['limit_mib']:.1f}MiB"
            )
        if time.monotonic() >= deadline:
            raise MemoryPressureError(
                "G2B memory guard timed out waiting for headroom: "
                f"rss={state['rss_mib']:.1f}MiB "
                f"effective={state['effective_mib']:.1f}MiB "
                f"container_wait={state['wait_threshold_mib']:.1f}MiB "
                f"container_limit={state['limit_mib']:.1f}MiB"
            )
        time.sleep(0.25)


def apply_default_process_tuning():
    """Fill blank allocator/native-thread settings; preserve explicit values."""
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
    "MemoryPressureError",
    "DEFAULT_SOFT_LIMIT_MIB",
    "MIN_SOFT_LIMIT_MIB",
    "MAX_SOFT_LIMIT_MIB",
    "soft_limit_mib",
    "current_rss_mib",
    "memory_budget_snapshot",
    "snapshot",
    "heavy_work_allowed",
    "wait_for_heavy_work_budget",
    "apply_default_process_tuning",
]
