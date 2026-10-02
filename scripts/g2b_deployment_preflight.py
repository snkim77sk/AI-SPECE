"""Deployment preflight for G2B vNext 4.1.

This command performs no G2B/LOFIN/EDUINFO source traffic.  It verifies the single
PostgreSQL storage contract, runtime role, budget schema and credential presence.
No secret values or database URLs are returned.
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
import budget_storage
import g2b_database
from db import db_is_persistent, init_db, source_credential_configured
from runtime_role import UNIFIED, runtime_role


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-keys",
        action="store_true",
        help="exit nonzero unless both infrastructure and live collection keys are ready",
    )
    return parser


def _flag(name, default=True):
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def run_preflight():
    role = runtime_role()
    test_mode_enabled = _flag("G2B_TEST_MODE", False)
    control_storage_ready = False
    control_storage_error_code = ""
    try:
        init_db()
        control_storage_ready = True
    except Exception as exc:
        control_storage_error_code = type(exc).__name__

    persistent = bool(db_is_persistent())
    try:
        budget_backend = budget_storage.backend_name()
    except Exception:
        budget_backend = "INVALID"

    budget_configured = False
    budget_ready = False
    budget_error_code = ""
    if budget_backend == "POSTGRESQL":
        try:
            budget_configured = bool(budget_storage.storage_configured())
            if budget_configured:
                budget_ready = bool(budget_storage.storage_ready())
                budget_error_code = str(
                    budget_storage.storage_error_code() or ""
                )
            else:
                budget_error_code = "BUDGET_POSTGRES_NOT_CONFIGURED"
        except Exception as exc:
            budget_error_code = str(
                budget_storage.storage_error_code()
                or type(exc).__name__
            )
    else:
        budget_error_code = "G2B_BUDGET_STORAGE_POSTGRESQL_REQUIRED"

    keys = {
        "g2b_service_key_configured": False,
        "lofin_api_key_configured": False,
        "eduinfo_api_key_configured": False,
    }
    if control_storage_ready:
        keys = {
            "g2b_service_key_configured": bool(
                source_credential_configured("g2b_service_key")
            ),
            "lofin_api_key_configured": bool(
                source_credential_configured("lofin_api_key")
            ),
            "eduinfo_api_key_configured": bool(
                source_credential_configured("eduinfo_api_key")
            ),
        }

    infrastructure_ready = bool(
        not test_mode_enabled
        and role == UNIFIED
        and control_storage_ready
        and persistent
        and budget_backend == "POSTGRESQL"
        and budget_configured
        and budget_ready
    )
    collection_keys_ready = bool(
        keys["g2b_service_key_configured"]
        and keys["lofin_api_key_configured"]
    )
    collection_ready = bool(infrastructure_ready and collection_keys_ready)

    required_actions = []
    if test_mode_enabled:
        required_actions.append("UNSET_G2B_TEST_MODE")
    if role != UNIFIED:
        required_actions.append("SET_G2B_RUNTIME_ROLE_UNIFIED")
    if not control_storage_ready:
        required_actions.append("FIX_G2B_POSTGRES_APP_SCHEMA")
    if not persistent:
        required_actions.append("FIX_G2B_POSTGRES_CONNECTION")
    if budget_backend != "POSTGRESQL":
        required_actions.append("SET_G2B_BUDGET_STORAGE_POSTGRESQL")
    elif not budget_configured:
        required_actions.append("SET_G2B_DATABASE_URL")
    elif not budget_ready:
        required_actions.append(
            budget_error_code or "FIX_BUDGET_POSTGRES"
        )
    if not keys["g2b_service_key_configured"]:
        required_actions.append("SET_G2B_SERVICE_KEY")
    if not keys["lofin_api_key_configured"]:
        required_actions.append("SET_LOFIN_API_KEY")

    database_source = ""
    try:
        database_source = g2b_database.database_source_label()
    except Exception:
        database_source = ""

    return {
        "version": APP_VERSION,
        "storage_contract": "POSTGRESQL_UNIFIED_V41",
        "database_backend": "POSTGRESQL",
        "database_configured": bool(budget_configured and persistent),
        "database_source": database_source,
        "app_schema": g2b_database.app_schema(),
        "budget_schema": g2b_database.budget_schema(),
        "preflight_scope": "DEPLOYMENT_STORAGE_AND_CREDENTIAL_PRESENCE_ONLY",
        "source_io_performed": False,
        "runtime_role": role,
        "test_mode_enabled": test_mode_enabled,
        "auto_sync_enabled": _flag("G2B_AUTO_SYNC", False),
        "control_storage_ready": control_storage_ready,
        "control_storage_persistent": persistent,
        "control_storage_error_code": control_storage_error_code,
        "budget_backend": budget_backend,
        "budget_postgres_configured": budget_configured,
        "budget_postgres_ready": budget_ready,
        "budget_postgres_error_code": budget_error_code,
        **keys,
        "education_live_transport_hold": True,
        "bulk_historical_hold": True,
        "infrastructure_ready": infrastructure_ready,
        "collection_keys_ready": collection_keys_ready,
        "collection_ready": collection_ready,
        "required_actions": required_actions,
    }


def main(argv=None):
    args = build_parser().parse_args(argv)
    report = run_preflight()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.require_keys:
        return 0 if report["collection_ready"] else 2
    return 0 if report["infrastructure_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
