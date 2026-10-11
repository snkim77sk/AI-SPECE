"""Read-only, source-free handoff gate for the 4.1.238 -> newer G2B rollout.

This does NOT shut down Cafe24 processes or prove remote PIDs are absent.
An operator must explicitly attest that every old web and heavy-worker process
has exited. The PostgreSQL advisory check then rejects visible active v4.1
collector locks. No schema initialization, DDL, API access or credential writes.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import g2b_database

LOCK_NAMES = (
    "g2b_v41_operational_cycle",
    "g2b_v41_manual_shopping",
    "g2b_v41_manual_budget",
    "g2b_v41_match_rollover",
)
_TRUE = frozenset({"1", "true", "yes", "on"})


def _enabled(name):
    return str(os.getenv(name, "0") or "0").strip().lower() in _TRUE


def _probe_advisory_locks():
    """Acquire/release disposable, autocommit advisory locks without DML.

    An exclusive probe on the shared/exclusive global name conflicts with
    both legacy 4.1.238 and current code. Probe the two independent source
    names as well. A no-lock result alone cannot prove that a legacy process
    has exited after losing its idle-in-transaction PostgreSQL connection.
    """
    busy = []
    acquired = []
    with closing(g2b_database._operational_lease_connection()) as conn:
        try:
            for name in LOCK_NAMES:
                row = conn.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s))", (name,)
                ).fetchone()
                if bool(row and row[0]):
                    acquired.append(name)
                else:
                    busy.append(name)
        finally:
            for name in reversed(acquired):
                try:
                    conn.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))", (name,)
                    ).fetchone()
                except Exception:
                    # Physical close is authoritative for disposable PG
                    # advisory sessions, even when SQL unlock fails.
                    pass
    return busy


def inspect_cutover(*, operator_confirmed_old_stopped=False):
    """Fail closed; never represent the operator assertion as measured PID proof."""
    test_mode = _enabled("G2B_TEST_MODE")
    role = str(os.getenv("G2B_RUNTIME_ROLE", "UNIFIED") or "UNIFIED").upper()
    fresh_start = _enabled("G2B_V41_FRESH_START")
    destructive = _enabled("G2B_DESTRUCTIVE_RESET_CONFIRM")
    confirmed = bool(operator_confirmed_old_stopped)
    actions = []
    if test_mode:
        actions.append("DISABLE_TEST_MODE")
    if role != "UNIFIED":
        actions.append("USE_UNIFIED_RUNTIME_ROLE")
    if fresh_start or destructive:
        actions.append("DISABLE_ALL_DESTRUCTIVE_RESET_FLAGS")
    if not confirmed:
        actions.append("CONFIRM_OLD_WEB_AND_ALL_COLLECTOR_PIDS_EXITED")

    lock_busy = []
    pg_verified = False
    pg_error_code = ""
    # Always probe real PostgreSQL in production mode, even when operator
    # acknowledgement is missing; this reveals active conflicting leases.
    if not test_mode:
        try:
            lock_busy = _probe_advisory_locks()
            pg_verified = True
            if lock_busy:
                actions.append("STOP_OLD_OR_CONCURRENT_COLLECTION_AND_RECHECK_PG")
        except Exception as exc:
            pg_error_code = type(exc).__name__
            actions.append("VERIFY_POSTGRES_ADVISORY_CONNECTION")

    allowed = bool(
        confirmed and not test_mode and role == "UNIFIED"
        and not fresh_start and not destructive
        and pg_verified and not lock_busy
    )
    return {
        "gate": "G2B_V41_COLD_CUTOVER_OPERATOR_ATTESTED",
        "read_only": True,
        "source_api_calls": 0,
        "business_rows_modified": 0,
        "schema_ddl_performed": False,
        "pg_advisory_probe_ok": pg_verified,
        "pg_advisory_probe_busy": lock_busy,
        "pg_advisory_error_code": pg_error_code,
        "operator_attested_old_processes_stopped": confirmed,
        "old_process_exited_machine_verified": False,
        "test_mode_enabled": test_mode,
        "runtime_role": role,
        "destructive_flags_disabled": not fresh_start and not destructive,
        "cutover_ready": allowed,
        "required_actions": actions,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only G2B PostgreSQL cold cutover gate"
    )
    parser.add_argument(
        "--old-processes-stopped", action="store_true",
        help="Operator attests old web and all old isolated children already exited",
    )
    args = parser.parse_args(argv)
    report = inspect_cutover(
        operator_confirmed_old_stopped=args.old_processes_stopped
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["cutover_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
