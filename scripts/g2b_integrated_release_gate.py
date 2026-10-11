"""Read-only cumulative G2B release cutover evidence gate (4.1.280).

The old 4.1.238 application and its children MUST already be stopped before
considering a cold deployment. This program never stops processes, merges PRs,
deploys Cafe24, initializes schemas, changes a source credential/checkpoint, or
calls G2B/LOFIN source APIs. It is NOT proof of remote PID exit, nor an
authorization to deploy. It composes the existing independent PostgreSQL
advisory-lock and checkpoint-ABI gates, checking the lock state on both sides
of the schema inspection. Development PR status is verified separately. A reviewed cumulative
main-target PR may integrate all source changes while historical stack PRs
remain Draft; never claim those individual PRs were merged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app_version import APP_VERSION
from runtime_identity import build_commit_info
from runtime_role import automatic_collection_enabled
from scripts import g2b_cold_cutover_guard as cold
from scripts import g2b_checkpoint_cutover_guard as checkpoint

_HEX_SHA = re.compile(r"[0-9a-f]{40}\Z")
_TRUE = frozenset({"1", "true", "yes", "on"})


def _enabled(name):
    return str(os.getenv(name, "0") or "0").strip().lower() in _TRUE


def _release_manifest_matches():
    """Read local metadata only; never open a PostgreSQL connection."""
    try:
        version_line = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        header = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, ValueError, IndexError, KeyError, TypeError):
        return False
    return bool(
        version_line == "SINSUNG G2B VNEXT " + APP_VERSION
        and project.get("project", {}).get("version") == APP_VERSION
        and header == "# SINSUNG G2B vNext " + APP_VERSION
    )


def _safe_evidence(report, label, details):
    """Include small booleans and counts, never DSN, SQL or source keys."""
    report[label] = {
        "read_only": details.get("read_only") is True,
        "schema_ddl_performed": details.get("schema_ddl_performed") is True,
        "business_rows_modified": int(details.get("business_rows_modified") or 0),
        "source_api_calls": int(details.get("source_api_calls") or 0),
    }
    if "pg_advisory_probe_ok" in details:
        report[label]["pg_advisory_probe_ok"] = details.get("pg_advisory_probe_ok") is True
        report[label]["busy_lock_count"] = len(details.get("pg_advisory_probe_busy") or ())
    if "checkpoint_tables_checked" in details:
        report[label]["checkpoint_tables_checked"] = int(
            details.get("checkpoint_tables_checked") or 0
        )
        report[label]["marker_ok"] = details.get("marker_ok") is True


def _safe_contract(details):
    """Only bools and zero counters prove a called helper was non-mutating."""
    return bool(
        details.get("read_only") is True
        and details.get("schema_ddl_performed") is False
        and details.get("business_rows_modified") == 0
        and details.get("source_api_calls") == 0
    )


def inspect_integrated_release(
    *, reviewed_main_head="", old_processes_stopped=False,
    merged_main_confirmed=False, ci_simulation=False,
):
    """Fail-closed cutover evidence only; never a deployment approval."""
    expected = str(reviewed_main_head or "").strip().lower()
    identity = build_commit_info()
    observed = str(identity.get("build_commit") or "").strip().lower()
    checkout = str(identity.get("git_checkout_commit") or "").strip().lower()
    report = {
        "gate": "G2B_4_1_280_INTEGRATED_READ_ONLY_COLD_CUTOVER",
        "version": APP_VERSION,
        "candidate_head": observed if _HEX_SHA.fullmatch(observed) else "",
        "reviewed_main_head": expected if _HEX_SHA.fullmatch(expected) else "",
        "candidate_head_source": str(identity.get("build_commit_source") or ""),
        "build_commit_authoritative": bool(identity.get("build_commit_authoritative")),
        "manifest_consistent": False,
        "operator_attested_old_processes_stopped": bool(old_processes_stopped),
        "old_process_exited_machine_verified": False,
        "operator_attested_main_merged_and_reviewed": bool(merged_main_confirmed),
        "separate_github_pr_review_verified": False,
        "read_only": True,
        "business_rows_modified": 0,
        "schema_ddl_performed": False,
        "source_api_calls": 0,
        "deployment_performed": False,
        "deployment_authorized": False,
        "ci_simulation": bool(ci_simulation),
        "stages_checked": [],
        "cutover_evidence_ready": False,
        "required_actions": [],
    }
    actions = report["required_actions"]
    report["manifest_consistent"] = _release_manifest_matches()
    if not report["manifest_consistent"]:
        actions.append("FIX_RELEASE_VERSION_MANIFEST")
    if not _HEX_SHA.fullmatch(expected):
        actions.append("SPECIFY_REVIEWED_40_CHARACTER_MAIN_SHA")
    if not _HEX_SHA.fullmatch(observed):
        actions.append("OBSERVE_AUTHORITATIVE_BUILD_HEAD")
    if not identity.get("build_commit_authoritative"):
        actions.append("VERIFY_DEPLOYABLE_CHECKOUT_OR_PLATFORM_SHA")
    if (
        _HEX_SHA.fullmatch(observed)
        and _HEX_SHA.fullmatch(expected)
        and observed != expected
    ):
        actions.append("CANDIDATE_HEAD_MISMATCH")
    if identity.get("build_commit_mismatch"):
        actions.append("STALE_MANUAL_BUILD_COMMIT")
    if checkout and _HEX_SHA.fullmatch(checkout) and observed and checkout != observed:
        actions.append("CHECKOUT_PLATFORM_HEAD_MISMATCH")
    manual = str(identity.get("configured_build_commit") or "").strip().lower()
    if manual and observed and manual != observed:
        actions.append("MANUAL_BUILD_COMMIT_MISMATCH")
    if _enabled("G2B_TEST_MODE"):
        actions.append("DISABLE_TEST_MODE")
    if str(os.getenv("G2B_RUNTIME_ROLE", "UNIFIED") or "").strip().upper() != "UNIFIED":
        actions.append("USE_UNIFIED_RUNTIME_ROLE")
    if _enabled("G2B_V41_FRESH_START") or _enabled("G2B_DESTRUCTIVE_RESET_CONFIRM"):
        actions.append("DISABLE_HISTORICAL_RESET_FLAGS")
    if not automatic_collection_enabled(test_mode=False):
        actions.append("ENABLE_UNIFIED_AUTO_COLLECTION")
    if not merged_main_confirmed:
        # Historical action name retained for existing 4.1.280 callers;
        # a reviewed single cumulative integration PR is also acceptable.
        actions.append("MERGE_AND_REVIEW_CUMULATIVE_DRAFT_PRS_FIRST")
    if not old_processes_stopped:
        actions.append("CONFIRM_OLD_WEB_AND_ALL_CHILD_PIDS_EXITED")

    # Do NOT even open a PostgreSQL session before release metadata and
    # operator prerequisites are consistent. An ordinary deployment
    # preflight calls db.init_db(), so it is *not* a substitute for this gate.
    if actions:
        return report

    try:
        before = dict(cold.inspect_cutover(operator_confirmed_old_stopped=True))
        report["stages_checked"].append("PG_ADVISORY_BEFORE")
        _safe_evidence(report, "pg_before", before)
        if not _safe_contract(before) or before.get("cutover_ready") is not True:
            actions.append("COLD_CUTOVER_PRECHECK_HOLD")
            return report

        schema = dict(checkpoint.inspect_checkpoint_cutover())
        report["stages_checked"].append("PG_CHECKPOINT_SCHEMA")
        _safe_evidence(report, "checkpoint", schema)
        if not _safe_contract(schema) or schema.get("schema_checkpoint_compatible") is not True:
            actions.append("CHECKPOINT_SCHEMA_OR_MARKER_HOLD")
            return report

        # Catch newly contended source leases after checking checkpoint ABI.
        # This does NOT prove the old Cafe24 process cannot restart later.
        after = dict(cold.inspect_cutover(operator_confirmed_old_stopped=True))
        report["stages_checked"].append("PG_ADVISORY_AFTER")
        _safe_evidence(report, "pg_after", after)
        if not _safe_contract(after) or after.get("cutover_ready") is not True:
            actions.append("COLD_CUTOVER_POSTCHECK_HOLD")
            return report

        report["cutover_evidence_ready"] = True
    except Exception as exc:
        # Do not disclose connection strings, source keys or server paths.
        actions.append("INTEGRATED_CUTOVER_CHECK_FAILED_" + type(exc).__name__)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only G2B integrated cutover evidence, not deployment authorization",
    )
    parser.add_argument("--reviewed-main-head", default="", help="Reviewed merged main SHA, exactly 40 hex chars")
    parser.add_argument("--old-processes-stopped", action="store_true",
                        help="Operator attests all old Cafe24 web and child PIDs exited")
    parser.add_argument("--merged-main-confirmed", action="store_true",
                        help="Operator attests the complete reviewed cumulative code is integrated in main")
    parser.add_argument("--ci-simulation", action="store_true",
                        help="Ephemeral CI rehearsal only, not Cafe24 evidence")
    args = parser.parse_args(argv)
    report = inspect_integrated_release(
        reviewed_main_head=args.reviewed_main_head,
        old_processes_stopped=args.old_processes_stopped,
        merged_main_confirmed=args.merged_main_confirmed,
        ci_simulation=args.ci_simulation,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["cutover_evidence_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
