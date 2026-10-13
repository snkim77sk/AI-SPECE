"""Disposable CI ONLY: emulate a 113.3MiB Cafe24 cgroup baseline.

Keep anonymous resident bytes in the SAME real memory.max=256MiB container as
the G2B web and one synthetic source worker. No SQL, DDL or external HTTP.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

EXPECTED_CI_DATABASE = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"
TARGET_MIB = 113.3
READY = Path("/tmp/g2b-4294-baseline-ready.json")
MIB = 1024 * 1024


def authorized():
    return (
        os.environ.get("G2B_CI_BASELINE_BALLAST") == "1"
        and os.environ.get("G2B_DATABASE_URL") == EXPECTED_CI_DATABASE
        and os.environ.get("G2B_RUNTIME_ROLE") == "UNIFIED"
        and os.environ.get("G2B_TEST_MODE") == "0"
        and os.environ.get("G2B_AUTO_SYNC_DISABLE") == "1"
        and os.environ.get("G2B_APP_SCHEMA") == "g2b_app_4292_ci"
        and os.environ.get("G2B_BUDGET_SCHEMA") == "g2b_budget_4292_ci"
        and not any(os.environ.get(name) for name in (
            "G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"
        ))
    )


def main():
    if not authorized():
        print("G2B_4294_BASELINE_AUTH_HOLD", flush=True)
        return 2
    import memory_guard

    chunks = []
    start = memory_guard.container_budget_snapshot()
    if (
        start.get("source") != "cgroup-v2"
        or start.get("limit_mib") != 256.0
        or float(start.get("effective_mib") or 0) >= TARGET_MIB + 2.0
    ):
        print("G2B_4294_BASELINE_CGROUP_UNSAFE", flush=True)
        return 1

    snapshot = start
    for _ in range(100):
        delta = TARGET_MIB - float(snapshot["effective_mib"])
        if delta <= 0.65:
            break
        size = int(max(0.125, min(4.0, delta * 0.78)) * MIB)
        # bytearray touches resident anonymous pages; unlike OS file cache
        # this cannot be reclaimed without terminating the test-only helper.
        chunks.append(bytearray(size))
        snapshot = memory_guard.container_budget_snapshot()
        if snapshot["blocked"] or snapshot["effective_mib"] > TARGET_MIB + 2.0:
            print("G2B_4294_BASELINE_OVERSHOT", flush=True)
            return 1

    time.sleep(0.25)
    snapshot = memory_guard.container_budget_snapshot()
    effective = float(snapshot.get("effective_mib") or 0)
    if not TARGET_MIB - 2.0 <= effective <= TARGET_MIB + 2.0:
        print("G2B_4294_BASELINE_NOT_REPRODUCED", round(effective, 2), flush=True)
        return 1

    report = {
        "ci_only": True,
        "target_effective_mib": TARGET_MIB,
        "baseline_effective_mib": round(effective, 2),
        "baseline_cgroup_current_mib": round(
            float(snapshot.get("current_mib") or 0), 2
        ),
        "limit_mib": snapshot["limit_mib"],
        "ballast_chunks": len(chunks),
        "external_source_api_calls": 0,
        "database_access": False,
    }
    READY.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    print("G2B_4294_CAFE24_BASELINE_REPRODUCED",
          json.dumps(report, sort_keys=True), flush=True)
    # Stay alive until the disposable Docker job terminates this process.
    while True:
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
