"""Read-only source-free post-boot automatic collection memory admission check.

A successful GitHub /live, /ready, and 70-second CI liveness test does NOT show
that the production 256MiB UNIFIED container can spawn its 112MiB collector.
Run against the actual warmed web cgroup AFTER the 300s web-first grace.
The check makes no PostgreSQL connection, no data modifications, no source API
requests, and cannot start collection. It never relaxes the memory guard.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app_version import APP_VERSION
import memory_guard
import safe_boot_vnext
from runtime_role import UNIFIED, automatic_collection_enabled, runtime_role


def _flag(name):
    return str(os.getenv(name, "0") or "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _worker_soft_limit():
    try:
        requested = int(str(
            os.getenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB", "112") or "112"
        ).strip())
    except (TypeError, ValueError):
        requested = 112
    return max(96, min(160, requested))


def assess_postboot_admission(
    memory,
    *,
    role,
    auto_sync_enabled,
    test_mode=False,
    web_grace_operator_confirmed=False,
    worker_soft_limit_mib=112,
):
    """Evaluate exactly the existing collector admission guard without side effects.

    For a small or unknown cgroup the production UNIFIED code uses an isolated
    child; for larger cgroups it uses the in-process heavy-work guard.
    """
    state = dict(memory or {})
    limit = float(state.get("cgroup_limit_mib") or 0)
    effective = float(state.get("cgroup_effective_mib") or 0)
    worker = max(96, min(160, int(worker_soft_limit_mib)))
    reserve = float(safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB)
    small = 0 < limit <= float(safe_boot_vnext.LOW_MEMORY_LIMIT_MIB)
    isolated = limit <= float(safe_boot_vnext.LOW_MEMORY_LIMIT_MIB)
    isolated_mode = "ISOLATED" if isolated else "IN_PROCESS"
    if isolated:
        admitted, reason = safe_boot_vnext.child_admission(
            state, worker_soft_limit_mib=worker
        )
    else:
        admitted = bool(state.get("heavy_work_ok", state.get("guard_ok", False)))
        if int(state.get("cgroup_oom_group") or 0) == 1:
            admitted = False
        reason = (
            "LARGE_CGROUP_HEAVY_WORK_OK"
            if admitted else "LARGE_CGROUP_MEMORY_HOLD"
        )
    correct_runtime = (
        str(role).upper() == UNIFIED and not bool(test_mode)
        and bool(auto_sync_enabled)
    )
    confirmed = bool(web_grace_operator_confirmed)
    ready = bool(correct_runtime and confirmed and admitted)
    actions = []
    if str(role).upper() != UNIFIED:
        actions.append("REQUIRE_PRODUCTION_UNIFIED_RUNTIME")
    if test_mode:
        actions.append("DISABLE_TEST_MODE")
    if not auto_sync_enabled:
        actions.append("VERIFY_AUTOMATIC_COLLECTION_ENABLEMENT")
    if not confirmed:
        actions.append("CONFIRM_WEB_BOOT_300_SECOND_GRACE_ELAPSED")
    if not admitted:
        actions.append("AUTO_COLLECTION_WORKER_MEMORY_ADMISSION_HOLD")
    if small and reason == "CGRP_CHILD_HEADROOM_UNSAFE":
        actions.append("VERIFY_CAFE24_256MIB_WORKER_CAPACITY_WITHOUT_LOWERING_GUARD")
    # The cgroup's effective memory can differ from parent-process RSS. The
    # maximum is a strict boundary because child_admission uses >= to HOLD.
    max_effective = round(limit - worker - reserve, 2) if small else None
    return {
        "gate": "G2B_V41_POSTBOOT_AUTO_COLLECTION_ADMISSION",
        "version": APP_VERSION,
        "source_api_calls": 0,
        "postgres_connections_opened": 0,
        "database_rows_modified": 0,
        "schema_ddl_performed": False,
        "web_grace_elapsed_operator_confirmed": confirmed,
        "web_grace_elapsed_machine_verified": False,
        "runtime_role": str(role).upper(),
        "test_mode_enabled": bool(test_mode),
        "auto_sync_enabled": bool(auto_sync_enabled),
        "collection_execution_mode": isolated_mode,
        "memory_guard_ok": bool(state.get("guard_ok", False)),
        "observed_web_rss_mib": float(state.get("rss_mib") or 0),
        "observed_cgroup_limit_mib": limit,
        "observed_cgroup_effective_mib": effective,
        "worker_soft_limit_mib": worker if isolated else None,
        "worker_headroom_reserve_mib": reserve if isolated else None,
        "max_prelaunch_effective_mib_exclusive": max_effective,
        "current_admission_allowed": bool(admitted),
        "current_admission_reason": str(reason),
        "automatic_collection_deployment_ready": ready,
        "required_actions": actions,
    }


def inspect_postboot(*, web_grace_operator_confirmed=False):
    role = runtime_role()
    test_mode = _flag("G2B_TEST_MODE")
    auto = automatic_collection_enabled(test_mode=test_mode)
    try:
        state = memory_guard.snapshot(collect=False)
    except Exception as exc:
        # Keep error output sanitized: never echo URLs, source keys or secrets.
        state = {
            "guard_ok": False,
            "heavy_work_ok": False,
            "cgroup_limit_mib": 0,
            "cgroup_effective_mib": 0,
        }
        report = assess_postboot_admission(
            state, role=role, auto_sync_enabled=auto,
            test_mode=test_mode,
            web_grace_operator_confirmed=web_grace_operator_confirmed,
            worker_soft_limit_mib=_worker_soft_limit(),
        )
        report["memory_probe_error_code"] = type(exc).__name__
        report["required_actions"].append("FIX_READ_ONLY_CGROUP_MEMORY_PROBE")
        return report
    report = assess_postboot_admission(
        state, role=role, auto_sync_enabled=auto,
        test_mode=test_mode,
        web_grace_operator_confirmed=web_grace_operator_confirmed,
        worker_soft_limit_mib=_worker_soft_limit(),
    )
    report["memory_probe_error_code"] = ""
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only warm-web source worker headroom gate"
    )
    parser.add_argument(
        "--web-grace-elapsed",
        action="store_true",
        help="Operator attests the 300-second web-first boot grace already elapsed",
    )
    args = parser.parse_args(argv)
    result = inspect_postboot(
        web_grace_operator_confirmed=args.web_grace_elapsed
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["automatic_collection_deployment_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
