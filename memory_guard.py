"""Low-overhead process memory guard for G2B web workers.

The guard is advisory: it prevents new memory-heavy background work from starting
when the web process is already under pressure. It never exits the process and
never mutates application data.
"""
from __future__ import annotations

import gc
import os
import resource

DEFAULT_SOFT_LIMIT_MIB = 160
MIN_SOFT_LIMIT_MIB = 96
MAX_SOFT_LIMIT_MIB = 4096


def _env_int(name, default, *, lower, upper):
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(lower), min(int(upper), value))


def soft_limit_mib():
    return _env_int(
        "G2B_MEMORY_SOFT_LIMIT_MB",
        DEFAULT_SOFT_LIMIT_MIB,
        lower=MIN_SOFT_LIMIT_MIB,
        upper=MAX_SOFT_LIMIT_MIB,
    )


def current_rss_mib():
    """Return current process RSS in MiB when available.

    Linux /proc is preferred because ru_maxrss is a peak value and cannot show
    memory recovery after GC. The resource fallback is still useful on other
    Unix-like hosts.
    """
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


def snapshot(*, collect=False):
    if collect:
        gc.collect()
    rss = float(current_rss_mib() or 0.0)
    limit = int(soft_limit_mib())
    return {
        "rss_mib": round(rss, 2),
        "soft_limit_mib": limit,
        "guard_ok": bool(rss <= 0 or rss < limit),
    }


def heavy_work_allowed(*, collect=True):
    return bool(snapshot(collect=collect)["guard_ok"])


__all__ = [
    "DEFAULT_SOFT_LIMIT_MIB",
    "MIN_SOFT_LIMIT_MIB",
    "MAX_SOFT_LIMIT_MIB",
    "soft_limit_mib",
    "current_rss_mib",
    "snapshot",
    "heavy_work_allowed",
]
