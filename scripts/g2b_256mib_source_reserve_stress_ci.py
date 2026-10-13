"""G2B V4.1.297 source-free experimental 64MiB reserve guard stress.

Runs ONLY inside disposable CI Docker memory.max=256MiB with no real keys and
the explicit experimental guard flag. Allocates bounded anonymous bytes to
verify the real source-only cooperative guard stops BEFORE cgroup OOM. No PG,
SQL, HTTPS, source calls, source credentials, or production file changes.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIB = 1024 * 1024
EXPECTED_CI_DB = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"


def _authorized():
    return (
        os.getenv("G2B_DATABASE_URL") == EXPECTED_CI_DB
        and os.getenv("G2B_TEST_MODE") == "0"
        and os.getenv("G2B_RUNTIME_ROLE") == "UNIFIED"
        and os.getenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY") == "1"
        and os.getenv("G2B_AUTO_SYNC_DISABLE") == "1"
        and os.getenv("G2B_APP_SCHEMA") == "g2b_app_4292_ci"
        and os.getenv("G2B_BUDGET_SCHEMA") == "g2b_budget_4292_ci"
        and not any(os.getenv(k) for k in (
            "G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"
        ))
    )


def main():
    if not _authorized():
        print("G2B_4297_INFLIGHT_GUARD_CI_AUTH_HOLD", flush=True)
        return 2
    # Worker role only after strict external CI authorization. This avoids
    # importing any heavyweight collector while injecting anonymous stress.
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_SOURCE_ONLY_RUNTIME"] = "1"
    os.environ["G2B_ISOLATED_HEAVY_WORKER"] = "1"
    os.environ["G2B_MEMORY_SOFT_LIMIT_MB"] = "112"

    import memory_guard
    start = memory_guard.container_budget_snapshot()
    if (
        start.get("source") != "cgroup-v2"
        or float(start.get("limit_mib") or 0) != 256.0
        or float(start.get("effective_mib") or 0) >= 184.0
    ):
        print("G2B_4297_INFLIGHT_GUARD_UNSAFE_INITIAL_CGROUP", flush=True)
        return 1

    chunks = []
    guard_caught = ""
    observed = start
    for _ in range(110):
        observed = memory_guard.container_budget_snapshot()
        before = max(
            float(observed.get("effective_mib") or 0),
            float(observed.get("current_mib") or 0),
        )
        if before >= 191.5:
            # Small final allocation still crosses the actual 64MiB reserve
            # threshold; no arbitrary 20/40/100MiB jump is allowed.
            size_mib = 0.25
        else:
            size_mib = min(4.0, max(0.25, 191.75 - before))
        if before < 192.0:
            chunks.append(bytearray(int(size_mib * MIB)))
        try:
            memory_guard.cooperative_batch_checkpoint(timeout=0)
        except memory_guard.MemoryPressureError as exc:
            guard_caught = str(exc)
            break

    if guard_caught != "SOURCE_CGROUP_WEB_RESERVE_HOLD":
        print(
            "G2B_4297_INFLIGHT_GUARD_NOT_TRIGGERED",
            type(guard_caught).__name__, flush=True,
        )
        return 1
    observed = memory_guard.container_budget_snapshot()
    print(
        "G2B_4297_REAL_256MIB_INFLIGHT_64MIB_RESERVE_GUARD_OK",
        "current_mib", observed.get("current_mib"),
        "effective_mib", observed.get("effective_mib"),
        "rss_mib", memory_guard.current_rss_mib(),
        "limit_mib", observed.get("limit_mib"),
        "guard_reason", guard_caught,
        "allocations", len(chunks),
        "external_source_api_calls", 0,
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
