"""Production web runtime for SINSUNG G2B vNext 4.1.

Production serves normalized budget/business records and read models. Source JSON
RAW is not an operating storage layer. External source traffic remains safety-gated
and is never triggered by read-only pages.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import html
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app_version import APP_VERSION
from db import (
    connect,
    current_db_path,
    db_is_persistent,
    get_result_sync_token,
    get_service_key,
    get_setting,
    set_source_credential,
    source_credential_configured,
)
from runtime_role import (
    automatic_collection_enabled,
    can_collect_sources,
    is_local_collector,
    is_result_server,
    is_unified,
    runtime_role,
)
from runtime_identity import (
    build_commit_info,
    deployment_verdict_info,
    process_identity_info,
    source_fingerprint_info,
)
import result_snapshot_vnext
import memory_guard
from vnext_clean_db import (
    authenticate,
    create_admin,
    create_session,
    delete_session,
    ensure_clean_schema,
    session_user,
    users_empty,
)

SESSION_COOKIE = "g2b_vnext_session"
SETUP_COOKIE = "g2b_vnext_setup"
TEST_MODE = str(os.getenv("G2B_TEST_MODE", "0")).lower() in ("1", "true", "yes", "on")
_TRUE_ENV = {"1", "true", "yes", "on"}


def _env_flag(name, default=False):
    fallback = "1" if default else "0"
    return str(os.getenv(name, fallback) or fallback).strip().lower() in _TRUE_ENV


def post_boot_maintenance_enabled():
    """Heavy source-free repair is opt-in and never runs in the 256MB web tier."""
    if not TEST_MODE and memory_guard.low_memory_web_hold():
        return False
    return bool(_env_flag("G2B_POST_BOOT_MAINTENANCE_ENABLE", False))


def _memory_status_fields(*, collect=False):
    state = memory_guard.snapshot(collect=collect)
    events = dict(state.get("cgroup_events") or {})
    guard_ok = bool(state.get("guard_ok", True))
    return {
        "memory_rss_mib": float(state.get("rss_mib") or 0.0),
        "memory_soft_limit_mib": int(
            state.get("soft_limit_mib")
            or memory_guard.soft_limit_mib()
        ),
        "memory_process_guard_ok": bool(
            state.get("process_guard_ok", guard_ok)
        ),
        "memory_guard_ok": guard_ok,
        "memory_heavy_work_ok": bool(
            state.get("heavy_work_ok", guard_ok)
        ),
        "memory_low_memory_web_hold": bool(
            state.get("low_memory_web_hold", False)
        ),
        "memory_guard_state": str(
            state.get("guard_state")
            or ("SAFE" if guard_ok else "HOLD")
        ),
        "memory_cgroup_source": str(state.get("cgroup_source") or ""),
        "memory_cgroup_limit_mib": float(
            state.get("cgroup_limit_mib") or 0.0
        ),
        "memory_cgroup_current_mib": float(
            state.get("cgroup_current_mib") or 0.0
        ),
        "memory_cgroup_peak_mib": float(
            state.get("cgroup_peak_mib") or 0.0
        ),
        "memory_cgroup_effective_mib": float(
            state.get("cgroup_effective_mib") or 0.0
        ),
        "memory_cgroup_wait_threshold_mib": float(
            state.get("cgroup_wait_threshold_mib") or 0.0
        ),
        "memory_cgroup_block_threshold_mib": float(
            state.get("cgroup_block_threshold_mib") or 0.0
        ),
        "memory_cgroup_blocked": bool(
            state.get("cgroup_blocked", False)
        ),
        "memory_cgroup_oom_group": int(
            state.get("cgroup_oom_group", 0) or 0
        ),
        "memory_cgroup_oom": int(events.get("oom", 0)),
        "memory_cgroup_oom_kill": int(events.get("oom_kill", 0)),
        "memory_cgroup_failcnt": int(events.get("failcnt", 0)),
    }


TARGET_CATEGORIES = ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")
CATEGORY_LABELS = {
    "LIGHTING": "조명",
    "POLE": "가로등주",
    "ELECTRICAL": "전기",
    "SOLAR": "태양광",
}
LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_FAILURES = 8
BACKEND_RETRY_SECONDS = 5.0
MAX_RESULT_SYNC_COMPRESSED_BYTES = 4 * 1024 * 1024
MAX_RESULT_SYNC_JSON_BYTES = 12 * 1024 * 1024


def _env_int(name, default, *, lower, upper):
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(lower), min(int(upper), value))


SHOPPING_SYNC_INTERVAL_SECONDS = _env_int(
    "G2B_SHOPPING_SYNC_INTERVAL_SECONDS", 7200, lower=300, upper=86400
)
SHOPPING_SYNC_DAYS_PER_RUN = _env_int(
    "G2B_SHOPPING_SYNC_DAYS_PER_RUN", 62, lower=1, upper=62
)
SHOPPING_RECHECK_DAYS = _env_int(
    "G2B_SHOPPING_RECHECK_DAYS", 7, lower=0, upper=7
)
SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN = _env_int(
    "G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN", 2, lower=0, upper=2
)
SHOPPING_RETENTION_DAYS = _env_int(
    "G2B_SHOPPING_RETENTION_DAYS", 365, lower=30, upper=365
)
SHOPPING_RETENTION_MONTHS = _env_int(
    "G2B_SHOPPING_RETENTION_MONTHS", 27, lower=1, upper=27
)
BUDGET_SYNC_MAX_PAGES = _env_int(
    "G2B_BUDGET_SYNC_MAX_PAGES", 256, lower=1, upper=512
)
ISOLATED_BUDGET_SCOPE_MAX_PAGES = _env_int(
    "G2B_ISOLATED_BUDGET_SCOPE_MAX_PAGES", 16, lower=4, upper=64
)
ISOLATED_BUDGET_CLASSIFY_MAX_BATCHES = _env_int(
    "G2B_ISOLATED_BUDGET_CLASSIFY_MAX_BATCHES", 32, lower=1, upper=128
)
PARTIAL_PROGRESS_RETRY_SECONDS = _env_int(
    "G2B_PARTIAL_PROGRESS_RETRY_SECONDS", 15, lower=10, upper=300
)
BUDGET_SYNC_MAX_REQUESTS = _env_int(
    "G2B_BUDGET_SYNC_MAX_REQUESTS", 500, lower=1, upper=500
)
FUTURE_BUDGET_SYNC_MAX_PAGES = _env_int(
    "G2B_FUTURE_BUDGET_SYNC_MAX_PAGES", 24, lower=1, upper=128
)
CURRENT_APPROPRIATION_SYNC_MAX_PAGES = _env_int(
    "G2B_CURRENT_APPROPRIATION_SYNC_MAX_PAGES", 16, lower=1, upper=128
)
BUDGET_HISTORY_DAYS_PER_RUN = _env_int(
    "G2B_BUDGET_HISTORY_DAYS_PER_RUN", 31, lower=1, upper=31
)
BUDGET_HISTORY_RESERVE_REQUESTS = _env_int(
    "G2B_BUDGET_HISTORY_RESERVE_REQUESTS", 20, lower=0, upper=128
)
BUDGET_RETENTION_DAYS = _env_int(
    "G2B_BUDGET_RETENTION_DAYS", 365, lower=30, upper=730
)
BUDGET_RECEIPT_RETENTION_DAYS = _env_int(
    "G2B_BUDGET_RECEIPT_RETENTION_DAYS", 3, lower=1, upper=30
)
OPERATIONAL_LEASE_RETRY_SECONDS = _env_int(
    "G2B_OPERATIONAL_LEASE_RETRY_SECONDS", 15, lower=5, upper=300
)
MATCH_ROLLOVER_REFRESH_SECONDS = 6 * 60 * 60

_BACKEND_LOCK = threading.Lock()
_BACKEND_STATE = {
    "initialized": False,
    "initializing": False,
    "backend_ok": False,
    "backend_error": "",
    "attempts": 0,
    "last_attempt_at": 0.0,
    "fresh_start_status": "",
    "fresh_start_marker_ok": False,
    "fresh_start_marker_value": "",
    "fresh_start_reset_performed": False,
}
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES = {}
_RECENT_COLLECTION_LOCK = threading.Lock()
_RECENT_COLLECTION_WAKE = threading.Event()
_RECENT_COLLECTION_THREAD = None
_MANUAL_COLLECTION_LOCK = threading.Lock()
_MANUAL_COLLECTION_THREADS = {"shopping": None, "budget": None}
_HEAVY_WORK_LOCK = threading.Lock()
_ISOLATED_HEAVY_LOCK = threading.Lock()
_ISOLATED_HEAVY_PROCESS = None
_ISOLATED_HEAVY_KIND = ""
_ISOLATED_HEAVY_LAST = {"kind": "", "exit_code": None}
_ISOLATED_HEAVY_PENDING = []
_ISOLATED_HEAVY_SUPERVISOR = None
_MATCH_BACKFILL_LOCK = threading.Lock()
_MATCH_BACKFILL_THREAD = None
_MATCH_BACKFILL_STATE = {
    "state": "IDLE",
    "last_error": "",
    "last_started_at": "",
    "last_finished_at": "",
    "rollover_years": [],
    "population_complete_years": [],
    "persisted_matches_by_year": {},
    "pattern_count": 0,
    "patterns_updated_at": "",
    "legacy_backfill_active": False,
    "shopping_complete_days": 0,
    "shopping_total_days": 0,
    "shopping_next_date": "",
    "budget_complete": False,
    # Compatibility counters remain available for older status consumers.
    "persisted_2026_matches": 0,
    "persisted_2025_matches": 0,
    "last_result_status": "",
}
_RECENT_COLLECTION_STATE = {
    "state": "IDLE",
    "last_error": "",
    "last_started_at": "",
    "last_finished_at": "",
    "last_status": "",
    "shopping_run_state": "IDLE",
    "shopping_last_error": "",
    "shopping_last_started_at": "",
    "shopping_last_finished_at": "",
    "shopping_last_status": "",
    "budget_run_state": "IDLE",
    "budget_last_error": "",
    "budget_maintenance_warning": "",
    "budget_last_started_at": "",
    "budget_last_finished_at": "",
    "budget_last_status": "",
    "future_budget_status": "",
    "future_budget_year": 0,
    "current_appropriation_status": "",
    "current_appropriation_year": 0,
    "budget_history_status": "",
    "lofin_quota_limit": 0,
    "lofin_quota_used": 0,
    "lofin_quota_remaining": 0,
}
_BUDGET_POSTGRES_PROBE_LOCK = threading.Lock()
_BUDGET_POSTGRES_PROBE_STATE = {
    "configured": False,
    "ready": False,
    "error_code": "",
    "checked_at": 0.0,
}


def runtime_build_commit():
    """Return authoritative platform/checkout identity with stale-manual detection."""
    return str(build_commit_info().get("build_commit") or "")


def runtime_build_commit_source():
    return str(build_commit_info().get("build_commit_source") or "")


def build_commit_label():
    value = runtime_build_commit()
    return value[:12] if value else "미확인"


def runtime_source_fingerprint():
    return str(source_fingerprint_info().get("source_fingerprint") or "")


def runtime_deployment_identity():
    identity = {
        **build_commit_info(),
        **source_fingerprint_info(),
        **process_identity_info(),
    }
    return {
        **identity,
        **deployment_verdict_info(
            identity,
            phase="G2B_VNEXT_CLEAN",
            recovery_mode=False,
        ),
    }


def esc(value):
    return html.escape(str(value or ""))


def money(value):
    try:
        return f"{int(value or 0):,}원"
    except Exception:
        return "0원"


def _public_error(value):
    """Keep full diagnostics in logs/tests, but minimize public production detail."""
    text = str(value or "").strip()
    if TEST_MODE or not text:
        return text
    return text.split(":", 1)[0][:120]


def _secure(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers[
        "Content-Security-Policy"
    ] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
    if not TEST_MODE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def initialize_backend(*, force=False):
    """Initialize storage synchronously.

    Production startup never calls this on the event-loop thread; it is retained as
    a deterministic helper for background work and regression tests.
    """
    with _BACKEND_LOCK:
        if _BACKEND_STATE["backend_ok"] and not force:
            return True
        if _BACKEND_STATE["initializing"] and not force:
            return False
        _BACKEND_STATE["initializing"] = True
        _BACKEND_STATE["attempts"] += 1
        _BACKEND_STATE["last_attempt_at"] = time.monotonic()

    fresh_start_result = {
        "status": "TEST_MODE" if TEST_MODE else "",
        "marker": False,
        "marker_value": "",
        "reset": False,
    }
    try:
        # 4.1 intentionally starts from a fresh G2B dataset instead of migrating
        # the old SQLite + budget-PostgreSQL split.  The destructive step is
        # guarded by G2B_V41_FRESH_START=1 and a durable PostgreSQL marker.
        if not TEST_MODE:
            import v41_fresh_start
            fresh_start_result = v41_fresh_start.prepare_v41_storage()

        # Common startup owns control + shopping storage only. Budget readiness is
        # probed independently below and inside budget collection, so a budget-only
        # schema outage cannot suppress 나라장터 shopping collection.
        ensure_clean_schema()
    except Exception as exc:
        with _BACKEND_LOCK:
            _BACKEND_STATE.update(
                initialized=True,
                initializing=False,
                backend_ok=False,
                backend_error=f"{type(exc).__name__}: {str(exc)[:400]}",
                fresh_start_status=(
                    str(fresh_start_result.get("status") or "FAILED")
                ),
                fresh_start_marker_ok=bool(
                    fresh_start_result.get("marker")
                    and fresh_start_result.get("marker_value")
                    == "NORMALIZED_NO_RAW_V1"
                ),
                fresh_start_marker_value=str(
                    fresh_start_result.get("marker_value") or ""
                ),
                fresh_start_reset_performed=bool(
                    fresh_start_result.get("reset")
                ),
            )
        print("G2B_VNEXT_BOOT_DEGRADED", type(exc).__name__, flush=True)
        return False

    with _BACKEND_LOCK:
        _BACKEND_STATE.update(
            initialized=True,
            initializing=False,
            backend_ok=True,
            backend_error="",
            fresh_start_status=str(
                fresh_start_result.get("status") or ""
            ),
            fresh_start_marker_ok=bool(
                fresh_start_result.get("marker")
                and fresh_start_result.get("marker_value")
                == "NORMALIZED_NO_RAW_V1"
            ),
            fresh_start_marker_value=str(
                fresh_start_result.get("marker_value") or ""
            ),
            fresh_start_reset_performed=bool(
                fresh_start_result.get("reset")
            ),
        )
    print("G2B_VNEXT_BOOT_OK", APP_VERSION, flush=True)

    # Heavy source-free repair is intentionally opt-in. Normal production boot
    # stops after HTTP + PostgreSQL/schema readiness so a redeploy cannot trigger
    # a large classification scan in the web process.
    if (
        post_boot_maintenance_enabled()
        and not TEST_MODE
        and not is_result_server()
    ):
        maintenance_slot = _HEAVY_WORK_LOCK.acquire(blocking=False)
        if not maintenance_slot:
            print("G2B_POST_BOOT_MAINTENANCE_MEMORY_GUARD_HELD", flush=True)
        else:
            try:
                import gc
                import budget_storage as _budget_storage
                import classification_vnext as _classification_vnext
                if (
                    _budget_storage.storage_configured()
                    and _budget_storage.storage_ready()
                ):
                    repaired = []
                    for _dataset in _budget_storage.BUDGET_DATASETS:
                        memory = memory_guard.snapshot(collect=True)
                        if not memory.get("heavy_work_ok", memory["guard_ok"]):
                            print(
                                "G2B_BUDGET_CLASSIFICATION_REPAIR_MEMORY_HOLD",
                                memory["rss_mib"],
                                memory["soft_limit_mib"],
                                flush=True,
                            )
                            break
                        repaired.append(
                            _classification_vnext.classify_dataset(
                                _dataset,
                                batch_size=200,
                            )
                        )
                        gc.collect()
                    print(
                        "G2B_BUDGET_CLASSIFICATION_REPAIR_OK",
                        sum(int(row.get("classified") or 0) for row in repaired),
                        flush=True,
                    )
            except Exception as exc:
                print(
                    "G2B_BUDGET_CLASSIFICATION_REPAIR_DEGRADED",
                    type(exc).__name__,
                    flush=True,
                )
            finally:
                _HEAVY_WORK_LOCK.release()
    elif not TEST_MODE:
        print("G2B_POST_BOOT_MAINTENANCE_HOLD", flush=True)

    # Recurring source work is separately opt-in through G2B_AUTO_SYNC=1.
    schedule_recent_collection()
    return True


def _backend_worker():
    initialize_backend(force=True)


def schedule_backend_init(*, force=False):
    """Start DB/schema initialization in a daemon thread and return immediately."""
    with _BACKEND_LOCK:
        if _BACKEND_STATE["backend_ok"] and not force:
            return False
        if _BACKEND_STATE["initializing"]:
            return False
        if (
            not force
            and _BACKEND_STATE["attempts"] > 0
            and time.monotonic() - float(_BACKEND_STATE["last_attempt_at"] or 0)
            < BACKEND_RETRY_SECONDS
        ):
            return False
        # Reserve the initialization slot before the thread is started so multiple
        # simultaneous platform probes cannot create duplicate DB initializers.
        _BACKEND_STATE["initializing"] = True
    thread = threading.Thread(
        target=_backend_worker,
        name="g2b-vnext-backend-init",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as exc:
        with _BACKEND_LOCK:
            _BACKEND_STATE.update(
                initializing=False,
                backend_error=f"THREAD_START_{type(exc).__name__}",
            )
        return False
    return True


def backend_status():
    with _BACKEND_LOCK:
        return dict(_BACKEND_STATE)


def _isolated_heavy_worker_mode():
    return bool(_env_flag("G2B_ISOLATED_HEAVY_WORKER", False))


def _budget_scope_page_limit(requested):
    value = max(1, int(requested))
    if _isolated_heavy_worker_mode():
        return min(value, int(ISOLATED_BUDGET_SCOPE_MAX_PAGES))
    return value


def _auto_sync_enabled():
    """Owner-approved automatic collection policy for this runtime role.

    On the 256 MiB UNIFIED tier the scheduler itself stays lightweight and sends
    source work to disposable isolated children, so low_memory_web_hold must not
    disable automatic scheduling.
    """
    return bool(
        can_collect_sources()
        and automatic_collection_enabled(test_mode=TEST_MODE)
    )


def _isolated_worker_exit_state(kind, exit_code):
    """Map disposable child exit codes back into the web-visible source state."""
    mode = str(kind or "").strip().lower()
    if mode not in {"shopping", "budget"}:
        return
    code = int(exit_code)
    state_map = {
        0: ("COMPLETE", ""),
        72: ("WAITING_KEYS", "KEY_REQUIRED"),
        73: ("WAITING_QUOTA", "DAILY_QUOTA_EXHAUSTED"),
        74: ("WAITING_STORAGE", "STORAGE_NOT_READY"),
        75: ("WAITING_MEMORY", "MEMORY_PRESSURE"),
        76: ("WAITING_MEMORY", "CGROUP_OOM_HOLD"),
        -9: ("WAITING_MEMORY", "WORKER_SIGKILL_MEMORY_HOLD"),
        137: ("WAITING_MEMORY", "WORKER_SIGKILL_MEMORY_HOLD"),
        77: ("LEASE_HELD", "LEASE_HELD"),
        78: ("PARTIAL", "PARTIAL"),
    }
    run_state, error = state_map.get(
        code,
        ("FAILED", f"ISOLATED_WORKER_EXIT_{code}"),
    )
    if code in {1, 75, 76}:
        try:
            detail = str(
                get_setting(f"{mode}_recent_last_error", "") or ""
            ).strip()
        except Exception:
            detail = ""
        if detail:
            if code == 1 and mode == "shopping" and not detail.upper().startswith(
                ("SHOPPING", "MEMORY_PRESSURE")
            ):
                error = ("SHOPPING:" + detail)[:180]
            else:
                error = detail[:180]
    finished = time.strftime("%Y-%m-%dT%H:%M:%S")
    _set_source_collection_state(
        mode,
        run_state=run_state,
        last_status=run_state,
        last_error=error,
        last_finished_at=finished,
    )
    with _RECENT_COLLECTION_LOCK:
        shopping_state = str(
            _RECENT_COLLECTION_STATE.get("shopping_run_state") or "IDLE"
        )
        budget_state = str(
            _RECENT_COLLECTION_STATE.get("budget_run_state") or "IDLE"
        )
        shopping_error = str(
            _RECENT_COLLECTION_STATE.get("shopping_last_error") or ""
        )
        budget_error = str(
            _RECENT_COLLECTION_STATE.get("budget_last_error") or ""
        )
    aggregate = _aggregate_source_run_state(shopping_state, budget_state)
    _set_recent_collection_state(
        state=aggregate,
        last_status=aggregate,
        last_error=_aggregate_source_errors(
            shopping_error,
            budget_error,
        ),
        last_finished_at=finished,
    )


def _launch_isolated_heavy_worker_locked(mode):
    global _ISOLATED_HEAVY_PROCESS, _ISOLATED_HEAVY_KIND
    import subprocess
    import sys

    env = os.environ.copy()
    env["G2B_AUTO_SYNC"] = "0"
    env["G2B_AUTO_SYNC_DISABLE"] = "1"
    env["G2B_POST_BOOT_MAINTENANCE_ENABLE"] = "0"
    env["G2B_MATCH_ROLLOVER_AUTO_ENABLE"] = "0"
    env["G2B_V41_FRESH_START"] = "0"
    process = subprocess.Popen(
        [sys.executable, "-B", "-u", "-m", "g2b_heavy_worker", mode],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )
    _ISOLATED_HEAVY_PROCESS = process
    _ISOLATED_HEAVY_KIND = mode
    _ISOLATED_HEAVY_LAST["kind"] = mode
    _ISOLATED_HEAVY_LAST["exit_code"] = None
    print(
        "G2B_ISOLATED_WORKER_STARTED",
        mode,
        int(process.pid or 0),
        flush=True,
    )
    return process


def _isolated_heavy_supervisor_worker():
    global _ISOLATED_HEAVY_PROCESS, _ISOLATED_HEAVY_KIND
    global _ISOLATED_HEAVY_SUPERVISOR
    current_thread = threading.current_thread()
    while True:
        with _ISOLATED_HEAVY_LOCK:
            process = _ISOLATED_HEAVY_PROCESS
            kind = str(_ISOLATED_HEAVY_KIND or "")
        if process is None:
            with _ISOLATED_HEAVY_LOCK:
                if _ISOLATED_HEAVY_SUPERVISOR is current_thread:
                    _ISOLATED_HEAVY_SUPERVISOR = None
            return

        code = int(process.wait())
        next_mode = ""
        with _ISOLATED_HEAVY_LOCK:
            if _ISOLATED_HEAVY_PROCESS is process:
                _ISOLATED_HEAVY_LAST["kind"] = kind
                _ISOLATED_HEAVY_LAST["exit_code"] = code
                _ISOLATED_HEAVY_PROCESS = None
                _ISOLATED_HEAVY_KIND = ""
            if _ISOLATED_HEAVY_PENDING:
                next_mode = str(_ISOLATED_HEAVY_PENDING.pop(0) or "")

        _isolated_worker_exit_state(kind, code)

        if not next_mode:
            with _ISOLATED_HEAVY_LOCK:
                if _ISOLATED_HEAVY_SUPERVISOR is current_thread:
                    _ISOLATED_HEAVY_SUPERVISOR = None
            return

        memory = memory_guard.snapshot(collect=True)
        if (
            int(memory.get("cgroup_oom_group") or 0) == 1
            or not bool(memory.get("guard_ok", False))
        ):
            _isolated_worker_exit_state(next_mode, 75)
            with _ISOLATED_HEAVY_LOCK:
                while _ISOLATED_HEAVY_PENDING:
                    held_mode = str(_ISOLATED_HEAVY_PENDING.pop(0) or "")
                    _isolated_worker_exit_state(held_mode, 75)
                if _ISOLATED_HEAVY_SUPERVISOR is current_thread:
                    _ISOLATED_HEAVY_SUPERVISOR = None
            return

        with _ISOLATED_HEAVY_LOCK:
            if _ISOLATED_HEAVY_PROCESS is not None:
                _ISOLATED_HEAVY_PENDING.insert(0, next_mode)
                continue
            _launch_isolated_heavy_worker_locked(next_mode)
        started = time.strftime("%Y-%m-%dT%H:%M:%S")
        _set_source_collection_state(
            next_mode,
            run_state="RUNNING",
            last_status="RUNNING",
            last_error="",
            last_started_at=started,
        )
        _set_recent_collection_state(
            state="RUNNING",
            last_status="RUNNING",
            last_error="",
            last_started_at=started,
        )


def _ensure_isolated_heavy_supervisor_locked():
    global _ISOLATED_HEAVY_SUPERVISOR
    existing = _ISOLATED_HEAVY_SUPERVISOR
    if existing is not None and existing.is_alive():
        return
    thread = threading.Thread(
        target=_isolated_heavy_supervisor_worker,
        name="g2b-isolated-heavy-supervisor",
        daemon=True,
    )
    _ISOLATED_HEAVY_SUPERVISOR = thread
    thread.start()


def _isolated_heavy_worker_status():
    with _ISOLATED_HEAVY_LOCK:
        process = _ISOLATED_HEAVY_PROCESS
        kind = str(_ISOLATED_HEAVY_KIND or "")
        pending = list(_ISOLATED_HEAVY_PENDING)
        if process is None:
            return {
                "running": False,
                "kind": str(_ISOLATED_HEAVY_LAST.get("kind") or ""),
                "exit_code": _ISOLATED_HEAVY_LAST.get("exit_code"),
                "pid": 0,
                "pending": pending,
            }
        code = process.poll()
        return {
            "running": code is None,
            "kind": kind,
            "exit_code": None if code is None else int(code),
            "pid": int(process.pid or 0),
            "pending": pending,
        }


def _isolated_worker_admission_ok(mode):
    memory = memory_guard.snapshot(collect=True)
    if int(memory.get("cgroup_oom_group") or 0) == 1:
        print("G2B_ISOLATED_WORKER_HOLD OOM_GROUP", mode, flush=True)
        return False
    # low_memory_web_hold intentionally makes heavy_work_ok false; admission of
    # the disposable child uses the instantaneous guard only.
    if not bool(memory.get("guard_ok", False)):
        print(
            "G2B_ISOLATED_WORKER_HOLD MEMORY_PRESSURE",
            mode,
            memory.get("guard_state"),
            flush=True,
        )
        return False
    return True


def _request_isolated_source_worker(kind):
    """Start or queue one source worker without running two heavy children at once."""
    mode = str(kind or "").strip().lower()
    if mode not in {"shopping", "budget"}:
        raise ValueError("UNSUPPORTED_ISOLATED_SOURCE_MODE")
    if not _isolated_worker_admission_ok(mode):
        return "HOLD"

    with _ISOLATED_HEAVY_LOCK:
        existing = _ISOLATED_HEAVY_PROCESS
        active_kind = str(_ISOLATED_HEAVY_KIND or "")
        if existing is not None:
            if active_kind == mode or mode in _ISOLATED_HEAVY_PENDING:
                return "ALREADY"
            _ISOLATED_HEAVY_PENDING.append(mode)
            _ensure_isolated_heavy_supervisor_locked()
            print(
                "G2B_ISOLATED_WORKER_QUEUED",
                mode,
                active_kind,
                flush=True,
            )
            return "QUEUED"

        _launch_isolated_heavy_worker_locked(mode)
        _ensure_isolated_heavy_supervisor_locked()
        return "STARTED"


def _spawn_isolated_heavy_worker(kind):
    """Start one non-source disposable heavy worker on the 256 MiB tier."""
    mode = str(kind or "").strip().lower()
    if mode not in {"shopping", "budget", "match", "match-legacy"}:
        raise ValueError("UNSUPPORTED_ISOLATED_HEAVY_MODE")
    if mode in {"shopping", "budget"}:
        return _request_isolated_source_worker(mode) in {"STARTED", "QUEUED"}
    if not _isolated_worker_admission_ok(mode):
        return False

    with _ISOLATED_HEAVY_LOCK:
        existing = _ISOLATED_HEAVY_PROCESS
        if existing is not None:
            return False
        _launch_isolated_heavy_worker_locked(mode)
        _ensure_isolated_heavy_supervisor_locked()
        return True


def recent_collection_status():
    with _RECENT_COLLECTION_LOCK:
        state = dict(_RECENT_COLLECTION_STATE)
        thread = _RECENT_COLLECTION_THREAD
    state["thread_alive"] = bool(thread and thread.is_alive())
    isolated = _isolated_heavy_worker_status()
    state["isolated_heavy_worker"] = dict(isolated)
    with _MANUAL_COLLECTION_LOCK:
        manual_shopping_running = bool(
            _MANUAL_COLLECTION_THREADS.get("shopping")
            and _MANUAL_COLLECTION_THREADS["shopping"].is_alive()
        )
        manual_budget_running = bool(
            _MANUAL_COLLECTION_THREADS.get("budget")
            and _MANUAL_COLLECTION_THREADS["budget"].is_alive()
        )
    pending_isolated = {
        str(value or "")
        for value in (isolated.get("pending") or [])
    }
    if isolated.get("running") and isolated.get("kind") == "shopping":
        manual_shopping_running = True
    if isolated.get("running") and isolated.get("kind") == "budget":
        manual_budget_running = True
    if "shopping" in pending_isolated:
        manual_shopping_running = True
    if "budget" in pending_isolated:
        manual_budget_running = True
    state["manual_shopping_running"] = manual_shopping_running
    state["manual_budget_running"] = manual_budget_running
    state["manual_shopping_queued"] = "shopping" in pending_isolated
    state["manual_budget_queued"] = "budget" in pending_isolated
    state["manual_sources_running"] = int(
        manual_shopping_running
    ) + int(manual_budget_running)
    if state["manual_sources_running"] > 0:
        # The compatibility/global state must never report COMPLETE while one of
        # the independent manual source workers is still active.
        state["state"] = "RUNNING"
    state["auto_sync_enabled"] = _auto_sync_enabled()
    state["order"] = "FORWARD"
    state["start_date"] = "2026-01-01"
    state["interval_seconds"] = SHOPPING_SYNC_INTERVAL_SECONDS
    state["shopping_scope"] = "LIGHTING_AND_POLE_ONLY"
    state["budget_scope"] = "NORMALIZED_BUDGET_POSTGRESQL"
    state.update(_memory_status_fields(collect=False))
    state["post_boot_maintenance_enabled"] = post_boot_maintenance_enabled()
    return state


def _set_recent_collection_state(**values):
    with _RECENT_COLLECTION_LOCK:
        _RECENT_COLLECTION_STATE.update(values)


def _set_source_collection_state(source, **values):
    source = str(source or "").strip().lower()
    if source not in {"shopping", "budget"}:
        raise ValueError("UNSUPPORTED_SOURCE_STATE")
    mapped = {
        f"{source}_{key}": value
        for key, value in values.items()
    }
    _set_recent_collection_state(**mapped)


def _component_run_state(value, *, failed=False):
    if failed:
        return "FAILED"
    value = str(value or "")
    if value == "COMPLETE":
        return "COMPLETE"
    if value == "WAITING_POSTGRES":
        return "WAITING_STORAGE"
    if value == "WAITING_KEY":
        return "WAITING_KEYS"
    if value == "WAITING_QUOTA":
        return "WAITING_QUOTA"
    if value == "WAITING_MEMORY":
        return "WAITING_MEMORY"
    if value in {"RUNNING", "PARTIAL", "INCOMPLETE"}:
        return "PARTIAL"
    return "PARTIAL"


def _combined_budget_component_status(values):
    """Preserve WAITING_QUOTA when quota is the only unfinished budget state."""
    states = {
        str(value or "").strip().upper()
        for value in values
        if str(value or "").strip()
    }
    if "FAILED" in states:
        return "FAILED"
    if (
        "WAITING_QUOTA" in states
        and states <= {"COMPLETE", "WAITING_QUOTA"}
    ):
        return "WAITING_QUOTA"
    if states & {"RUNNING", "PARTIAL", "INCOMPLETE", "WAITING_QUOTA"}:
        return "PARTIAL"
    if states == {"COMPLETE"}:
        return "COMPLETE"
    return "PARTIAL"


def _aggregate_source_run_state(shopping_state, budget_state):
    """Summarize independent source states without last-finisher races."""
    states = {
        str(value or "").strip().upper()
        for value in (shopping_state, budget_state)
        if str(value or "").strip().upper() not in {"", "IDLE"}
    }
    if not states:
        return "IDLE"
    for state in (
        "RUNNING",
        "FAILED",
        "WAITING_STORAGE",
        "WAITING_PERSISTENT_STORAGE",
        "WAITING_MEMORY",
        "WAITING_KEYS",
        "WAITING_QUOTA",
        "WAITING_MEMORY",
        "QUEUED",
        "PARTIAL",
        "LEASE_HELD",
    ):
        if state in states:
            return state
    if states == {"COMPLETE"}:
        return "COMPLETE"
    if states <= {"COMPLETE"}:
        return "COMPLETE"
    return "PARTIAL"


def _aggregate_source_errors(shopping_error, budget_error):
    return ",".join(
        value
        for value in (
            str(shopping_error or "").strip(),
            str(budget_error or "").strip(),
        )
        if value
    )


def _run_recent_collection_once_impl(source="all"):
    """Run one operational source cycle.

    source="shopping" executes only 나라장터 delivery collection.
    source="budget" executes only 지방재정365 budget collection.
    source="all" preserves the existing automatic unified cycle.
    """
    source = str(source or "all").strip().lower()
    if source not in {"all", "shopping", "budget"}:
        raise ValueError("UNSUPPORTED_OPERATIONAL_SOURCE")
    run_shopping = source in {"all", "shopping"}
    run_budget = source in {"all", "budget"}
    if not backend_status().get("backend_ok"):
        _set_recent_collection_state(state="WAITING_STORAGE")
        return None
    if is_unified() and not TEST_MODE and not db_is_persistent():
        _set_recent_collection_state(state="WAITING_PERSISTENT_STORAGE")
        return None

    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    now_dt = _dt.datetime.now(_ZoneInfo("Asia/Seoul"))
    now = now_dt.isoformat(timespec="seconds")
    today = now_dt.date()
    state_update = {
        "state": "RUNNING",
        "last_started_at": now,
        "last_error": "",
    }
    if run_shopping:
        state_update["shopping_status"] = "WAITING_KEY"
        state_update.update(
            shopping_run_state="RUNNING",
            shopping_last_started_at=now,
            shopping_last_error="",
        )
    if run_budget:
        state_update.update(
            future_budget_status="WAITING_KEY",
            future_budget_year=today.year + 1,
            current_appropriation_status="WAITING_KEY",
            current_appropriation_year=today.year,
            budget_status="WAITING_KEY",
            budget_history_status="WAITING_KEY",
            budget_run_state="RUNNING",
            budget_last_started_at=now,
            budget_last_error="",
            budget_maintenance_warning="",
        )
    _set_recent_collection_state(**state_update)

    outcomes = {
        "source": source,
        "shopping": None,
        "shopping_retention": None,
        "future_budget": None,
        "current_appropriation": None,
        "budget": None,
    }
    failures = []
    shopping_memory_hold = False

    # 1) Shopping: nationwide scan, normalized lighting/pole records only.
    if run_shopping and get_service_key(""):
        try:
            import shopping_recent_vnext
            shopping = shopping_recent_vnext.collect_forward(
                start_date="2026-01-01",
                max_days=SHOPPING_SYNC_DAYS_PER_RUN,
                recheck_days=SHOPPING_RECHECK_DAYS,
                longtail_recheck_days_per_run=(
                    SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN
                ),
                retention_days=SHOPPING_RETENTION_DAYS,
                retention_months=SHOPPING_RETENTION_MONTHS,
                # Production shopping is classified deterministically while each
                # normalized row is persisted. Avoid repeated post-classification
                # calls for every completed date during large catch-up runs.
                defer_classification=True,
            )
            outcomes["shopping"] = shopping
            _set_recent_collection_state(
                shopping_status=str(shopping.get("status") or "COMPLETE")
            )
        except memory_guard.MemoryPressureError as exc:
            shopping_memory_hold = True
            reason = " ".join(str(exc or "MEMORY_PRESSURE").split())[:160]
            error = f"MEMORY_PRESSURE:{reason}"
            _set_recent_collection_state(
                shopping_status="WAITING_MEMORY",
                last_error=error,
                shopping_last_error=error,
            )
        except Exception as exc:
            failures.append(("shopping", type(exc).__name__))
            _set_recent_collection_state(
                shopping_status="FAILED",
                last_error=f"SHOPPING:{type(exc).__name__}",
                shopping_last_error=f"SHOPPING:{type(exc).__name__}",
            )

    # Shopping retention is a storage policy, not a source-key side effect.
    # Run it whenever the shopping source family is selected, even when the key is
    # temporarily absent or the source request failed.
    if run_shopping and not shopping_memory_hold:
        try:
            import shopping_store_v41
            outcomes["shopping_retention"] = shopping_store_v41.purge_history(
                SHOPPING_RETENTION_DAYS,
                retention_months=SHOPPING_RETENTION_MONTHS,
                now=now_dt,
            )
        except Exception as exc:
            failures.append(("shopping_retention", type(exc).__name__))
            _set_recent_collection_state(
                last_error=f"SHOPPING_RETENTION:{type(exc).__name__}",
                shopping_last_error=(
                    f"SHOPPING_RETENTION:{type(exc).__name__}"
                ),
            )

    if not run_budget:
        finished = _dt.datetime.now(_ZoneInfo("Asia/Seoul")).isoformat(
            timespec="seconds"
        )
        with _RECENT_COLLECTION_LOCK:
            shopping_state = str(
                _RECENT_COLLECTION_STATE.get("shopping_status") or ""
            )
            shopping_state_error = str(
                _RECENT_COLLECTION_STATE.get("shopping_last_error") or ""
            )
        if failures:
            final_state = "FAILED"
            final_error = ",".join(
                f"{name}:{kind}" for name, kind in failures
            )
        elif shopping_state == "COMPLETE":
            final_state = "COMPLETE"
            final_error = ""
        elif shopping_state == "WAITING_KEY":
            final_state = "WAITING_KEYS"
            final_error = ""
        elif shopping_state == "WAITING_QUOTA":
            final_state = "WAITING_QUOTA"
            final_error = ""
        elif shopping_state == "WAITING_MEMORY":
            final_state = "WAITING_MEMORY"
            final_error = shopping_state_error or "MEMORY_PRESSURE"
        elif shopping_state in {"RUNNING", "PARTIAL", "INCOMPLETE"}:
            final_state = "PARTIAL"
            final_error = ""
        else:
            final_state = "PARTIAL"
            final_error = ""
        with _RECENT_COLLECTION_LOCK:
            budget_run_state = str(
                _RECENT_COLLECTION_STATE.get("budget_run_state") or "IDLE"
            )
            budget_last_error = str(
                _RECENT_COLLECTION_STATE.get("budget_last_error") or ""
            )
        aggregate_state = _aggregate_source_run_state(
            final_state,
            budget_run_state,
        )
        aggregate_error = _aggregate_source_errors(
            final_error,
            budget_last_error,
        )
        _set_recent_collection_state(
            state=aggregate_state,
            last_error=aggregate_error,
            last_finished_at=finished,
            last_status=aggregate_state,
            shopping_run_state=final_state,
            shopping_last_error=final_error,
            shopping_last_finished_at=finished,
            shopping_last_status=final_state,
        )
        print(
            "G2B_OPERATIONAL_SHOPPING",
            aggregate_state,
            shopping_state,
            flush=True,
        )
        return outcomes

    # 2) Budget: normalized QWGJK project state + bounded change evidence.
    budget_storage_module = None
    try:
        import budget_storage
        import lofin_vnext_http
        budget_storage_module = budget_storage
        budget_ready = budget_storage.storage_ready()
        lofin_ready = bool(lofin_vnext_http.get_lofin_key())
        lofin_quota = (
            lofin_vnext_http.daily_quota_status()
            if budget_ready and lofin_ready
            else {"limit": 0, "used": 0, "remaining": 0}
        )
    except Exception as exc:
        budget_ready = False
        lofin_ready = False
        lofin_quota = {"limit": 0, "used": 0, "remaining": 0}
        failures.append(("budget_prepare", type(exc).__name__))

    _set_recent_collection_state(
        lofin_quota_limit=int(lofin_quota.get("limit") or 0),
        lofin_quota_used=int(lofin_quota.get("used") or 0),
        lofin_quota_remaining=int(lofin_quota.get("remaining") or 0),
    )
    any_budget_collected = False
    classification_prechecked = False
    classification_drain_only = False

    # On the low-memory production worker, drain already-stored exact-current
    # classifications before touching LOFIN.  This gives operators a strict
    # invariant: a cycle that is reducing classification backlog performs zero
    # source requests.  The disposable child exits after one bounded pass so RSS
    # is released between passes; the automatic scheduler then retries PARTIAL
    # work until the stored backlog is empty.
    if (
        budget_ready
        and budget_storage_module is not None
        and budget_storage_module.using_postgres()
        and _isolated_heavy_worker_mode()
    ):
        import classification_vnext
        classification_prechecked = True
        classification_results = [
            classification_vnext.classify_dataset(
                dataset,
                batch_size=500,
                max_batches=ISOLATED_BUDGET_CLASSIFY_MAX_BATCHES,
            )
            for dataset in (
                "budget",
                "budget_appropriation",
                "education_budget",
            )
        ]
        classification_rows_drained = sum(
            int(row.get("classified") or 0)
            for row in classification_results
        )
        classification_limit_reached = any(
            bool(row.get("batch_limit_reached"))
            for row in classification_results
        )
        classification_drain_only = bool(
            classification_rows_drained > 0
            or classification_limit_reached
        )
        outcomes["budget_incremental_classification"] = classification_results
        outcomes["budget_classification_source_free"] = True
        outcomes["budget_classification_pending"] = (
            classification_limit_reached
        )
        outcomes["budget_classification_rows_drained"] = (
            classification_rows_drained
        )
        outcomes["budget_classification_drain_only"] = (
            classification_drain_only
        )
        if classification_drain_only:
            _set_recent_collection_state(budget_status="PARTIAL")

    if not budget_ready:
        _set_recent_collection_state(
            future_budget_status="WAITING_POSTGRES",
            current_appropriation_status="WAITING_POSTGRES",
            budget_status="WAITING_POSTGRES",
            budget_history_status="WAITING_POSTGRES",
        )
    elif classification_drain_only:
        # This child is classification-only by design.  Do not enter any LOFIN
        # collector while stored rows are being drained.
        pass
    elif not lofin_ready:
        _set_recent_collection_state(
            future_budget_status="WAITING_KEY",
            current_appropriation_status="WAITING_KEY",
            budget_status="WAITING_KEY",
            budget_history_status="WAITING_KEY",
        )
    elif int(lofin_quota.get("remaining") or 0) <= 0:
        outcomes["lofin_quota"] = lofin_quota
        _set_recent_collection_state(
            future_budget_status="WAITING_QUOTA",
            current_appropriation_status="WAITING_QUOTA",
            budget_status="WAITING_QUOTA",
            budget_history_status="WAITING_QUOTA",
        )
    else:
        import budget_appropriation_vnext
        import budget_reorganize_vnext
        import budget_vnext
        from vnext_source_guard import (
            MAX_OPERATIONAL_BUDGET_AGE_DAYS,
            operational_budget_source_context,
        )

        future_status = "NOT_STARTED"
        current_appropriation_status = "NOT_STARTED"
        current_status = "NOT_STARTED"
        cycle_request_budget = max(
            1,
            min(
                int(BUDGET_SYNC_MAX_REQUESTS),
                int(lofin_quota.get("remaining") or 0),
            ),
        )
        outcomes["lofin_quota_before"] = dict(lofin_quota)
        outcomes["lofin_cycle_request_budget"] = cycle_request_budget

        # Future budget gets first use of the remaining daily allowance.
        future_request_budget = max(
            1,
            min(
                int(FUTURE_BUDGET_SYNC_MAX_PAGES),
                cycle_request_budget,
            ),
        )
        try:
            with operational_budget_source_context(
                snapshot_date=today.isoformat(),
                max_requests=future_request_budget,
            ):
                future_budget = budget_appropriation_vnext.collect_full_appropriation(
                    today.year + 1,
                    page_size=1000,
                    max_pages=future_request_budget,
                    resume=True,
                    refresh_date=today.isoformat(),
                )
            outcomes["future_budget"] = future_budget
            future_status = str(future_budget.get("status") or "COMPLETE")
            any_budget_collected = True
            _set_recent_collection_state(
                future_budget_status=future_status
            )
        except memory_guard.MemoryPressureError:
            raise
        except Exception as exc:
            failures.append(("future_budget", type(exc).__name__))
            future_status = "FAILED"
            _set_recent_collection_state(
                future_budget_status="FAILED",
                last_error=f"FUTURE_BUDGET:{type(exc).__name__}",
            )

        # Current-year AIDFA is the fiscal-year baseline requested for the
        # Jan-1 budget scope. It is bounded separately from next-year AIDFA so
        # QWGJK current/history always retain quota.
        try:
            quota_after_future = lofin_vnext_http.daily_quota_status()
        except Exception:
            quota_after_future = {"limit": 0, "used": 0, "remaining": 0}
        outcomes["lofin_quota_after_future"] = dict(quota_after_future)

        current_appropriation_budget = max(
            0,
            min(
                int(CURRENT_APPROPRIATION_SYNC_MAX_PAGES),
                int(quota_after_future.get("remaining") or 0),
            ),
        )
        outcomes["current_appropriation_request_budget"] = (
            current_appropriation_budget
        )
        if current_appropriation_budget <= 0:
            current_appropriation_status = "WAITING_QUOTA"
            outcomes["current_appropriation"] = {
                "status": "WAITING_QUOTA",
                "complete": False,
                "reason": "LOFIN_DAILY_QUOTA_EXHAUSTED_AFTER_FUTURE_AIDFA",
            }
        else:
            try:
                with operational_budget_source_context(
                    snapshot_date=today.isoformat(),
                    max_requests=current_appropriation_budget,
                ):
                    current_appropriation = (
                        budget_appropriation_vnext.collect_full_appropriation(
                            today.year,
                            page_size=1000,
                            max_pages=current_appropriation_budget,
                            resume=True,
                            refresh_date=today.isoformat(),
                        )
                    )
                outcomes["current_appropriation"] = current_appropriation
                current_appropriation_status = str(
                    current_appropriation.get("status") or "COMPLETE"
                )
                any_budget_collected = True
                _set_recent_collection_state(
                    current_appropriation_status=current_appropriation_status
                )
            except memory_guard.MemoryPressureError:
                raise
            except Exception as exc:
                failures.append(("current_appropriation", type(exc).__name__))
                current_appropriation_status = "FAILED"
                _set_recent_collection_state(
                    current_appropriation_status="FAILED",
                    last_error=f"CURRENT_AIDFA:{type(exc).__name__}",
                )

        # Re-read the real quota after both AIDFA layers, then resume QWGJK.
        try:
            quota_after_appropriation = lofin_vnext_http.daily_quota_status()
        except Exception:
            quota_after_appropriation = {"limit": 0, "used": 0, "remaining": 0}
        outcomes["lofin_quota_after_current_appropriation"] = dict(
            quota_after_appropriation
        )
        remaining_permits = max(
            0,
            min(
                int(BUDGET_SYNC_MAX_REQUESTS),
                int(quota_after_appropriation.get("remaining") or 0),
            ),
        )

        # Keep current QWGJK first, but prevent the Jan-1 history backfill from
        # starving forever when the current nationwide snapshot consumes the whole
        # remaining daily quota. Reserve at most 25% (and the configured cap),
        # always leaving at least one permit for current-state progress.
        history_pending_before_current = None
        history_reserved_requests = 0
        current_request_budget = remaining_permits
        if (
            remaining_permits > 1
            and not TEST_MODE
            and budget_storage.using_postgres()
            and int(BUDGET_HISTORY_RESERVE_REQUESTS) > 0
        ):
            history_pending_before_current = (
                budget_vnext.next_historical_snapshot_date(
                    today=today,
                    retention_days=BUDGET_RETENTION_DAYS,
                )
            )
            if history_pending_before_current is not None:
                history_reserved_requests = min(
                    int(BUDGET_HISTORY_RESERVE_REQUESTS),
                    max(1, remaining_permits // 4),
                    remaining_permits - 1,
                )
                current_request_budget = (
                    remaining_permits - history_reserved_requests
                )

        budget_partition_fallback_active = False
        outcomes["budget_current_request_budget"] = current_request_budget
        outcomes["budget_history_reserved_requests"] = history_reserved_requests
        if history_pending_before_current is not None:
            outcomes["budget_history_next_date"] = (
                history_pending_before_current.isoformat()
            )

        try:
            if current_request_budget <= 0:
                current_status = "WAITING_QUOTA"
                outcomes["budget"] = {
                    "status": "WAITING_QUOTA",
                    "complete": False,
                    "reason": "LOFIN_DAILY_QUOTA_EXHAUSTED_AFTER_FUTURE_BUDGET",
                }
            else:
                latest_budget_day = budget_vnext.current_snapshot_date(
                    today=today
                )
                pending_day = budget_vnext.pending_nationwide_snapshot_date(
                    today=latest_budget_day
                )
                snapshot_day = pending_day or latest_budget_day
                outcomes["budget_snapshot_date"] = snapshot_day.isoformat()
                outcomes["budget_latest_source_safe_date"] = (
                    latest_budget_day.isoformat()
                )
                outcomes["budget_resume_pending"] = bool(pending_day)
                _set_recent_collection_state(
                    budget_snapshot_date=snapshot_day.isoformat()
                )
                budget_partition_fallback_active = (
                    budget_vnext.partition_fallback_required(
                        snapshot_day.year,
                        snapshot_day.isoformat(),
                    )
                )
                if budget_partition_fallback_active:
                    partition_plan = (
                        budget_vnext.operational_region_partition_plan(
                            snapshot_day.year
                        )
                    )
                    outcomes["budget_partition_fallback_plan"] = partition_plan
                    if not bool(partition_plan.get("ready")):
                        reason = str(
                            partition_plan.get("reason")
                            or "REGION_PARTITION_PLAN_NOT_READY"
                        )
                        budget = {
                            "status": "FAILED",
                            "complete": False,
                            "reason": reason,
                            "partition_fallback": True,
                            "source_collection_completeness_verified": False,
                        }
                        current_status = "FAILED"
                        failures.append(
                            ("budget", "REGION_PARTITION_PLAN_NOT_READY")
                        )
                        _set_recent_collection_state(
                            last_error=(
                                "BUDGET:REGION_PARTITION_PLAN_NOT_READY:"
                                + reason
                            )[:180],
                        )
                    else:
                        with operational_budget_source_context(
                            snapshot_date=snapshot_day.isoformat(),
                            max_requests=current_request_budget,
                        ):
                            partition = (
                                budget_vnext.collect_next_budget_region_partition(
                                    snapshot_day.year,
                                    snapshot_day.isoformat(),
                                    partition_plan.get("region_codes") or (),
                                    page_size=1000,
                                    max_pages=_budget_scope_page_limit(
                                        min(
                                            BUDGET_SYNC_MAX_PAGES,
                                            current_request_budget,
                                        )
                                    ),
                                    resume=True,
                                )
                            )
                        outcomes["budget_partition_fallback"] = partition
                        regional = dict(partition.get("result") or {})
                        if bool(partition.get("complete_for_planned_regions")):
                            import budget_pg_store
                            marker = (
                                budget_pg_store.mark_partition_complete_checkpoint(
                                    "budget",
                                    (
                                        f"{snapshot_day.year}:"
                                        f"{snapshot_day.isoformat()}"
                                    ),
                                    region_count=int(
                                        partition.get("region_count") or 0
                                    ),
                                )
                            )
                            outcomes["budget_partition_completion"] = marker
                            budget = {
                                "status": "COMPLETE",
                                "complete": True,
                                "reason": "REGION_PARTITION_PLAN_COMPLETE",
                                "partition_fallback": True,
                                "region_count": int(
                                    partition.get("region_count") or 0
                                ),
                                "source_collection_completeness_verified": False,
                            }
                            current_status = "COMPLETE"
                        elif bool(regional.get("drift_replay_exhausted")):
                            budget = {
                                **regional,
                                "partition_fallback": True,
                                "active_region": str(
                                    partition.get("active_region") or ""
                                ),
                            }
                            current_status = "FAILED"
                            failures.append(
                                ("budget", "REGION_OVERLAP_REPLAY_EXHAUSTED")
                            )
                            _set_recent_collection_state(
                                last_error=(
                                    "BUDGET:REGION_OVERLAP_REPLAY_EXHAUSTED:"
                                    + str(partition.get("active_region") or "")
                                )[:180],
                            )
                        else:
                            budget = {
                                **regional,
                                "status": "PARTIAL",
                                "complete": False,
                                "partition_fallback": True,
                                "active_region": str(
                                    partition.get("active_region") or ""
                                ),
                                "region_count": int(
                                    partition.get("region_count") or 0
                                ),
                                "completed_before": int(
                                    partition.get("completed_before") or 0
                                ),
                                "source_collection_completeness_verified": False,
                            }
                            current_status = "PARTIAL"
                            any_budget_collected = bool(regional)
                else:
                    with operational_budget_source_context(
                        snapshot_date=snapshot_day.isoformat(),
                        max_requests=current_request_budget,
                    ):
                        budget = budget_vnext.collect_full_budget(
                            snapshot_day.year,
                            snapshot_day.isoformat(),
                            page_size=1000,
                            max_pages=_budget_scope_page_limit(
                                min(
                                    BUDGET_SYNC_MAX_PAGES,
                                    current_request_budget,
                                )
                            ),
                            resume=True,
                            refresh_date=today.isoformat(),
                        )
                    if bool(budget.get("drift_replay_exhausted")):
                        current_status = "FAILED"
                        failures.append(
                            ("budget", "OVERLAP_REPLAY_EXHAUSTED")
                        )
                        _set_recent_collection_state(
                            last_error=(
                                "BUDGET:"
                                "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED"
                            ),
                        )
                    else:
                        current_status = str(
                            budget.get("status") or "COMPLETE"
                        )
                    any_budget_collected = True
                outcomes["budget"] = budget
        except memory_guard.MemoryPressureError:
            raise
        except Exception as exc:
            failures.append(("budget", type(exc).__name__))
            current_status = "FAILED"
            _set_recent_collection_state(
                last_error=f"BUDGET:{type(exc).__name__}",
            )

        history_window_days = min(
            int(BUDGET_RETENTION_DAYS),
            int(MAX_OPERATIONAL_BUDGET_AGE_DAYS),
        )
        history_window_start = max(
            _dt.date(2026, 1, 1),
            today - _dt.timedelta(days=history_window_days),
        )
        history_status = "COMPLETE" if TEST_MODE else "NOT_STARTED"
        history_results = []
        if (
            current_status not in {"FAILED", "WAITING_QUOTA"}
            and (
                not budget_partition_fallback_active
                or current_status == "COMPLETE"
            )
            and (
                not _isolated_heavy_worker_mode()
                or current_status == "COMPLETE"
            )
            and not TEST_MODE
            and budget_storage.using_postgres()
        ):
            try:
                quota_for_history = lofin_vnext_http.daily_quota_status()
                history_remaining = max(
                    0,
                    min(
                        int(BUDGET_SYNC_MAX_REQUESTS),
                        int(quota_for_history.get("remaining") or 0),
                    ),
                )
                history_days = 0
                while (
                    history_remaining > 0
                    and history_days < BUDGET_HISTORY_DAYS_PER_RUN
                ):
                    history_day = budget_vnext.next_historical_snapshot_date(
                        today=today
                    )
                    if history_day is None:
                        history_status = "COMPLETE"
                        break
                    with operational_budget_source_context(
                        snapshot_date=history_day.isoformat(),
                        max_requests=history_remaining,
                    ):
                        historical = budget_vnext.collect_full_budget(
                            history_day.year,
                            history_day.isoformat(),
                            page_size=1000,
                            max_pages=_budget_scope_page_limit(
                                min(
                                    BUDGET_SYNC_MAX_PAGES,
                                    history_remaining,
                                )
                            ),
                            resume=True,
                            advance_current=False,
                        )
                    history_results.append({
                        "snapshot_date": history_day.isoformat(),
                        **historical,
                    })
                    any_budget_collected = True
                    history_days += 1
                    if bool(historical.get("drift_replay_exhausted")):
                        history_status = "FAILED"
                        failures.append(
                            ("budget_history", "OVERLAP_REPLAY_EXHAUSTED")
                        )
                        _set_recent_collection_state(
                            last_error=(
                                "BUDGET_HISTORY:"
                                "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED"
                            ),
                        )
                        break
                    history_status = str(
                        historical.get("status") or "COMPLETE"
                    )
                    if historical.get("complete") is not True:
                        break
                    quota_for_history = lofin_vnext_http.daily_quota_status()
                    history_remaining = max(
                        0,
                        min(
                            int(BUDGET_SYNC_MAX_REQUESTS),
                            int(quota_for_history.get("remaining") or 0),
                        ),
                    )

                next_history = budget_vnext.next_historical_snapshot_date(
                    today=today
                )
                if next_history is None:
                    history_status = "COMPLETE"
                elif history_remaining <= 0:
                    history_status = "WAITING_QUOTA"
                elif history_status == "COMPLETE":
                    history_status = "PARTIAL"

                outcomes["budget_history"] = {
                    "status": history_status,
                    "start_date": history_window_start.isoformat(),
                    "latest_date": (today - _dt.timedelta(days=1)).isoformat(),
                    "results": history_results,
                }
                _set_recent_collection_state(
                    budget_history_status=history_status
                )
            except memory_guard.MemoryPressureError:
                raise
            except Exception as exc:
                failures.append(("budget_history", type(exc).__name__))
                history_status = "FAILED"
                _set_recent_collection_state(
                    budget_history_status="FAILED",
                    last_error=f"BUDGET_HISTORY:{type(exc).__name__}",
                )
        elif not TEST_MODE:
            history_status = (
                "WAITING_CURRENT"
                if current_status not in {"FAILED", "WAITING_QUOTA"}
                else current_status
            )
            _set_recent_collection_state(
                budget_history_status=history_status
            )

        if any_budget_collected and not _isolated_heavy_worker_mode():
            budget_reorganize_vnext.reorganize_existing_budget_raw()

        states = {
            future_status,
            current_appropriation_status,
            current_status,
            history_status,
        }
        combined_budget_status = _combined_budget_component_status(states)
        _set_recent_collection_state(
            budget_status=combined_budget_status
        )
        try:
            latest_quota = lofin_vnext_http.daily_quota_status()
            _set_recent_collection_state(
                lofin_quota_limit=int(latest_quota.get("limit") or 0),
                lofin_quota_used=int(latest_quota.get("used") or 0),
                lofin_quota_remaining=int(latest_quota.get("remaining") or 0),
            )
            outcomes["lofin_quota_after"] = latest_quota
        except Exception:
            pass

    # Exact-current classification is source-free maintenance. On low-memory
    # production it must continue even when LOFIN has no new pages, the key is
    # temporarily absent, or the daily quota is exhausted; otherwise rows left
    # pending at the end of collection can remain permanently unclassified.
    if (
        budget_ready
        and budget_storage_module is not None
        and budget_storage_module.using_postgres()
        and _isolated_heavy_worker_mode()
        and (not classification_prechecked or any_budget_collected)
    ):
        import classification_vnext
        classification_results = [
            classification_vnext.classify_dataset(
                dataset,
                batch_size=500,
                max_batches=ISOLATED_BUDGET_CLASSIFY_MAX_BATCHES,
            )
            for dataset in (
                "budget",
                "budget_appropriation",
                "education_budget",
            )
        ]
        outcomes["budget_incremental_classification"] = classification_results
        outcomes["budget_classification_source_free"] = (
            not any_budget_collected
        )
        outcomes["budget_classification_pending"] = any(
            bool(row.get("batch_limit_reached"))
            for row in classification_results
        )

    # Retention is a storage policy, not a source-collection success side effect.
    # Keep it running whenever PostgreSQL itself is available, even if the LOFIN key
    # is temporarily missing or the source request failed during this cycle.
    if budget_ready and budget_storage_module is not None:
        try:
            purged = budget_storage_module.purge_history(
                BUDGET_RETENTION_DAYS,
                receipt_retention_days=BUDGET_RECEIPT_RETENTION_DAYS,
            )
            outcomes["budget_retention"] = purged
            _set_recent_collection_state(budget_maintenance_warning="")
            if int(purged.get("expired_current_records") or 0) > 0:
                import budget_projection_vnext
                outcomes["budget_read_model_prune"] = (
                    budget_projection_vnext.prune_stale_budget_read_model()
                )
        except Exception as exc:
            warning = f"BUDGET_RETENTION:{type(exc).__name__}"
            outcomes["budget_retention_warning"] = warning
            _set_recent_collection_state(
                budget_maintenance_warning=warning,
            )

    finished = _dt.datetime.now(_ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    with _RECENT_COLLECTION_LOCK:
        shopping_state = str(
            _RECENT_COLLECTION_STATE.get("shopping_status") or ""
        )
        budget_state = str(
            _RECENT_COLLECTION_STATE.get("budget_status") or ""
        )
    if failures:
        state = "FAILED"
        error = ",".join(f"{name}:{kind}" for name, kind in failures)
    else:
        if source == "budget":
            component_states = (budget_state,)
        else:
            component_states = (shopping_state, budget_state)
        if component_states and all(
            value == "COMPLETE" for value in component_states
        ):
            state = "COMPLETE"
        elif "WAITING_POSTGRES" in component_states:
            state = "WAITING_STORAGE"
        elif "WAITING_KEY" in component_states:
            state = "WAITING_KEYS"
        elif "WAITING_QUOTA" in component_states:
            state = "WAITING_QUOTA"
        elif any(value in {"RUNNING", "PARTIAL", "INCOMPLETE"} for value in component_states):
            state = "PARTIAL"
        else:
            # Unknown/non-terminal component states must never be promoted to COMPLETE.
            state = "PARTIAL"
        error = ""

    source_updates = {}
    if run_shopping:
        shopping_failed = any(
            str(name).startswith("shopping") for name, _kind in failures
        )
        shopping_error = ",".join(
            f"{name}:{kind}"
            for name, kind in failures
            if str(name).startswith("shopping")
        )
        source_updates.update(
            shopping_run_state=_component_run_state(
                shopping_state, failed=shopping_failed
            ),
            shopping_last_error=shopping_error,
            shopping_last_finished_at=finished,
            shopping_last_status=_component_run_state(
                shopping_state, failed=shopping_failed
            ),
        )
    if run_budget:
        budget_failures = [
            (name, kind)
            for name, kind in failures
            if not str(name).startswith("shopping")
        ]
        budget_error = ",".join(
            f"{name}:{kind}" for name, kind in budget_failures
        )
        budget_run_state = _component_run_state(
            budget_state, failed=bool(budget_failures)
        )
        source_updates.update(
            budget_run_state=budget_run_state,
            budget_last_error=budget_error,
            budget_last_finished_at=finished,
            budget_last_status=budget_run_state,
        )

    if source == "budget":
        with _RECENT_COLLECTION_LOCK:
            shopping_run_state = str(
                _RECENT_COLLECTION_STATE.get("shopping_run_state") or "IDLE"
            )
            shopping_last_error = str(
                _RECENT_COLLECTION_STATE.get("shopping_last_error") or ""
            )
        state = _aggregate_source_run_state(
            shopping_run_state,
            source_updates["budget_run_state"],
        )
        error = _aggregate_source_errors(
            shopping_last_error,
            source_updates["budget_last_error"],
        )

    _set_recent_collection_state(
        state=state,
        last_error=error,
        last_finished_at=finished,
        last_status=state,
        **source_updates,
    )

    # Match generation is a derived read-model refresh. It must never make source
    # requests on its own. After an operational source cycle, refresh the latest
    # two fiscal years from already stored QWGJK + shopping rows when the compact
    # evidence is stale. The scheduler is singleton + time-throttled.
    if (
        not TEST_MODE
        and state in {"COMPLETE", "PARTIAL"}
        and _env_flag("G2B_MATCH_ROLLOVER_AUTO_ENABLE", False)
    ):
        try:
            outcomes["match_rollover_scheduled"] = bool(
                schedule_match_rollover()
            )
        except Exception as exc:
            outcomes["match_rollover_scheduled"] = False
            outcomes["match_rollover_schedule_error"] = type(exc).__name__
            print(
                "G2B_MATCH_ROLLOVER_SCHEDULE_ERROR",
                type(exc).__name__,
                flush=True,
            )
    elif not TEST_MODE:
        outcomes["match_rollover_scheduled"] = False

    print(
        "G2B_OPERATIONAL_SYNC",
        state,
        _RECENT_COLLECTION_STATE.get("shopping_status"),
        _RECENT_COLLECTION_STATE.get("budget_status"),
        flush=True,
    )
    return outcomes


def _budget_source_boundary_run_state(exc):
    """Map expected LOFIN request boundaries without hiding real source failures."""
    code = " ".join(str(exc or "").split()).strip()
    if code == "LOCAL_DAILY_QUOTA_REACHED":
        return "WAITING_QUOTA"
    if code != "VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED":
        return ""
    try:
        import lofin_vnext_http

        quota = lofin_vnext_http.daily_quota_status()
        if (
            int(quota.get("limit") or 0) > 0
            and int(quota.get("remaining") or 0) <= 0
        ):
            return "WAITING_QUOTA"
    except Exception:
        pass
    # A per-context request slice can end before the daily quota is exhausted.
    # That is resumable progress, not a source failure.
    return "PARTIAL"


def _run_recent_collection_once_locked(source="all"):
    """Run one selected source cycle after local memory/concurrency admission."""
    source = str(source or "all").strip().lower()
    if source not in {"all", "shopping", "budget"}:
        raise ValueError("UNSUPPORTED_OPERATIONAL_SOURCE")
    if TEST_MODE:
        return _run_recent_collection_once_impl(source=source)

    # Fast local guards avoid touching PostgreSQL when the control backend itself
    # is not ready or UNIFIED lost its persistent Cafe24 storage.
    if not backend_status().get("backend_ok"):
        return _run_recent_collection_once_impl(source=source)
    if is_unified() and not db_is_persistent():
        return _run_recent_collection_once_impl(source=source)

    lease_acquired = False

    def lease_held_result():
        lease_state = {
            "state": "IDLE",
            "last_status": "LEASE_HELD",
            "last_error": "",
        }
        if source == "shopping":
            lease_state.update(
                shopping_run_state="LEASE_HELD",
                shopping_last_status="LEASE_HELD",
                shopping_last_error="",
            )
        elif source == "budget":
            lease_state.update(
                budget_run_state="LEASE_HELD",
                budget_last_status="LEASE_HELD",
                budget_last_error="",
            )
        _set_recent_collection_state(**lease_state)
        print(
            "G2B_OPERATIONAL_SYNC_SKIPPED",
            "ACTIVE_PROCESS_LEASE",
            flush=True,
        )
        return {
            "shopping": None,
            "budget": None,
            "operational_cycle_lease": "HELD_BY_OTHER_PROCESS",
        }

    try:
        import g2b_database

        if source == "all":
            with g2b_database.operational_cycle_lease(
                "g2b_v41_operational_cycle",
                shared=False,
            ) as acquired:
                if not acquired:
                    return lease_held_result()
                lease_acquired = True
                return _run_recent_collection_once_impl(source=source)

        # Manual API-specific cycles need two advisory locks: a global shared gate
        # (to conflict with an automatic all-source cycle) plus a source-exclusive
        # gate (to prevent duplicate shopping/budget runs). Hold both on one
        # PostgreSQL session so the tiny 1+1 worker pool still has one connection
        # available for checkpoint/data work.
        with g2b_database.operational_source_cycle_lease(source) as acquired:
            if not acquired:
                return lease_held_result()
            lease_acquired = True
            return _run_recent_collection_once_impl(source=source)
    except Exception as exc:
        # Expected LOFIN request-budget boundaries are resumable. In particular,
        # the final permitted request can consume the 500th daily call and the
        # next page then hits the source-context boundary before the transport's
        # local quota guard. Do not misreport that normal stop as a worker error.
        if lease_acquired and source in {"all", "budget"}:
            boundary_state = _budget_source_boundary_run_state(exc)
            if boundary_state:
                budget_status = (
                    "WAITING_QUOTA"
                    if boundary_state == "WAITING_QUOTA"
                    else "PARTIAL"
                )
                _set_recent_collection_state(
                    state=boundary_state,
                    last_status=boundary_state,
                    last_error="",
                    budget_status=budget_status,
                    budget_run_state=boundary_state,
                    budget_last_status=boundary_state,
                    budget_last_error="",
                )
                print(
                    "G2B_OPERATIONAL_BUDGET_SOURCE_BOUNDARY",
                    boundary_state,
                    str(exc),
                    flush=True,
                )
                return {
                    "shopping": None,
                    "budget": {
                        "status": budget_status,
                        "complete": False,
                        "reason": str(exc),
                    },
                    "operational_cycle_lease": "SOURCE_BOUNDARY",
                }

        # Once the process lease has been acquired, every other failure belongs to
        # the cycle itself and must reach the existing worker-level safety net.
        if lease_acquired:
            raise
        failure_state = {
            "state": "WAITING_STORAGE",
            "last_status": "WAITING_STORAGE",
            "last_error": f"LEASE:{type(exc).__name__}",
        }
        if source in {"all", "budget"}:
            failure_state["budget_status"] = "WAITING_POSTGRES"
        if source == "shopping":
            failure_state.update(
                shopping_status="WAITING_STORAGE",
                shopping_run_state="WAITING_STORAGE",
                shopping_last_status="WAITING_STORAGE",
                shopping_last_error=f"LEASE:{type(exc).__name__}",
            )
        elif source == "budget":
            failure_state.update(
                budget_run_state="WAITING_STORAGE",
                budget_last_status="WAITING_STORAGE",
                budget_last_error=f"LEASE:{type(exc).__name__}",
            )
        _set_recent_collection_state(**failure_state)
        print(
            "G2B_OPERATIONAL_SYNC_LEASE_ERROR",
            type(exc).__name__,
            flush=True,
        )
        return {
            "shopping": None,
            "budget": None,
            "operational_cycle_lease": "UNAVAILABLE",
        }


def _memory_hold_result(source, reason):
    source = str(source or "all").strip().lower()
    state = {
        "state": "WAITING_MEMORY",
        "last_status": "WAITING_MEMORY",
        "last_error": str(reason),
    }
    if source in {"all", "shopping"}:
        state.update(
            shopping_run_state="WAITING_MEMORY",
            shopping_last_status="WAITING_MEMORY",
            shopping_last_error=str(reason),
        )
    if source in {"all", "budget"}:
        state.update(
            budget_run_state="WAITING_MEMORY",
            budget_last_status="WAITING_MEMORY",
            budget_last_error=str(reason),
        )
    _set_recent_collection_state(**state)
    return {
        "shopping": None,
        "budget": None,
        "operational_cycle_lease": str(reason),
    }


def _run_recent_collection_once(source="all"):
    """Admit at most one memory-heavy source cycle per process."""
    source = str(source or "all").strip().lower()
    if source not in {"all", "shopping", "budget"}:
        raise ValueError("UNSUPPORTED_OPERATIONAL_SOURCE")
    if TEST_MODE:
        return _run_recent_collection_once_locked(source=source)

    memory = memory_guard.snapshot(collect=True)
    if not memory.get("heavy_work_ok", memory["guard_ok"]):
        print(
            "G2B_MEMORY_GUARD_HOLD",
            source,
            memory["rss_mib"],
            memory["soft_limit_mib"],
            flush=True,
        )
        return _memory_hold_result(source, "MEMORY_PRESSURE")

    if not _HEAVY_WORK_LOCK.acquire(blocking=False):
        print("G2B_MEMORY_GUARD_BUSY", source, flush=True)
        return _memory_hold_result(source, "MEMORY_GUARD_HELD")

    try:
        return _run_recent_collection_once_locked(source=source)
    finally:
        _HEAVY_WORK_LOCK.release()


def _manual_collection_worker(source):
    source = str(source or "").strip().lower()
    current_thread = threading.current_thread()
    try:
        try:
            _run_recent_collection_once(source=source)
        except Exception as exc:
            error = f"{source.upper()}_WORKER:{type(exc).__name__}"
            source_state = {
                "state": "FAILED",
                "last_status": "FAILED",
                "last_error": error,
            }
            if source == "shopping":
                source_state.update(
                    shopping_run_state="FAILED",
                    shopping_last_status="FAILED",
                    shopping_last_error=error,
                )
            elif source == "budget":
                source_state.update(
                    budget_run_state="FAILED",
                    budget_last_status="FAILED",
                    budget_last_error=error,
                )
            _set_recent_collection_state(**source_state)
            print(
                "G2B_MANUAL_SOURCE_WORKER_ERROR",
                source,
                type(exc).__name__,
                flush=True,
            )
    finally:
        with _MANUAL_COLLECTION_LOCK:
            if _MANUAL_COLLECTION_THREADS.get(source) is current_thread:
                _MANUAL_COLLECTION_THREADS[source] = None


def schedule_manual_collection(source):
    source = str(source or "").strip().lower()
    if source not in {"shopping", "budget"}:
        raise ValueError("UNSUPPORTED_MANUAL_SOURCE")
    if not can_collect_sources():
        return False
    if not TEST_MODE and memory_guard.low_memory_web_hold():
        request_state = _request_isolated_source_worker(source)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if request_state == "STARTED":
            _set_source_collection_state(
                source,
                run_state="RUNNING",
                last_status="RUNNING",
                last_error="",
                last_started_at=now,
            )
            _set_recent_collection_state(
                state="RUNNING",
                last_status="RUNNING",
                last_error="",
                last_started_at=now,
            )
            return True
        if request_state == "QUEUED":
            _set_source_collection_state(
                source,
                run_state="QUEUED",
                last_status="QUEUED",
                last_error="",
                last_started_at=now,
            )
            _set_recent_collection_state(
                state="RUNNING",
                last_status="RUNNING",
                last_error="",
            )
            return True
        if request_state == "ALREADY":
            return False
        _memory_hold_result(source, "LOW_MEMORY_WORKER_UNSAFE")
        print(
            "G2B_MANUAL_SOURCE_256MB_WORKER_HOLD",
            source,
            flush=True,
        )
        return False
    with _MANUAL_COLLECTION_LOCK:
        existing = _MANUAL_COLLECTION_THREADS.get(source)
        if existing is not None and (
            existing.is_alive()
            or getattr(existing, "ident", None) is None
        ):
            return False
        thread = threading.Thread(
            target=_manual_collection_worker,
            args=(source,),
            name=f"g2b-v41-manual-{source}",
            daemon=True,
        )
        _MANUAL_COLLECTION_THREADS[source] = thread
        try:
            thread.start()
        except Exception:
            if _MANUAL_COLLECTION_THREADS.get(source) is thread:
                _MANUAL_COLLECTION_THREADS[source] = None
            raise
    return True


def _set_match_backfill_state(**values):
    with _MATCH_BACKFILL_LOCK:
        _MATCH_BACKFILL_STATE.update(values)


def _match_rollover_years(today=None):
    """Return the previous + current KST fiscal years for compact evidence."""
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    if today is None:
        day = _dt.datetime.now(_ZoneInfo("Asia/Seoul")).date()
    elif isinstance(today, _dt.datetime):
        day = today.date()
    elif isinstance(today, _dt.date):
        day = today
    else:
        day = _dt.date.fromisoformat(str(today)[:10])
    current = int(day.year)
    return tuple(year for year in (current - 1, current) if year > 0)


def _match_rollover_refresh_due(*, now=None):
    """Refresh when either rollover year is missing or older than six hours."""
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo
    import budget_shopping_match_store

    current = now or _dt.datetime.now(_ZoneInfo("Asia/Seoul"))
    if isinstance(current, _dt.date) and not isinstance(current, _dt.datetime):
        current = _dt.datetime.combine(
            current,
            _dt.time.min,
            tzinfo=_ZoneInfo("Asia/Seoul"),
        )
    if current.tzinfo is None:
        current = current.replace(tzinfo=_ZoneInfo("Asia/Seoul"))

    try:
        for year in _match_rollover_years(current):
            rows = budget_shopping_match_store.match_run_rows(
                fiscal_year=int(year),
                region="",
                limit=1,
            )
            if not rows:
                return True
            stamp = str(rows[0].get("updated_at") or "").strip()
            if not stamp:
                return True
            try:
                updated = _dt.datetime.fromisoformat(
                    stamp.replace("Z", "+00:00")
                )
            except ValueError:
                return True
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=_dt.timezone.utc)
            age = (
                current.astimezone(_dt.timezone.utc)
                - updated.astimezone(_dt.timezone.utc)
            ).total_seconds()
            if age >= MATCH_ROLLOVER_REFRESH_SECONDS:
                return True
    except Exception:
        # A missing/old schema is itself a reason to let the worker initialize it.
        return True
    return False


def _durable_match_backfill_snapshot():
    """Recover dynamic rollover evidence + the legacy 2025 bootstrap progress."""
    import budget_shopping_match_store

    years = _match_rollover_years()
    legacy_active = 2025 in years
    history_window = (
        budget_shopping_match_store.pattern_history_years(
            target_fiscal_year=max(years) + 1,
            region="",
            window=len(years),
        )
        if years
        else {"population_years": []}
    )
    plan = {
        "shopping_complete": True,
        "shopping_complete_days": 0,
        "shopping_total_days": 0,
        "shopping_next_date": "",
        "budget_complete": True,
    }
    if legacy_active:
        import budget_match_backfill_vnext
        plan = budget_match_backfill_vnext.build_2025_backfill_plan(
            {"expand_2025_recommended": True}
        )

    def run_info(year):
        rows = budget_shopping_match_store.match_run_rows(
            fiscal_year=int(year),
            region="",
            limit=1,
        )
        if not rows:
            return {"exists": False, "count": 0, "updated_at": ""}
        row = rows[0]
        count = int(row.get("high_matches") or 0) + int(
            row.get("candidate_matches") or 0
        )
        return {
            "exists": True,
            "count": count,
            "updated_at": str(row.get("updated_at") or ""),
        }

    run_info_by_year = {year: run_info(year) for year in years}
    persisted = {
        str(year): int(info["count"])
        for year, info in run_info_by_year.items()
    }
    updated_stamps = [
        str(info["updated_at"])
        for info in run_info_by_year.values()
        if str(info["updated_at"])
    ]
    pattern_stamp = max(updated_stamps) if updated_stamps else ""
    all_runs_exist = bool(years) and all(
        bool(info["exists"]) for info in run_info_by_year.values()
    )
    any_run_exists = any(
        bool(info["exists"]) for info in run_info_by_year.values()
    )
    legacy_complete = (
        not legacy_active
        or (
            bool(plan.get("budget_complete"))
            and bool(plan.get("shopping_complete"))
        )
    )
    legacy_progress = bool(
        legacy_active
        and (
            bool(plan.get("budget_complete"))
            or int(plan.get("shopping_complete_days") or 0) > 0
        )
    )

    if all_runs_exist and legacy_complete:
        durable_state = "COMPLETE"
    elif any_run_exists or legacy_progress:
        durable_state = "PARTIAL"
    else:
        durable_state = "IDLE"

    return {
        "state": durable_state,
        "rollover_years": list(years),
        "population_complete_years": list(
            history_window.get("population_years") or []
        ),
        "persisted_matches_by_year": persisted,
        "patterns_updated_at": pattern_stamp,
        "legacy_backfill_active": legacy_active,
        "shopping_complete_days": int(
            plan.get("shopping_complete_days") or 0
        ),
        "shopping_total_days": int(
            plan.get("shopping_total_days") or 0
        ),
        "shopping_next_date": str(
            plan.get("shopping_next_date") or ""
        ),
        "budget_complete": bool(plan.get("budget_complete")),
        "persisted_2026_matches": int(persisted.get("2026") or 0),
        "persisted_2025_matches": int(persisted.get("2025") or 0),
    }


def match_backfill_status():
    with _MATCH_BACKFILL_LOCK:
        runtime = dict(_MATCH_BACKFILL_STATE)

    try:
        durable = _durable_match_backfill_snapshot()
    except Exception:
        return runtime

    # RUNNING / quota / error are live runtime states. Durable persisted compact
    # evidence wins for year counters after redeploy/restart.
    merged = dict(runtime)
    merged["rollover_years"] = list(
        durable.get("rollover_years")
        or runtime.get("rollover_years")
        or []
    )
    runtime_map = {
        str(key): int(value or 0)
        for key, value in dict(
            runtime.get("persisted_matches_by_year") or {}
        ).items()
    }
    durable_map = {
        str(key): int(value or 0)
        for key, value in dict(
            durable.get("persisted_matches_by_year") or {}
        ).items()
    }
    merged_map = {}
    for key in set(runtime_map) | set(durable_map):
        merged_map[key] = max(
            int(runtime_map.get(key) or 0),
            int(durable_map.get(key) or 0),
        )
    merged["persisted_matches_by_year"] = merged_map
    merged["population_complete_years"] = sorted({
        int(value)
        for value in (
            list(runtime.get("population_complete_years") or [])
            + list(durable.get("population_complete_years") or [])
        )
        if int(value) > 0
    })
    merged["persisted_2026_matches"] = int(merged_map.get("2026") or 0)
    merged["persisted_2025_matches"] = int(merged_map.get("2025") or 0)

    runtime_days = int(runtime.get("shopping_complete_days") or 0)
    durable_days = int(durable.get("shopping_complete_days") or 0)
    merged["shopping_complete_days"] = max(runtime_days, durable_days)
    merged["shopping_total_days"] = max(
        int(runtime.get("shopping_total_days") or 0),
        int(durable.get("shopping_total_days") or 0),
    )
    if (
        runtime_days >= durable_days
        and str(runtime.get("state") or "IDLE") != "IDLE"
        and str(runtime.get("shopping_next_date") or "")
    ):
        merged["shopping_next_date"] = str(
            runtime.get("shopping_next_date") or ""
        )
    else:
        merged["shopping_next_date"] = str(
            durable.get("shopping_next_date")
            or runtime.get("shopping_next_date")
            or ""
        )
    merged["budget_complete"] = bool(
        runtime.get("budget_complete")
        or durable.get("budget_complete")
    )
    merged["legacy_backfill_active"] = bool(
        runtime.get("legacy_backfill_active")
        or durable.get("legacy_backfill_active")
    )
    merged["pattern_count"] = max(
        int(runtime.get("pattern_count") or 0),
        int(durable.get("pattern_count") or 0),
    )
    merged["patterns_updated_at"] = max(
        str(runtime.get("patterns_updated_at") or ""),
        str(durable.get("patterns_updated_at") or ""),
    )
    isolated = _isolated_heavy_worker_status()
    if isolated.get("running") and str(isolated.get("kind") or "").startswith("match"):
        merged["state"] = "RUNNING"
        merged["last_result_status"] = "RUNNING"
        merged["isolated_worker"] = dict(isolated)
    elif str(runtime.get("state") or "IDLE") == "IDLE":
        merged["state"] = str(durable.get("state") or "IDLE")
        merged["last_result_status"] = merged["state"]
        merged["isolated_worker"] = dict(isolated)
    else:
        merged["isolated_worker"] = dict(isolated)
    return merged


def _match_backfill_worker(allow_legacy_backfill=False):
    """Refresh compact evidence; legacy source backfill is explicit/manual only."""
    global _MATCH_BACKFILL_THREAD
    current_thread = threading.current_thread()
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    memory = memory_guard.snapshot(collect=True)
    if not memory.get("heavy_work_ok", memory["guard_ok"]):
        _set_match_backfill_state(
            state="WAITING_MEMORY",
            last_result_status="WAITING_MEMORY",
            last_error="MEMORY_PRESSURE",
        )
        with _MATCH_BACKFILL_LOCK:
            if _MATCH_BACKFILL_THREAD is current_thread:
                _MATCH_BACKFILL_THREAD = None
        print(
            "G2B_MATCH_ROLLOVER_MEMORY_HOLD",
            memory["rss_mib"],
            memory["soft_limit_mib"],
            flush=True,
        )
        return

    heavy_slot = _HEAVY_WORK_LOCK.acquire(blocking=False)
    if not heavy_slot:
        _set_match_backfill_state(
            state="WAITING_MEMORY",
            last_result_status="WAITING_MEMORY",
            last_error="MEMORY_GUARD_HELD",
        )
        with _MATCH_BACKFILL_LOCK:
            if _MATCH_BACKFILL_THREAD is current_thread:
                _MATCH_BACKFILL_THREAD = None
        print("G2B_MATCH_ROLLOVER_MEMORY_GUARD_HELD", flush=True)
        return

    now = _dt.datetime.now(_ZoneInfo("Asia/Seoul")).isoformat(
        timespec="seconds"
    )
    years = _match_rollover_years()
    _set_match_backfill_state(
        state="RUNNING",
        last_error="",
        last_started_at=now,
        rollover_years=list(years),
        legacy_backfill_active=2025 in years,
    )
    try:
        import budget_match_backfill_vnext
        import budget_shopping_match_store
        import budget_shopping_match_vnext

        summaries = {}
        saved_by_year = {}
        population_complete_years = []
        for year in sorted(years, reverse=True):
            summary = budget_shopping_match_vnext.historical_match_summary(
                fiscal_year=int(year),
                region="",
                categories=("LIGHTING", "POLE"),
                budget_limit=300,
                shopping_limit=5000,
                candidates_per_project=3,
                full_population=True,
            )
            summaries[int(year)] = summary
            if bool(summary.get("match_population_complete")):
                population_complete_years.append(int(year))
            saved = budget_shopping_match_store.save_match_summary(summary)
            saved_by_year[str(year)] = int(
                saved.get("saved_matches") or 0
            )

        legacy_result = None
        legacy_after = {}
        if allow_legacy_backfill and 2025 in years:
            gate_year = max(years)
            legacy_result = budget_match_backfill_vnext.run_2025_backfill(
                summaries.get(gate_year) or {},
                shopping_days=7,
                shopping_max_pages=40,
                budget_max_pages=100,
            )
            legacy_after = dict(legacy_result.get("after") or {})
            if (
                bool(legacy_after.get("budget_complete"))
                and int(legacy_after.get("shopping_complete_days") or 0) > 0
            ):
                summary_2025 = (
                    budget_shopping_match_vnext.historical_match_summary(
                        fiscal_year=2025,
                        region="",
                        categories=("LIGHTING", "POLE"),
                        budget_limit=300,
                        shopping_limit=5000,
                        candidates_per_project=3,
                        full_population=True,
                    )
                )
                summaries[2025] = summary_2025
                if (
                    bool(summary_2025.get("match_population_complete"))
                    and 2025 not in population_complete_years
                ):
                    population_complete_years.append(2025)
                saved_2025 = (
                    budget_shopping_match_store.save_match_summary(
                        summary_2025
                    )
                )
                saved_by_year["2025"] = int(
                    saved_2025.get("saved_matches") or 0
                )

        # Force the full chain through institution-pattern aggregation. Patterns
        # remain derived/read-only and are not copied into another source table.
        patterns = budget_shopping_match_store.organization_patterns(
            fiscal_years=years,
            region="",
            min_score=80,
            limit=200,
        )

        status = (
            "COMPLETE"
            if len(set(population_complete_years)) >= len(set(years))
            else "PARTIAL"
        )
        if legacy_result is not None:
            legacy_status = str(legacy_result.get("status") or "")
            if legacy_status in {"FAILED", "WAITING_QUOTA"}:
                status = legacy_status
            elif legacy_status == "PARTIAL":
                status = "PARTIAL"

        pattern_stamp = _dt.datetime.now(
            _ZoneInfo("Asia/Seoul")
        ).isoformat(timespec="seconds")
        _set_match_backfill_state(
            state=status,
            last_result_status=status,
            rollover_years=list(years),
            population_complete_years=sorted(
                set(population_complete_years)
            ),
            persisted_matches_by_year=dict(saved_by_year),
            pattern_count=len(patterns),
            patterns_updated_at=pattern_stamp,
            legacy_backfill_active=2025 in years,
            shopping_complete_days=int(
                legacy_after.get("shopping_complete_days") or 0
            ),
            shopping_total_days=int(
                legacy_after.get("shopping_total_days") or 0
            ),
            shopping_next_date=str(
                legacy_after.get("shopping_next_date") or ""
            ),
            budget_complete=(
                bool(legacy_after.get("budget_complete"))
                if 2025 in years
                else True
            ),
            persisted_2026_matches=int(
                saved_by_year.get("2026") or 0
            ),
            persisted_2025_matches=int(
                saved_by_year.get("2025") or 0
            ),
        )
    except Exception as exc:
        name = type(exc).__name__
        state = "WAITING_QUOTA" if "Quota" in name else "FAILED"
        _set_match_backfill_state(
            state=state,
            last_result_status=state,
            last_error=f"{name}",
        )
        print(
            "G2B_MATCH_ROLLOVER_WORKER_ERROR",
            name,
            flush=True,
        )
    finally:
        finished = _dt.datetime.now(_ZoneInfo("Asia/Seoul")).isoformat(
            timespec="seconds"
        )
        _set_match_backfill_state(last_finished_at=finished)
        _HEAVY_WORK_LOCK.release()
        with _MATCH_BACKFILL_LOCK:
            if _MATCH_BACKFILL_THREAD is current_thread:
                _MATCH_BACKFILL_THREAD = None


def schedule_match_rollover(*, force=False, allow_legacy_backfill=False):
    """Start one nonblocking stored-data compact-evidence rollover refresh."""
    global _MATCH_BACKFILL_THREAD
    if not can_collect_sources():
        return False
    if not TEST_MODE and memory_guard.low_memory_web_hold():
        mode = "match-legacy" if allow_legacy_backfill else "match"
        if _spawn_isolated_heavy_worker(mode):
            _set_match_backfill_state(
                state="RUNNING",
                last_result_status="RUNNING",
                last_error="",
                last_started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            return True
        _set_match_backfill_state(
            state="WAITING_MEMORY",
            last_result_status="WAITING_MEMORY",
            last_error="LOW_MEMORY_WORKER_BUSY_OR_UNSAFE",
        )
        print("G2B_MATCH_ROLLOVER_256MB_WORKER_HOLD", flush=True)
        return False
    if not force and not _match_rollover_refresh_due():
        return False
    with _MATCH_BACKFILL_LOCK:
        existing = _MATCH_BACKFILL_THREAD
        if existing is not None and (
            existing.is_alive()
            or getattr(existing, "ident", None) is None
        ):
            return False
        thread = threading.Thread(
            target=_match_backfill_worker,
            args=(bool(allow_legacy_backfill),),
            name="g2b-v41-match-rollover",
            daemon=True,
        )
        _MATCH_BACKFILL_THREAD = thread
        try:
            thread.start()
        except Exception:
            if _MATCH_BACKFILL_THREAD is thread:
                _MATCH_BACKFILL_THREAD = None
            raise
    return True


def schedule_match_backfill_2025():
    """Compatibility alias: only this explicit path may call 2025 source APIs."""
    return schedule_match_rollover(
        force=True,
        allow_legacy_backfill=True,
    )


def _seconds_until_next_kst_date(now=None):
    """Return a small positive delay ending just after the next KST midnight."""
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    kst = _ZoneInfo("Asia/Seoul")
    current = now or _dt.datetime.now(kst)
    if current.tzinfo is None:
        current = current.replace(tzinfo=kst)
    else:
        current = current.astimezone(kst)
    next_day = current.date() + _dt.timedelta(days=1)
    midnight = _dt.datetime.combine(next_day, _dt.time.min, tzinfo=kst)
    return max(1, int((midnight - current).total_seconds()) + 1)


def _run_low_memory_automatic_cycle():
    """Queue shopping then budget as one serialized isolated automatic batch.

    The long-lived web process never executes source-heavy work on the 256 MiB
    tier.  The existing isolated-worker supervisor guarantees one child at a time,
    and each child resumes durable checkpoints from the previous attempt.
    """
    scheduled = {}
    for source in ("shopping", "budget"):
        if not _auto_sync_enabled():
            break
        scheduled[source] = bool(schedule_manual_collection(source))

    # Wait only in this lightweight scheduler thread until the serialized batch is
    # drained. This lets the next wake decision observe real quota/memory states
    # instead of blindly retrying every two hours while a child is still running.
    while _auto_sync_enabled():
        isolated = _isolated_heavy_worker_status()
        if (
            not bool(isolated.get("running"))
            and not list(isolated.get("pending") or [])
        ):
            break
        time.sleep(1.0)

    return {
        "operational_cycle_lease": "ISOLATED_AUTOMATIC",
        "isolated_automatic": True,
        "scheduled": scheduled,
    }


def _automatic_cycle_wait_seconds(outcome=None, *, now=None):
    """Choose the next worker wake without wasting same-day quota retries."""
    lease_state = (
        str((outcome or {}).get("operational_cycle_lease") or "")
        if isinstance(outcome, dict)
        else ""
    )
    if lease_state in {"HELD_BY_OTHER_PROCESS", "UNAVAILABLE"}:
        return OPERATIONAL_LEASE_RETRY_SECONDS
    if lease_state in {"MEMORY_GUARD_HELD", "MEMORY_PRESSURE"}:
        return max(OPERATIONAL_LEASE_RETRY_SECONDS, 60)

    with _RECENT_COLLECTION_LOCK:
        source_states = (
            str(_RECENT_COLLECTION_STATE.get("shopping_run_state") or "IDLE"),
            str(_RECENT_COLLECTION_STATE.get("budget_run_state") or "IDLE"),
        )

    # If quota is the only remaining blocker, there is no benefit in repeating
    # the same source checks every two hours. Both local quota namespaces roll at
    # the KST date boundary, and an explicit wake/manual action can still interrupt
    # this sleep through _RECENT_COLLECTION_WAKE.
    if (
        "WAITING_QUOTA" in source_states
        and all(state in {"COMPLETE", "WAITING_QUOTA"} for state in source_states)
    ):
        return _seconds_until_next_kst_date(now)

    if "WAITING_MEMORY" in source_states:
        return max(OPERATIONAL_LEASE_RETRY_SECONDS, 60)

    if "LEASE_HELD" in source_states:
        return OPERATIONAL_LEASE_RETRY_SECONDS

    if "PARTIAL" in source_states:
        return max(
            OPERATIONAL_LEASE_RETRY_SECONDS,
            PARTIAL_PROGRESS_RETRY_SECONDS,
        )

    return SHOPPING_SYNC_INTERVAL_SECONDS


def _recent_collection_worker():
    global _RECENT_COLLECTION_THREAD
    current_thread = threading.current_thread()
    try:
        while True:
            outcome = None
            try:
                if (
                    not TEST_MODE
                    and memory_guard.low_memory_web_hold()
                ):
                    outcome = _run_low_memory_automatic_cycle()
                else:
                    outcome = _run_recent_collection_once()
            except Exception as exc:
                # A single unexpected cycle failure must not permanently kill automatic
                # collection. Source-specific failures are normally handled inside the
                # cycle; this is the final worker-level safety net.
                _set_recent_collection_state(
                    state="FAILED",
                    last_status="FAILED",
                    last_error=f"WORKER:{type(exc).__name__}",
                )
                print(
                    "G2B_OPERATIONAL_SYNC_WORKER_ERROR",
                    type(exc).__name__,
                    flush=True,
                )

            # force=True can still run a manual one-shot on roles where automatic
            # collection is disabled (for example LOCAL_COLLECTOR compatibility).
            if not _auto_sync_enabled():
                return

            # Lease conflicts retry quickly. When quota is the only blocker,
            # wake just after the next KST date boundary so the preserved checkpoint
            # resumes promptly after the daily counter resets.
            wait_seconds = _automatic_cycle_wait_seconds(outcome)

            # The event is a wake-up signal, not a queued extra run. A click while a
            # cycle is already active is satisfied by that active cycle and is consumed
            # here; a click while sleeping wakes the worker immediately.
            _RECENT_COLLECTION_WAKE.clear()
            _RECENT_COLLECTION_WAKE.wait(wait_seconds)
    finally:
        with _RECENT_COLLECTION_LOCK:
            if _RECENT_COLLECTION_THREAD is current_thread:
                _RECENT_COLLECTION_THREAD = None


def schedule_recent_collection(*, force=False):
    global _RECENT_COLLECTION_THREAD
    if not can_collect_sources():
        return False
    if not force and not _auto_sync_enabled():
        return False
    with _RECENT_COLLECTION_LOCK:
        existing = _RECENT_COLLECTION_THREAD
        if existing is not None and (
            existing.is_alive()
            or getattr(existing, "ident", None) is None
        ):
            # Passive startup/re-initialization must never pull the next source
            # cycle forward. Only an explicit manual force request wakes the
            # existing singleton worker.
            if force and existing.is_alive():
                _RECENT_COLLECTION_WAKE.set()
            return False

        # Never let a stale wake flag make a newly-created worker run two cycles
        # back-to-back. A new worker executes one cycle immediately by design.
        _RECENT_COLLECTION_WAKE.clear()
        thread = threading.Thread(
            target=_recent_collection_worker,
            name="g2b-v4-operational-sync",
            daemon=True,
        )
        _RECENT_COLLECTION_THREAD = thread
        # Start while the singleton lock is still held. Otherwise another caller
        # can observe the assigned Thread before start(), see is_alive()==False,
        # and create a duplicate worker.
        try:
            thread.start()
        except Exception:
            if _RECENT_COLLECTION_THREAD is thread:
                _RECENT_COLLECTION_THREAD = None
            raise
    return True


@asynccontextmanager
async def lifespan(_app):
    # Critical deployment invariant: HTTP startup does not wait for SQLite.
    schedule_backend_init()
    yield


app = FastAPI(title="SINSUNG G2B vNext", version=APP_VERSION, lifespan=lifespan)


STYLE = """
:root{font-family:Inter,Pretendard,Arial,sans-serif;color:#172033;background:#f4f6f9}
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#172033}
a{color:inherit;text-decoration:none}.top{background:#111b31;color:white;padding:18px 22px}
.brand{font-size:22px;font-weight:900}.sub{opacity:.75;margin-top:5px;font-size:13px}
.nav{display:flex;gap:8px;overflow:auto;padding:12px 16px;background:white;border-bottom:1px solid #dde2ea}
.nav a{white-space:nowrap;padding:10px 16px;border-radius:999px;background:#eef1f5;font-weight:800}
.nav a.on{background:#14213d;color:white}.wrap{max-width:1440px;margin:auto;padding:18px}
.card{background:white;border:1px solid #dde2ea;border-radius:18px;padding:20px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.kpi{background:white;border:1px solid #dde2ea;border-radius:16px;padding:18px}
.kpi b{font-size:28px;display:block;margin-bottom:8px}.muted{color:#697386}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:11px;border-bottom:1px solid #e6e9ee;text-align:left;vertical-align:top}
th{background:#f7f8fa}.table{width:100%;overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch}
.shopping-table table{min-width:1190px;table-layout:fixed}
.shopping-table th{white-space:nowrap}
.shopping-table th:nth-child(1),.shopping-table td:nth-child(1){width:110px}
.shopping-table th:nth-child(2),.shopping-table td:nth-child(2){width:210px}
.shopping-table th:nth-child(3),.shopping-table td:nth-child(3){width:155px}
.shopping-table th:nth-child(4),.shopping-table td:nth-child(4){width:265px}
.shopping-table th:nth-child(5),.shopping-table td:nth-child(5){width:160px}
.shopping-table th:nth-child(6),.shopping-table td:nth-child(6){width:80px}
.shopping-table th:nth-child(7),.shopping-table td:nth-child(7){width:105px}
.shopping-table th:nth-child(8),.shopping-table td:nth-child(8){width:105px}
.shopping-table .text-cell{word-break:keep-all;overflow-wrap:anywhere;line-height:1.45}
.budget-table table{min-width:1320px;table-layout:fixed}
.budget-table th,.budget-table td{word-break:keep-all;overflow-wrap:break-word;vertical-align:top;line-height:1.5}
.budget-table th:nth-child(1),.budget-table td:nth-child(1){width:74px}
.budget-table th:nth-child(2),.budget-table td:nth-child(2){width:220px}
.budget-table th:nth-child(3),.budget-table td:nth-child(3){width:165px}
.budget-table th:nth-child(4),.budget-table td:nth-child(4){width:405px}
.budget-table th:nth-child(5),.budget-table td:nth-child(5){width:100px}
.budget-table th:nth-child(6),.budget-table td:nth-child(6){width:135px}
.budget-table th:nth-child(7),.budget-table td:nth-child(7){width:110px}
.budget-table th:nth-child(8),.budget-table td:nth-child(8){width:125px}
.budget-org{font-weight:800;margin-top:5px}.budget-region{display:inline-block;font-size:12px;font-weight:800;padding:3px 7px;border-radius:999px;background:#eef1f5}
.budget-type{display:inline-block;font-size:12px;font-weight:900;padding:5px 8px;border-radius:999px;background:#eef1f5;margin-bottom:6px}
.budget-type.detail{background:#eaf2ff;color:#214f9b}.budget-type.appropriation{background:#fff5cc;color:#765f00}.budget-type.education{background:#eaf8ef;color:#0d6b50}
.budget-project{font-size:15px;font-weight:900;line-height:1.4;margin-bottom:5px}.budget-meta{font-size:12px;color:#697386;line-height:1.5;margin-top:4px}
.budget-structure{display:grid;grid-template-columns:72px 1fr;gap:3px 8px;margin-top:6px;font-size:13px}.budget-structure b{font-size:12px;color:#697386}
.budget-linked{margin-top:10px;padding:9px 10px;border:1px solid #dde2ea;border-radius:10px;background:#f7f8fa}.budget-linked strong{font-size:12px}.budget-linked div{margin-top:5px;font-size:12px;line-height:1.45}
.budget-note{font-size:12px;color:#697386;margin-top:4px}.budget-section-note{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.budget-section-note span{font-size:12px;padding:6px 9px;border-radius:9px;background:#f7f8fa}
.budget-overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:14px 0}
.budget-overview-card{border:1px solid #dde2ea;border-radius:14px;padding:15px;background:#fff}
.budget-overview-card b{display:block;font-size:24px;margin-bottom:5px}.budget-overview-card small{color:#697386;line-height:1.4}
.budget-quick{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}.budget-quick a{padding:8px 11px;border-radius:999px;border:1px solid #cfd5df;background:#fff;font-size:13px;font-weight:800}
.budget-quick a.on{background:#14213d;color:#fff;border-color:#14213d}
.budget-current-mobile{display:none}.budget-project-card{border:1px solid #dde2ea;border-radius:14px;padding:14px;margin:10px 0;background:#fff}
.budget-project-card-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.budget-project-card h4{margin:6px 0 4px;font-size:17px;line-height:1.4}
.budget-project-org{font-size:13px;font-weight:800;color:#4e5969}.budget-project-dept{font-size:12px;color:#697386;margin-top:3px}
.budget-status-badge{display:inline-block;padding:5px 8px;border-radius:999px;font-size:12px;font-weight:900;background:#eef1f5;white-space:nowrap}
.budget-status-badge.unexecuted{background:#fff5cc;color:#765f00}.budget-status-badge.partial{background:#eaf2ff;color:#214f9b}.budget-status-badge.full{background:#eaf8ef;color:#0d6b50}
.budget-sales-badge{display:inline-block;margin-top:7px;padding:4px 7px;border-radius:8px;background:#eaf8ef;color:#0d6b50;font-size:12px;font-weight:900}
.budget-money-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:11px}.budget-money{background:#f7f8fa;border-radius:9px;padding:9px}.budget-money b{display:block;font-size:15px}.budget-money small{color:#697386}
.budget-exec-bar{height:7px;background:#eef1f5;border-radius:999px;overflow:hidden;margin-top:10px}.budget-exec-bar span{display:block;height:100%;background:#177d68}
.budget-tech summary{cursor:pointer;font-weight:900}.budget-tech[open] summary{margin-bottom:12px}
.budget-history-table table{min-width:1510px;table-layout:fixed}.budget-history-table th,.budget-history-table td{word-break:keep-all;overflow-wrap:break-word;vertical-align:top;line-height:1.45}
.budget-history-table th:nth-child(1),.budget-history-table td:nth-child(1){width:105px}.budget-history-table th:nth-child(2),.budget-history-table td:nth-child(2){width:210px}.budget-history-table th:nth-child(3),.budget-history-table td:nth-child(3){width:150px}.budget-history-table th:nth-child(4),.budget-history-table td:nth-child(4){width:340px}.budget-history-table th:nth-child(5),.budget-history-table td:nth-child(5){width:140px}.budget-history-table th:nth-child(6),.budget-history-table td:nth-child(6){width:140px}.budget-history-table th:nth-child(7),.budget-history-table td:nth-child(7){width:140px}.budget-history-table th:nth-child(8),.budget-history-table td:nth-child(8){width:175px}
.change-up{font-weight:800}.change-down{font-weight:800}.change-flat{color:#697386}
.btn,button{display:inline-block;border:1px solid #26334d;border-radius:9px;padding:10px 14px;background:white;font-weight:800;cursor:pointer}
button.primary,.primary{background:#14213d;color:white}.notice{background:#fff5cc;border:1px solid #e6d481;border-radius:12px;padding:14px;margin:12px 0;line-height:1.55}
.ok{background:#eaf8ef;border:1px solid #9bd4ac}.bad{background:#fff0f0;border:1px solid #e6aaaa}
form.row{display:flex;gap:10px;flex-wrap:wrap;align-items:end}label{font-weight:700}input,select{display:block;margin-top:6px;padding:10px;border:1px solid #c7ccd4;border-radius:8px;min-width:150px}
.auth{max-width:440px;margin:8vh auto;background:white;border:1px solid #dde2ea;border-radius:18px;padding:28px}.auth input{width:100%}.auth button{width:100%;margin-top:14px}
.actions{display:flex;gap:8px;flex-wrap:wrap}.right{float:right}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#eef1f5;font-size:12px;font-weight:800}
.num{text-align:right;white-space:nowrap}.nowrap{white-space:nowrap}.wide{min-width:260px}
.stage-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.stage-card{background:white;border:1px solid #dde2ea;border-radius:16px;padding:17px}
.stage-head{display:flex;justify-content:space-between;gap:10px;align-items:start;margin-bottom:12px}
.stage-title{font-weight:900;font-size:17px}.stage-number{font-size:12px;color:#697386;font-weight:800}
.stage-state{display:inline-block;padding:5px 9px;border-radius:999px;font-size:12px;font-weight:900;background:#eef1f5}
.stage-state.running{background:#e9f8f2;color:#0d6b50}.stage-state.complete{background:#eaf2ff;color:#214f9b}
.stage-state.failed,.stage-state.incomplete,.stage-state.stale{background:#fff0f0;color:#a62626}
.stage-state.hold,.stage-state.not-started,.stage-state.idle{background:#fff5cc;color:#765f00}
.stage-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:12px 0}
.stage-metric{background:#f7f8fa;border-radius:10px;padding:9px}.stage-metric b{display:block;font-size:16px}.stage-metric small{color:#697386}
.progress{height:8px;background:#eef1f5;border-radius:999px;overflow:hidden}.progress>span{display:block;height:100%;background:#177d68}
.stage-message{font-size:13px;line-height:1.45;color:#4e5969;margin-top:10px;min-height:38px}
.live-gate{font-size:11px;font-weight:800;color:#697386;margin-top:8px}
.partition-panel{margin:11px 0;padding:12px;border:1px solid #cfe3dc;border-radius:12px;background:#f2faf7}
.partition-title{font-size:13px;font-weight:900;margin-bottom:8px}
.partition-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}
.partition-item{background:#fff;border-radius:9px;padding:9px}
.partition-item b{display:block;font-size:15px}.partition-item small{color:#697386}
.partition-sub{margin-top:8px;font-size:12px;color:#4e5969;line-height:1.45}
.collection-recent-mobile{display:none}
.collection-recent-desktop table{min-width:1080px;table-layout:fixed}
.collection-recent-desktop th{white-space:nowrap}
.collection-recent-desktop th,.collection-recent-desktop td{word-break:keep-all;overflow-wrap:anywhere;line-height:1.45}
.collection-recent-desktop th:nth-child(1),.collection-recent-desktop td:nth-child(1){width:190px}
.collection-recent-desktop th:nth-child(2),.collection-recent-desktop td:nth-child(2){width:210px}
.collection-recent-desktop th:nth-child(3),.collection-recent-desktop td:nth-child(3){width:190px}
.collection-recent-desktop th:nth-child(4),.collection-recent-desktop td:nth-child(4){width:90px}
.collection-recent-desktop th:nth-child(5),.collection-recent-desktop td:nth-child(5){width:76px}
.collection-recent-desktop th:nth-child(6),.collection-recent-desktop td:nth-child(6){width:86px}
.collection-recent-desktop th:nth-child(7),.collection-recent-desktop td:nth-child(7){width:238px}
.collection-activity-card{border:1px solid #dde2ea;border-radius:14px;padding:14px;margin:10px 0;background:#fff}
.collection-activity-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.collection-activity-title{font-weight:900;font-size:15px;line-height:1.4;word-break:keep-all}
.collection-activity-status{flex:0 0 auto;display:inline-block;padding:5px 9px;border-radius:999px;background:#eef1f5;font-size:12px;font-weight:900}
.collection-activity-status.complete{background:#eaf2ff;color:#214f9b}
.collection-activity-status.running{background:#e9f8f2;color:#0d6b50}
.collection-activity-status.failed,.collection-activity-status.incomplete,.collection-activity-status.stale{background:#fff0f0;color:#a62626}
.collection-activity-status.idle,.collection-activity-status.not-started{background:#fff5cc;color:#765f00}
.collection-activity-range{margin-top:8px;font-size:13px;font-weight:700;line-height:1.45;word-break:keep-all;overflow-wrap:anywhere}
.collection-activity-time{margin-top:4px;font-size:12px;color:#697386;overflow-wrap:anywhere}
.collection-activity-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}
.collection-activity-metric{background:#f7f8fa;border-radius:10px;padding:9px}
.collection-activity-metric b{display:block;font-size:16px}.collection-activity-metric small{color:#697386}
.collection-activity-error{margin-top:10px;padding:10px;border-radius:10px;background:#fff0f0;color:#8f2424;font-size:12px;line-height:1.5;overflow-wrap:anywhere;word-break:break-word}
@media(max-width:1024px){
.collection-recent-desktop{display:none}
.collection-recent-mobile{display:block}
.budget-current-desktop{display:none}
.budget-current-mobile{display:block}
}
@media(max-width:640px){
.wrap{padding:10px}.card{padding:14px}.top{padding:14px}.brand{font-size:19px}th,td{padding:9px;font-size:12px}
.collection-activity-card{padding:13px}
.collection-activity-title{font-size:14px}
.collection-activity-range{font-size:12px}
}
"""


def _backend_warmup_html(state=None):
    state = dict(state or {})
    attempts = int(state.get("attempts") or 0)
    detail = _public_error(state.get("backend_error")) or "STORAGE_STARTING"
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='refresh' content='2'>"
        "<title>SINSUNG G2B vNext 시작 중</title>"
        "<style>"
        "body{margin:0;background:#f4f6f9;color:#172033;font-family:Inter,Pretendard,Arial,sans-serif}"
        ".wrap{max-width:620px;margin:12vh auto;padding:20px}"
        ".card{background:#fff;border:1px solid #dde2ea;border-radius:18px;padding:28px}"
        ".bar{height:8px;background:#eef1f5;border-radius:999px;overflow:hidden;margin:18px 0}"
        ".bar span{display:block;width:42%;height:100%;background:#177d68;animation:p 1.1s ease-in-out infinite alternate}"
        "@keyframes p{from{transform:translateX(-35%)}to{transform:translateX(170%)}}"
        ".muted{color:#697386;line-height:1.6}"
        "</style></head><body><main class='wrap'><section class='card'>"
        "<h2>G2B vNext 시작 중</h2>"
        "<div class='bar'><span></span></div>"
        "<p class='muted'>웹 서버는 연결되었습니다. PostgreSQL 저장소를 백그라운드에서 준비 중이며, 완료되면 이 화면이 자동으로 전환됩니다.</p>"
        f"<p class='muted'>초기화 확인 {attempts}회 · {esc(detail)}</p>"
        "</section></main></body></html>"
    )


def _backend_is_warming(state):
    state = dict(state or {})
    return bool(
        not state.get("backend_ok")
        and (
            state.get("initializing")
            or not state.get("initialized")
            or not str(state.get("backend_error") or "").strip()
        )
    )


@app.middleware("http")
async def backend_gate(request: Request, call_next):
    # Platform liveness/root probes must never wait on persistent storage.
    if request.url.path not in {"/", "/health", "/__ai_space_health", "/live", "/ready"}:
        state = backend_status()
        if not state["backend_ok"]:
            schedule_backend_init()
            state = backend_status()
            browser_get = bool(
                request.method.upper() == "GET"
                and not request.url.path.startswith("/api/")
            )
            if browser_get and _backend_is_warming(state):
                return _secure(
                    HTMLResponse(
                        _backend_warmup_html(state),
                        status_code=200,
                        headers={"Retry-After": "2"},
                    )
                )
            return _secure(
                HTMLResponse(
                    "<h2>G2B vNext 저장소 초기화 실패</h2>"
                    "<p>웹 프로세스는 살아 있지만 데이터 저장소 준비가 완료되지 않았습니다.</p>"
                    f"<pre>{esc(_public_error(state.get('backend_error')) or 'STORAGE_NOT_READY')}</pre>",
                    status_code=503,
                )
            )
        if not TEST_MODE and not db_is_persistent():
            return _secure(
                HTMLResponse(
                    "<h2>G2B vNext 영구 저장소 연결 필요</h2>"
                    "<p>운영 모드에서는 비영구 임시 DB로 관리자·API 키·수집자료를 저장하지 않습니다.</p>",
                    status_code=503,
                )
            )
    return _secure(await call_next(request))


def _session_token(request: Request):
    return str(request.cookies.get(SESSION_COOKIE, "") or "")


def current_user(request: Request):
    return session_user(_session_token(request))


def require_user(request: Request):
    return current_user(request)


def csrf_token(request: Request, path: str):
    token = _session_token(request)
    if not token:
        return ""
    return hashlib.sha256(f"{token}|{path}|g2b-vnext-csrf-v1".encode("utf-8")).hexdigest()


def csrf_input(request: Request, path: str):
    return f'<input type="hidden" name="_csrf" value="{esc(csrf_token(request, path))}">'


def valid_csrf(request: Request, path: str, supplied):
    expected = csrf_token(request, path)
    value = str(supplied or "")
    return bool(expected and value and secrets.compare_digest(expected, value))


def _client_ip(request: Request):
    return str(request.client.host if request.client else "unknown")


def _login_keys(request: Request, username):
    normalized_user = " ".join(str(username or "").casefold().split()) or "<empty>"
    return (f"ip:{_client_ip(request)}", f"user:{normalized_user}")


def _login_allowed(keys):
    now = time.time()
    with _LOGIN_LOCK:
        for key in keys:
            recent = [
                stamp
                for stamp in _LOGIN_FAILURES.get(key, [])
                if now - stamp < LOGIN_WINDOW_SECONDS
            ]
            _LOGIN_FAILURES[key] = recent
            if len(recent) >= LOGIN_MAX_FAILURES:
                return False
        return True


def _login_failed(keys):
    stamp = time.time()
    with _LOGIN_LOCK:
        for key in keys:
            _LOGIN_FAILURES.setdefault(key, []).append(stamp)


def _login_success(keys):
    with _LOGIN_LOCK:
        for key in keys:
            _LOGIN_FAILURES.pop(key, None)


def layout(title, body, active="", user=None, refresh_seconds=None):
    navs = [
        ("대시보드", "/dashboard"),
        ("예산·영업후보", "/budget"),
        ("LED 조명", "/shopping?category=LIGHTING"),
        ("등주", "/shopping?category=POLE"),
        ("업체·단가 분석", "/vendors"),
        ("수집 상태", "/collection-monitor"),
        ("설정", "/settings"),
    ]
    nav = "".join(
        f'<a class="{"on" if name==active else ""}" href="{href}">{name}</a>'
        for name, href in navs
    )
    user_html = ""
    if user:
        user_html = (
            f'<span class="right">{esc(user["username"])} · '
            '<a href="/logout">로그아웃</a></span>'
        )
    refresh_meta = (
        f'<meta http-equiv="refresh" content="{max(2, min(int(refresh_seconds), 60))}">'
        if refresh_seconds
        else ""
    )
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh_meta}
<title>{esc(title)} · SINSUNG G2B vNext</title><style>{STYLE}</style></head><body>
<header class="top"><div class="brand">SINSUNG · 신성라이텍 G2B vNext {esc(APP_VERSION)} {user_html}</div>
<div class="sub">미래예산 수집 → 기관·사업 정리 → 조명·등주 후보 · 사업자료 2026-01-01 이후</div></header>
<nav class="nav">{nav}</nav><main class="wrap">{body}</main></body></html>"""
    )


async def form_data(request: Request):
    raw = (await request.body()).decode("utf-8", "replace")
    return {k: (v[0] if v else "") for k, v in parse_qs(raw).items()}


def _query_options(request: Request):
    q = str(request.query_params.get("q", "") or "").strip()
    category = str(request.query_params.get("category", "") or "").upper().strip()
    categories = (category,) if category in TARGET_CATEGORIES else TARGET_CATEGORIES
    try:
        limit = max(10, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200
    opts = ['<option value="">전체 대상</option>']
    for code in TARGET_CATEGORIES:
        selected = " selected" if category == code else ""
        opts.append(f'<option value="{code}"{selected}>{CATEGORY_LABELS[code]}</option>')
    return q, category, categories, limit, "".join(opts)


def _shopping_date_range(request, *, today=None):
    """Return an inclusive KST-year shopping date range, bounded by 2026-01-01."""
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo

    current = today or _dt.datetime.now(_ZoneInfo("Asia/Seoul")).date()
    floor = _dt.date(2026, 1, 1)
    default_start = max(floor, _dt.date(current.year, 1, 1))
    default_end = _dt.date(current.year, 12, 31)

    def parse(name, default):
        raw = str(request.query_params.get(name, "") or "").strip()
        if not raw:
            return default
        try:
            value = _dt.date.fromisoformat(raw)
        except ValueError:
            return default
        return max(floor, value)

    start = parse("start_date", default_start)
    end = parse("end_date", default_end)
    if start > end:
        start, end = default_start, default_end
    return start.isoformat(), end.isoformat()


def raw_counts():
    """Compatibility name: return counts from normalized 4.1 records only."""
    if not _BACKEND_STATE["backend_ok"]:
        return []

    rows = []
    try:
        import shopping_store_v41
        shopping = shopping_store_v41.count()
        rows.append({
            "dataset": "shopping_delivery",
            "n": int(shopping.get("active_records") or 0),
            "history_n": int(shopping.get("history_records") or 0),
            "inactive_n": int(shopping.get("inactive_records") or 0),
            "last_at": str(
                shopping.get("active_last_at")
                or shopping.get("last_at")
                or ""
            ),
        })
    except Exception:
        rows.append({
            "dataset": "shopping_delivery",
            "n": 0,
            "history_n": 0,
            "inactive_n": 0,
            "last_at": "STORAGE_UNAVAILABLE",
        })

    try:
        import budget_storage
        for dataset in sorted(budget_storage.BUDGET_DATASETS):
            try:
                counts = budget_storage.dataset_counts(dataset)
                current_records = int(counts.get("current_records") or 0)
                rows.append({
                    "dataset": dataset,
                    "n": current_records,
                    "history_n": current_records,
                    "inactive_n": 0,
                    "last_at": str(counts.get("last_seen_at") or ""),
                })
            except Exception:
                rows.append({
                    "dataset": dataset,
                    "n": 0,
                    "history_n": 0,
                    "inactive_n": 0,
                    "last_at": "POSTGRES_UNAVAILABLE",
                })
    except Exception:
        pass

    rows.sort(key=lambda row: str(row.get("dataset") or ""))
    return rows

def raw_total():
    return sum(int(row["n"] or 0) for row in raw_counts())


def target_dataset_counts():
    if not _BACKEND_STATE["backend_ok"]:
        return {}
    from vnext_schema import CLASSIFIER_VERSION
    result = {}
    try:
        import shopping_store_v41
        shopping_store_v41.ensure_schema()
        with connect() as conn:
            rows = conn.execute(
                """SELECT primary_category,COUNT(*) n
                   FROM shopping_records
                   WHERE is_active=1
                     AND primary_category IN ('LIGHTING','POLE')
                   GROUP BY primary_category"""
            ).fetchall()
        result["shopping_delivery"] = sum(int(row["n"] or 0) for row in rows)
    except Exception:
        result["shopping_delivery"] = 0

    try:
        import budget_storage
        if budget_storage.using_postgres():
            budget_names = tuple(sorted(budget_storage.BUDGET_DATASETS))
            for dataset in budget_names:
                result.pop(dataset, None)

            current_hashes = budget_storage.current_payload_hashes(budget_names)
            placeholders = ",".join("?" for _ in budget_names)
            with connect() as conn:
                classifications = conn.execute(
                    f"""SELECT entity_type,entity_key,primary_category,source_payload_sha256
                        FROM classifications
                        WHERE classifier_version=?
                          AND entity_type IN ({placeholders})""",
                    (CLASSIFIER_VERSION, *budget_names),
                ).fetchall()

            for dataset in budget_names:
                result[dataset] = 0
            for row in classifications:
                key = (str(row["entity_type"]), str(row["entity_key"]))
                if (
                    current_hashes.get(key, "")
                    == str(row["source_payload_sha256"] or "")
                    and str(row["primary_category"] or "").upper()
                    in {"LIGHTING", "POLE", "ELECTRICAL", "SOLAR"}
                ):
                    result[key[0]] = result.get(key[0], 0) + 1
    except Exception:
        pass

    return result


def _budget_postgres_readiness(*, probe=True):
    """Return budget readiness while keeping health/liveness network-free."""
    required = bool(not TEST_MODE and is_unified())
    database_source = ""
    if required:
        try:
            import g2b_database
            database_source = str(g2b_database.database_source_label() or "")
        except Exception:
            database_source = ""
    if not required:
        return {
            "required": False,
            "configured": False,
            "ready": True,
            "error_code": "",
            "database_source": "",
        }

    try:
        import budget_storage

        configured = bool(budget_storage.storage_configured())
        if not configured:
            result = {
                "required": True,
                "configured": False,
                "ready": False,
                "error_code": "BUDGET_POSTGRES_NOT_CONFIGURED",
                "database_source": "",
            }
            with _BUDGET_POSTGRES_PROBE_LOCK:
                _BUDGET_POSTGRES_PROBE_STATE.update(
                    configured=False,
                    ready=False,
                    error_code=result["error_code"],
                    checked_at=time.monotonic(),
                )
            return result

        if not probe:
            with _BUDGET_POSTGRES_PROBE_LOCK:
                cached = dict(_BUDGET_POSTGRES_PROBE_STATE)
            return {
                "required": True,
                "configured": True,
                "ready": bool(
                    cached["configured"] and cached["ready"]
                ),
                "error_code": str(
                    cached["error_code"]
                    or budget_storage.storage_error_code()
                    or ""
                ),
                "database_source": database_source,
            }

        ready = bool(budget_storage.storage_ready())
        result = {
            "required": True,
            "configured": True,
            "ready": ready,
            "error_code": str(
                budget_storage.storage_error_code() or ""
            ),
            "database_source": database_source,
        }
        with _BUDGET_POSTGRES_PROBE_LOCK:
            _BUDGET_POSTGRES_PROBE_STATE.update(
                configured=True,
                ready=ready,
                error_code=result["error_code"],
                checked_at=time.monotonic(),
            )
        return result
    except Exception as exc:
        result = {
            "required": True,
            "configured": True,
            "ready": False,
            "error_code": _public_error(type(exc).__name__),
            "database_source": database_source,
        }
        with _BUDGET_POSTGRES_PROBE_LOCK:
            _BUDGET_POSTGRES_PROBE_STATE.update(
                configured=True,
                ready=False,
                error_code=result["error_code"],
                checked_at=time.monotonic(),
            )
        return result


@app.get("/live")
def live():
    return {
        "status": "ok",
        "process_alive": True,
        "runtime": "G2B_VNEXT_CLEAN",
        "runtime_role": runtime_role(),
        "result_snapshot_active": result_snapshot_vnext.snapshot_available(),
        "version": APP_VERSION,
        **runtime_deployment_identity(),
    }


@app.get("/ready")
def ready():
    state = backend_status()
    if not state["backend_ok"]:
        schedule_backend_init()
        state = backend_status()
    persistent_ok = bool(TEST_MODE or db_is_persistent())
    budget_pg = _budget_postgres_readiness()
    operational_ready = bool(
        state["backend_ok"]
        and persistent_ok
        and (not budget_pg["required"] or budget_pg["ready"])
    )
    payload = {
        "status": "ready" if operational_ready else "not_ready",
        "backend_ok": state["backend_ok"],
        "backend_initializing": state["initializing"],
        "backend_error": _public_error(state["backend_error"]),
        "db_persistent": db_is_persistent(),
        "persistent_storage_required": not TEST_MODE,
        "budget_postgres_required": budget_pg["required"],
        "budget_postgres_configured": budget_pg["configured"],
        "budget_postgres_ready": budget_pg["ready"],
        "budget_postgres_error_code": budget_pg["error_code"],
        "database_source": str(budget_pg.get("database_source") or ""),
        "fresh_start_status": str(state.get("fresh_start_status") or ""),
        "fresh_start_marker_ok": bool(state.get("fresh_start_marker_ok")),
        "fresh_start_marker_value": str(
            state.get("fresh_start_marker_value") or ""
        ),
        "fresh_start_reset_performed": bool(
            state.get("fresh_start_reset_performed")
        ),
        "fresh_start_flag_enabled": str(
            os.getenv("G2B_V41_FRESH_START", "0") or "0"
        ).strip().lower() in {"1", "true", "yes", "on"},
        "operational_ready": operational_ready,
        "destructive_reset_confirmed": _env_flag(
            "G2B_DESTRUCTIVE_RESET_CONFIRM", False
        ),
        "runtime": "G2B_VNEXT_CLEAN",
        "version": APP_VERSION,
        **runtime_deployment_identity(),
    }
    return JSONResponse(payload, status_code=200 if operational_ready else 503)


@app.get("/__ai_space_health")
def ai_space_health():
    """Platform liveness only: never touch storage, readiness, or source state."""
    return {
        "status": "ok",
        "process_alive": True,
        "runtime": "G2B_VNEXT_CLEAN",
        "runtime_role": runtime_role(),
        "version": APP_VERSION,
        **runtime_deployment_identity(),
    }


@app.get("/health")
def health():
    state = backend_status()
    if not state["backend_ok"]:
        schedule_backend_init()
        state = backend_status()
    budget_pg = _budget_postgres_readiness(probe=False)
    operational_ready = bool(
        state["backend_ok"]
        and (TEST_MODE or db_is_persistent())
        and (not budget_pg["required"] or budget_pg["ready"])
    )
    return {
        "status": "ok",
        "process_alive": True,
        "backend_ok": state["backend_ok"],
        "backend_initializing": state["initializing"],
        "backend_error": _public_error(state["backend_error"]),
        "backend_init_attempts": state["attempts"],
        "runtime": "G2B_VNEXT_CLEAN",
        "runtime_role": runtime_role(),
        **_memory_status_fields(collect=False),
        "post_boot_maintenance_enabled": post_boot_maintenance_enabled(),
        "result_snapshot_active": result_snapshot_vnext.snapshot_available(),
        "version": APP_VERSION,
        **runtime_deployment_identity(),
        "db_path": current_db_path() if TEST_MODE else "",
        "db_persistent": db_is_persistent(),
        "persistent_storage_required": not TEST_MODE,
        "budget_postgres_required": budget_pg["required"],
        "budget_postgres_configured": budget_pg["configured"],
        "budget_postgres_ready": budget_pg["ready"],
        "budget_postgres_error_code": budget_pg["error_code"],
        "database_source": str(budget_pg.get("database_source") or ""),
        "fresh_start_status": str(state.get("fresh_start_status") or ""),
        "fresh_start_marker_ok": bool(state.get("fresh_start_marker_ok")),
        "fresh_start_marker_value": str(
            state.get("fresh_start_marker_value") or ""
        ),
        "fresh_start_reset_performed": bool(
            state.get("fresh_start_reset_performed")
        ),
        "fresh_start_flag_enabled": str(
            os.getenv("G2B_V41_FRESH_START", "0") or "0"
        ).strip().lower() in {"1", "true", "yes", "on"},
        "operational_ready": operational_ready,
        "destructive_reset_confirmed": _env_flag(
            "G2B_DESTRUCTIVE_RESET_CONFIRM", False
        ),
        "storage_backend": "POSTGRESQL_UNIFIED" if not TEST_MODE else "SQLITE_TEST",
        "required_boot_env": (
            [
                "G2B_DATABASE_URL",
                "DB_HOST+DB_NAME+DB_USER",
                "PGHOST+PGDATABASE+PGUSER",
                "POSTGRES_URL|POSTGRESQL_URL|DATABASE_URL",
            ]
            if budget_pg["required"] and not budget_pg["configured"]
            else []
        ),
    }


@app.get("/")
def root(request: Request):
    state = backend_status()
    if not state["backend_ok"]:
        schedule_backend_init()
        state = backend_status()
        return _secure(
            HTMLResponse(
                _backend_warmup_html(state),
                status_code=200,
                headers={"Retry-After": "2"},
            )
        )
    # Keep the platform root probe DB-free. /login resolves setup/session state.
    return RedirectResponse("/login", 302)


@app.get("/setup")
def setup_page(request: Request):
    if not users_empty():
        return RedirectResponse("/login", 302)
    error = request.query_params.get("error", "")
    flash = f'<div class="notice bad">{esc(error)}</div>' if error else ""
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>G2B vNext 관리자 설정</title><style>{STYLE}</style></head><body><section class="auth">
<h2>G2B vNext 최초 관리자</h2>
<p class="muted">관리자 계정이 아직 없을 때만 이 화면이 열립니다. 최초 관리자 1회 생성 후에는 로그인 화면으로 이동합니다.</p>
{flash}<form method="post" action="/setup">
<label>아이디<input name="username" minlength="4" required></label>
<label>비밀번호<input type="password" name="password" minlength="10" required></label>
<label>비밀번호 확인<input type="password" name="confirm" minlength="10" required></label>
<button class="primary">관리자 생성</button></form></section></body></html>"""
    )


@app.post("/setup")
async def setup_submit(request: Request):
    # Fresh-install bootstrap only: create_admin() itself allows exactly one first
    # administrator, and this route becomes unreachable once that row exists.
    # Avoid cookie-bound setup CSRF here because mobile/in-app browsers can drop
    # the setup cookie during the first deployment flow.
    if not users_empty():
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if data.get("password") != data.get("confirm"):
        return RedirectResponse("/setup?error=" + quote("비밀번호 확인이 일치하지 않습니다."), 302)
    try:
        create_admin(data.get("username"), data.get("password"))
    except ValueError as exc:
        return RedirectResponse("/setup?error=" + quote(str(exc)), 302)
    return RedirectResponse("/login", 302)


@app.get("/login")
def login_page(request: Request):
    if users_empty():
        return RedirectResponse("/setup", 302)
    if require_user(request):
        return RedirectResponse("/dashboard", 302)
    error = request.query_params.get("error", "")
    flash = f'<div class="notice bad">{esc(error)}</div>' if error else ""
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>G2B vNext 로그인</title><style>{STYLE}</style></head><body><section class="auth">
<h2>SINSUNG G2B vNext</h2><p class="muted">공공조달 데이터 플랫폼</p>{flash}
<form method="post" action="/login"><label>아이디<input name="username" required></label>
<label>비밀번호<input type="password" name="password" required></label>
<button class="primary">로그인</button></form></section></body></html>"""
    )


@app.post("/login")
async def login_submit(request: Request):
    data = await form_data(request)
    login_keys = _login_keys(request, data.get("username"))
    if not _login_allowed(login_keys):
        return HTMLResponse("로그인 실패가 반복되어 잠시 제한됩니다.", status_code=429)
    user = authenticate(data.get("username"), data.get("password"))
    if not user:
        _login_failed(login_keys)
        return RedirectResponse("/login?error=" + quote("아이디 또는 비밀번호를 확인해 주세요."), 302)
    _login_success(login_keys)
    token = create_session(user["username"])
    response = RedirectResponse("/dashboard", 302)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=not TEST_MODE,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/logout")
def logout(request: Request):
    delete_session(_session_token(request))
    response = RedirectResponse("/login", 302)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=not TEST_MODE,
        httponly=True,
        samesite="lax",
    )
    return response


def _dashboard_snapshot():
    """Best-effort dashboard data from compact snapshot or normalized local records."""
    warnings = []
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        meta = result_snapshot_vnext.snapshot_metadata()
        counts = meta.get("source_counts") if isinstance(meta.get("source_counts"), dict) else {}
        raw = counts.get("raw") if isinstance(counts.get("raw"), dict) else {}
        target = counts.get("target") if isinstance(counts.get("target"), dict) else {}
        history = (
            counts.get("history")
            if isinstance(counts.get("history"), dict)
            else raw
        )
        inactive = (
            counts.get("inactive")
            if isinstance(counts.get("inactive"), dict)
            else {}
        )
        readiness = meta.get("readiness") if isinstance(meta.get("readiness"), dict) else {}
        manifest = meta.get("manifest") if isinstance(meta.get("manifest"), dict) else {}
        return {
            "by_name": {str(k): int(v or 0) for k, v in raw.items()},
            "history_by_name": {str(k): int(v or 0) for k, v in history.items()},
            "inactive_by_name": {str(k): int(v or 0) for k, v in inactive.items()},
            "target": {str(k): int(v or 0) for k, v in target.items()},
            "total": sum(int(v or 0) for v in raw.values()),
            "history_total": sum(int(v or 0) for v in history.values()),
            "readiness": readiness or {
                "status": "RESULT_SNAPSHOT",
                "status_scope": "LOCAL_COLLECTOR_RESULT_ONLY",
            },
            "warnings": [],
            "snapshot_manifest": manifest,
        }
    try:
        counts = raw_counts()
    except Exception as exc:
        print("G2B_DASHBOARD_DATA_COUNTS_FAILED", type(exc).__name__, flush=True)
        counts = []
        warnings.append("자료 집계 일시 대기")
    by_name = {row["dataset"]: int(row["n"] or 0) for row in counts}
    history_by_name = {
        row["dataset"]: int(row.get("history_n", row["n"]) or 0)
        for row in counts
    }
    inactive_by_name = {
        row["dataset"]: int(row.get("inactive_n") or 0)
        for row in counts
    }

    try:
        target = target_dataset_counts()
    except Exception as exc:
        print("G2B_DASHBOARD_TARGET_COUNTS_FAILED", type(exc).__name__, flush=True)
        target = {}
        warnings.append("분류 집계 일시 대기")

    try:
        import readiness_vnext
        readiness = readiness_vnext.build_readiness_report()
    except Exception as exc:
        print("G2B_DASHBOARD_READINESS_FAILED", type(exc).__name__, flush=True)
        readiness = {
            "status": "TEMPORARILY_UNAVAILABLE",
            "status_scope": "READ_ONLY_DASHBOARD_FAILSOFT",
        }
        warnings.append("준비상태 집계 일시 대기")

    return {
        "by_name": by_name,
        "history_by_name": history_by_name,
        "inactive_by_name": inactive_by_name,
        "target": target,
        "total": sum(by_name.values()),
        "history_total": sum(history_by_name.values()),
        "readiness": readiness,
        "warnings": warnings,
    }


@app.get("/dashboard")
def dashboard(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    snapshot = _dashboard_snapshot()
    by_name = snapshot["by_name"]
    history_by_name = snapshot.get("history_by_name") or by_name
    inactive_by_name = snapshot.get("inactive_by_name") or {}
    target = snapshot["target"]
    total = snapshot["total"]
    readiness = snapshot["readiness"]
    warning_html = (
        '<div class="notice"><b>집계 일시 대기:</b> '
        + esc(" · ".join(snapshot["warnings"]))
        + ' · 수집은 계속 진행되며 잠시 후 새로고침하면 됩니다.</div>'
        if snapshot["warnings"] else ""
    )
    body = f"""
<section class="card"><h2>G2B vNext 대시보드</h2>
<div class="notice"><b>운영 원칙:</b> {esc("호환 RESULT_SERVER: 로컬 결과 스냅샷만 표시합니다." if is_result_server() else ("Cafe24 통합 운영: 예산은 정규화해 PostgreSQL에 저장하고, 사업자료는 2026-01-01 이후 전국 조명·등주만 저장합니다." if is_unified() else "호환 로컬 수집기 모드입니다."))}</div>
{warning_html}</section>
<div class="grid">
<div class="kpi"><b>{esc(APP_VERSION)}</b><span>운영 버전</span></div>
<div class="kpi"><b>{esc(build_commit_label())}</b><span>배포 HEAD</span><small>{'환경 SHA 확인' if runtime_build_commit() else 'G2B_BUILD_COMMIT 또는 GITHUB_SHA 필요'}</small></div>
<div class="kpi"><b>{'OK' if db_is_persistent() else '주의'}</b><span>영구 저장소</span></div>
<div class="kpi"><b>{total:,}</b><span>현재 유효 저장자료</span></div>
<div class="kpi"><b>{target.get('shopping_delivery',0):,}</b><span>현재 대상 납품요구</span></div>
<div class="kpi"><b>{history_by_name.get('shopping_delivery',0):,}</b><span>보존 납품요구 이력</span></div>
<div class="kpi"><b>{inactive_by_name.get('shopping_delivery',0):,}</b><span>비활성 납품요구 이력</span></div>
<div class="kpi"><b>{target.get('budget',0)+target.get('education_budget',0):,}</b><span>대상 예산사업</span></div>
</div>
<section class="card"><h3>수집 준비상태</h3>
<p><span class="pill">{esc(readiness.get("status"))}</span> · {esc(readiness.get("status_scope"))}</p>
<p class="muted">예산 정규화 자료와 2026-01-01 이후 조명·등주 사업자료만 운영수집합니다. 용역·입찰은 NO1 담당이며 bulk historical과 교육청 live transport는 HOLD입니다.</p>
<p><a class="btn" href="/collection-monitor">각 자료 수집 상태 확인</a></p></section>
"""
    return layout("대시보드", body, "대시보드", user)


def _collector_state_class(state):
    return {
        "RUNNING": "running",
        "COMPLETE": "complete",
        "FAILED": "failed",
        "INCOMPLETE": "incomplete",
        "STALE": "stale",
        "NOT_STARTED": "not-started",
        "IDLE": "idle",
        "PARTIAL": "running",
        "WAITING_QUOTA": "idle",
        "WAITING_KEYS": "idle",
        "WAITING_STORAGE": "idle",
        "WAITING_PERSISTENT_STORAGE": "idle",
        "LEASE_HELD": "idle",
    }.get(str(state or ""), "")


def _collector_stage_html(stage):
    pages = int(stage.get("pages_processed") or 0)
    total_pages = stage.get("total_pages")
    page_text = f"{pages:,} / {int(total_pages):,}" if total_pages else f"{pages:,}"
    percent = stage.get("percent")
    width = float(percent) if percent is not None else 0.0
    progress_label = f"{float(percent):.1f}%" if percent is not None else "총량 확인 중"
    error_text = (
        f'<div class="notice bad"><b>오류:</b> {esc(stage.get("last_error"))}</div>'
        if stage.get("last_error")
        else ""
    )
    aidfa_text = ""
    aidfa_years = list(stage.get("aidfa_years") or [])
    if aidfa_years:
        lines = []
        for item in aidfa_years:
            year = int(item.get("year") or 0)
            role = str(item.get("role") or "")
            state_label = str(item.get("state_label") or "")
            saved = int(item.get("saved_count") or 0)
            pages_done = int(item.get("pages_processed") or 0)
            total_pages = item.get("total_pages")
            pages_label = (
                f"{pages_done:,}/{int(total_pages):,}페이지"
                if total_pages
                else f"{pages_done:,}페이지"
            )
            lines.append(
                '<div class="muted" style="margin-top:6px">'
                f'<b>{year} {esc(role)}:</b> {esc(state_label)} · '
                f'{pages_label} · {saved:,}건</div>'
            )
        aidfa_text = "".join(lines)

    shopping_storage_metrics = ""
    current_storage_label = "현재 저장"
    if str(stage.get("dataset") or "") == "shopping_delivery":
        current_storage_label = "현재 유효"
        shopping_storage_metrics = (
            '<div class="stage-metric"><b>'
            f'{int(stage.get("history_count") or 0):,}'
            '</b><small>보존 이력</small></div>'
            '<div class="stage-metric"><b>'
            f'{int(stage.get("inactive_count") or 0):,}'
            '</b><small>비활성 이력</small></div>'
        )

    history_text = ""
    if int(stage.get("history_total_days") or 0) > 0:
        complete_days = int(stage.get("history_complete_days") or 0)
        total_days = int(stage.get("history_total_days") or 0)
        history_percent = float(stage.get("history_percent") or 0)
        history_start_date = str(
            stage.get("history_start_date") or "2026-01-01"
        )
        next_date = str(stage.get("history_next_date") or "")
        next_label = (
            "전체 완료"
            if not next_date
            else "다음 " + next_date
        )
        history_text = (
            '<div class="muted" style="margin-top:9px">'
            f'<b>예산이력 {esc(history_start_date)} → D-1:</b> '
            f'{complete_days:,} / {total_days:,}일 · '
            f'{history_percent:.1f}% · {esc(next_label)}</div>'
        )

    partition_text = ""
    if bool(stage.get("partition_mode")):
        total_regions = int(stage.get("partition_total_regions") or 0)
        complete_regions = int(stage.get("partition_complete_regions") or 0)
        partition_percent = float(stage.get("partition_percent") or 0)
        active_region = str(
            stage.get("partition_active_region_label") or "다음 지역 준비"
        )
        active_pages = int(stage.get("partition_active_pages") or 0)
        active_total_pages = stage.get("partition_active_total_pages")
        active_pages_label = (
            f"{active_pages:,}/{int(active_total_pages):,}"
            if active_total_pages else f"{active_pages:,}"
        )
        partition_text = (
            '<div class="partition-panel">'
            '<div class="partition-title">광역지역 분할수집</div>'
            '<div class="partition-grid">'
            f'<div class="partition-item"><b>{complete_regions:,} / {total_regions:,}</b>'
            '<small>완료 지역</small></div>'
            f'<div class="partition-item"><b>{esc(active_region)}</b>'
            '<small>현재 지역</small></div>'
            f'<div class="partition-item"><b>{partition_percent:.1f}%</b>'
            '<small>지역 진행률</small></div>'
            f'<div class="partition-item"><b>{esc(active_pages_label)}</b>'
            '<small>현재 지역 페이지</small></div>'
            '</div>'
            f'<div class="partition-sub">대상 기준일 · '
            f'{esc(stage.get("partition_snapshot_date") or "")} · '
            '전국 중첩페이지를 반복 호출하지 않고 지역별 checkpoint에서 자동 재개합니다.</div>'
            '</div>'
        )
    return f"""
<div class="stage-card">
  <div class="stage-head">
    <div><div class="stage-number">{esc(stage.get('number'))} · {esc(stage.get('group'))}</div>
    <div class="stage-title">{esc(stage.get('label'))}</div></div>
    <span class="stage-state {_collector_state_class(stage.get('state'))}">{esc(stage.get('state_label'))}</span>
  </div>
  <div class="progress"><span style="width:{max(0.0,min(width,100.0)):.1f}%"></span></div>
  <div class="stage-message">{esc(stage.get('message'))}</div>
  {partition_text}
  <div class="stage-metrics">
    <div class="stage-metric"><b>{page_text}</b><small>처리 페이지</small></div>
    <div class="stage-metric"><b>{int(stage.get('saved_count') or 0):,}</b><small>현재 실행 저장</small></div>
    <div class="stage-metric"><b>{int(stage.get('raw_count') or 0):,}</b><small>{esc(current_storage_label)}</small></div>
    {shopping_storage_metrics}
    <div class="stage-metric"><b>{esc(progress_label)}</b><small>진행률</small></div>
  </div>
  <div class="muted">최근 갱신: {esc(stage.get('last_activity') or '없음')}</div>
  <div class="muted">범위: {esc(stage.get('scope') or '실행 이력 없음')}</div>
  <div class="live-gate">live gate: {esc(stage.get('live_gate'))}</div>
  {aidfa_text}
  {history_text}
  {error_text}
</div>"""


def _source_quota_snapshot():
    """Read independent local source quotas without external API I/O."""
    result = {
        "shopping": {
            "used": 0, "limit": 0, "remaining": 0, "error": "",
        },
        "budget": {
            "used": 0, "limit": 0, "remaining": 0, "error": "",
        },
    }

    try:
        import vnext_http
        usage = dict(vnext_http.api_usage() or {})
        used = int(usage.get("total") or 0)
        limit = int(usage.get("limit") or 0)
        result["shopping"] = {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used),
            "error": "",
        }
    except Exception as exc:
        result["shopping"]["error"] = type(exc).__name__

    try:
        import lofin_vnext_http
        usage = dict(lofin_vnext_http.daily_quota_status() or {})
        used = int(usage.get("used") or 0)
        limit = int(usage.get("limit") or 0)
        result["budget"] = {
            "used": used,
            "limit": limit,
            "remaining": max(
                0,
                int(usage.get("remaining"))
                if usage.get("remaining") is not None
                else limit - used,
            ),
            "error": "",
        }
    except Exception as exc:
        result["budget"]["error"] = type(exc).__name__

    return result


def _apply_runtime_wait_states(snapshot, runtime_sources, source_quota):
    """Prefer explicit source wait states over stale resumable checkpoints."""
    data = dict(snapshot or {})
    stages = [dict(stage) for stage in (data.get("stages") or [])]
    wait_labels = {
        "WAITING_QUOTA": "호출한도대기",
        "WAITING_KEYS": "키대기",
        "WAITING_STORAGE": "저장소대기",
        "WAITING_PERSISTENT_STORAGE": "저장소대기",
        "WAITING_MEMORY": "메모리대기",
        "QUEUED": "대기열",
        "LEASE_HELD": "다른 프로세스 실행중",
    }
    source_state = {
        "shopping_delivery": str(
            (runtime_sources or {}).get("shopping_run_state") or ""
        ),
        "budget": str(
            (runtime_sources or {}).get("budget_run_state") or ""
        ),
        "budget_appropriation": str(
            (runtime_sources or {}).get("budget_run_state") or ""
        ),
    }
    for stage in stages:
        dataset = str(stage.get("dataset") or "")
        runtime_state = source_state.get(dataset, "")
        if (
            runtime_state in wait_labels
            and str(stage.get("state") or "") in {
                "RUNNING", "STALE", "PARTIAL", "INCOMPLETE"
            }
        ):
            stage["state"] = runtime_state
            stage["state_label"] = wait_labels[runtime_state]
            if runtime_state == "WAITING_QUOTA":
                quota_key = (
                    "shopping"
                    if dataset == "shopping_delivery"
                    else "budget"
                )
                quota = dict((source_quota or {}).get(quota_key) or {})
                used = int(quota.get("used") or 0)
                limit = int(quota.get("limit") or 0)
                stage["message"] = (
                    f"일일 API 호출한도 대기 · {used:,}/{limit:,}회 · "
                    "다음 KST 일자에 checkpoint부터 재개"
                )
                stage["last_error"] = ""
            elif runtime_state == "WAITING_KEYS":
                stage["message"] = "API 키 설정 대기"
                stage["last_error"] = ""
            elif runtime_state in {
                "WAITING_STORAGE", "WAITING_PERSISTENT_STORAGE"
            }:
                stage["message"] = "PostgreSQL 저장소 준비 대기"
            elif runtime_state == "WAITING_MEMORY":
                stage["message"] = (
                    "메모리 안전대기 · checkpoint 보존 · 자동 재개"
                )
                stage["last_error"] = ""
            elif runtime_state == "LEASE_HELD":
                stage["message"] = "다른 프로세스가 같은 수집을 실행 중"

    if stages:
        data["stages"] = stages
        summary = dict(data.get("summary") or {})
        summary["running"] = sum(
            str(stage.get("state") or "") == "RUNNING" for stage in stages
        )
        summary["complete"] = sum(
            str(stage.get("state") or "") == "COMPLETE" for stage in stages
        )
        summary["errors"] = sum(
            str(stage.get("state") or "")
            in {"FAILED", "INCOMPLETE", "STALE"}
            for stage in stages
        )
        summary["not_started"] = sum(
            str(stage.get("state") or "") == "NOT_STARTED" for stage in stages
        )
        data["summary"] = summary
    return data


def _runtime_collection_snapshot():
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        meta = result_snapshot_vnext.snapshot_metadata()
        return meta.get("collection_status") or {}
    import collection_monitor_vnext
    snapshot = dict(collection_monitor_vnext.monitor_snapshot())
    runtime_sources = recent_collection_status()
    snapshot["runtime_sources"] = runtime_sources
    if is_result_server():
        snapshot = dict(snapshot)
        snapshot["collection_controls_enabled"] = False
        operational = dict(snapshot.get("operational_recent") or {})
        operational["enabled_capability"] = False
        operational["runtime_role"] = "RESULT_SERVER"
        snapshot["operational_recent"] = operational
    else:
        # Local counters only; this performs no source-network I/O. Keep the two
        # source families separate in the API just as they are in the UI.
        source_quota = _source_quota_snapshot()
        snapshot["source_quota"] = source_quota
        snapshot = _apply_runtime_wait_states(
            snapshot,
            runtime_sources,
            source_quota,
        )
    return snapshot


def _collection_monitor_refresh_seconds(
    *,
    shopping_running=False,
    budget_running=False,
    match_backfill_running=False,
):
    """Poll quickly only while work is active; keep idle/error screens lightweight."""
    return 5 if any(
        (
            bool(shopping_running),
            bool(budget_running),
            bool(match_backfill_running),
        )
    ) else 30


@app.get("/collection-monitor")
def collection_monitor_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    snapshot = _runtime_collection_snapshot()
    runtime_sources = snapshot.get("runtime_sources") or {}
    source_quota = snapshot.get("source_quota") or {
        "shopping": {"used": 0, "limit": 0, "remaining": 0, "error": ""},
        "budget": {"used": 0, "limit": 0, "remaining": 0, "error": ""},
    }
    shopping_quota = source_quota["shopping"]
    budget_quota = source_quota["budget"]
    match_backfill = match_backfill_status()
    match_backfill_state = str(
        match_backfill.get("state") or "IDLE"
    )
    match_backfill_running = match_backfill_state == "RUNNING"
    shopping_running = bool(runtime_sources.get("manual_shopping_running"))
    budget_running = bool(runtime_sources.get("manual_budget_running"))
    auto_sync_enabled = bool(runtime_sources.get("auto_sync_enabled"))
    auto_thread_alive = bool(runtime_sources.get("thread_alive"))
    auto_sync_label = "ON" if auto_sync_enabled else "OFF"
    auto_worker_label = (
        "동작중"
        if auto_thread_alive
        else ("기동대기" if auto_sync_enabled else "중지")
    )
    auto_collection_notice = (
        '<div class="notice ok"><b>자동수집 ON:</b> '
        'UNIFIED 운영에서는 배포 후 PostgreSQL 준비가 끝나면 별도 클릭 없이 '
        '나라장터 → 지방재정365 순으로 checkpoint를 자동 재개합니다. '
        '아래 수동 버튼은 즉시 실행·점검용입니다.</div>'
        if auto_sync_enabled
        else
        '<div class="notice"><b>자동수집 OFF:</b> '
        '운영 UNIFIED에서 OFF이면 긴급중지 스위치(G2B_AUTO_SYNC_DISABLE) 또는 '
        'runtime role을 확인하십시오. 수동 버튼은 별도로 사용할 수 있습니다.</div>'
    )
    monitor_refresh_seconds = _collection_monitor_refresh_seconds(
        shopping_running=shopping_running,
        budget_running=budget_running,
        match_backfill_running=match_backfill_running,
    )
    source_state_labels = {
        "IDLE": "대기",
        "RUNNING": "실행중",
        "COMPLETE": "완료",
        "PARTIAL": "부분완료",
        "FAILED": "오류",
        "WAITING_KEYS": "키대기",
        "WAITING_STORAGE": "저장소대기",
        "WAITING_QUOTA": "호출한도대기",
        "WAITING_MEMORY": "메모리대기",
        "QUEUED": "대기열",
        "LEASE_HELD": "다른 프로세스 실행중",
    }
    shopping_run_state = str(
        runtime_sources.get("shopping_run_state") or "IDLE"
    )
    budget_run_state = str(
        runtime_sources.get("budget_run_state") or "IDLE"
    )
    shopping_run_label = source_state_labels.get(
        shopping_run_state, shopping_run_state
    )
    budget_run_label = source_state_labels.get(
        budget_run_state, budget_run_state
    )
    shopping_button = (
        '<button class="primary" disabled>나라장터 수집중…</button>'
        if shopping_running
        else '<button class="primary">나라장터 조명·등주 수집</button>'
    )
    budget_button = (
        '<button disabled>지방재정365 수집중…</button>'
        if budget_running
        else '<button>지방재정365 예산 수집</button>'
    )
    match_backfill_button = (
        '<button disabled>과거매칭 갱신중…</button>'
        if match_backfill_running
        else '<button>과거매칭 최근 2개 연도 갱신</button>'
    )
    match_years = [
        int(value)
        for value in (match_backfill.get("rollover_years") or [])
        if int(value) > 0
    ]
    match_years_label = " · ".join(str(year) for year in match_years) or "미확인"
    persisted_by_year = {
        str(key): int(value or 0)
        for key, value in dict(
            match_backfill.get("persisted_matches_by_year") or {}
        ).items()
    }
    match_evidence_label = (
        " · ".join(
            f"{year}년 {int(persisted_by_year.get(str(year)) or 0):,}건"
            for year in match_years
        )
        or "아직 없음"
    )
    population_years_label = (
        " · ".join(
            str(year)
            for year in (
                match_backfill.get("population_complete_years") or []
            )
        )
        or "없음"
    )
    legacy_match_html = ""
    if bool(match_backfill.get("legacy_backfill_active")):
        legacy_match_html = (
            f'<div class="kpi"><b>{int(match_backfill.get("shopping_complete_days") or 0):,} / '
            f'{int(match_backfill.get("shopping_total_days") or 365):,}</b>'
            '<span>2025 레거시 검증 백필 날짜</span></div>'
            f'<div class="kpi"><b>{esc(match_backfill.get("shopping_next_date") or "완료")}</b>'
            '<span>2025 레거시 다음 resume</span></div>'
        )
    summary = snapshot.get("summary") or {
        "running": 0, "complete": 0, "stage_count": 0,
        "errors": 0, "total_raw": 0, "last_activity": "",
    }
    stages = "".join(_collector_stage_html(stage) for stage in (snapshot.get("stages") or []))
    recent_activity = list(snapshot.get("recent_activity") or [])
    recent_rows = "".join(
        f"<tr><td>{esc(row['updated_at'])}</td><td>{esc(row['label'])}</td>"
        f"<td>{esc(row.get('scope_display') or row['scope'])}</td><td><span class='stage-state {_collector_state_class(row.get('status'))}'>{esc(row['status_label'])}</span></td>"
        f"<td class='num'>{int(row['pages_processed']):,}</td>"
        f"<td class='num'>{int(row['saved_count']):,}</td>"
        f"<td>{esc(row['last_error'])}</td></tr>"
        for row in recent_activity
    )
    recent_cards = "".join(
        "<article class='collection-activity-card'>"
        "<div class='collection-activity-head'>"
        f"<div class='collection-activity-title'>{esc(row['label'])}</div>"
        f"<span class='collection-activity-status {_collector_state_class(row.get('status'))}'>{esc(row['status_label'])}</span>"
        "</div>"
        f"<div class='collection-activity-range'>수집범위 · {esc(row.get('scope_display') or row['scope'] or '범위 미확인')}</div>"
        f"<div class='collection-activity-time'>갱신 · {esc(row['updated_at'] or '미확인')}</div>"
        "<div class='collection-activity-metrics'>"
        f"<div class='collection-activity-metric'><b>{int(row['pages_processed']):,}</b><small>페이지</small></div>"
        f"<div class='collection-activity-metric'><b>{int(row['saved_count']):,}</b><small>저장건수</small></div>"
        "</div>"
        + (
            f"<div class='collection-activity-error'><b>오류</b><br>{esc(row['last_error'])}</div>"
            if row.get("last_error") else ""
        )
        + "</article>"
        for row in recent_activity
    )
    body = f"""
<section class="card"><h2>공식자료 수집 상태</h2>
<p class="muted">실제 정규화 저장건수와 collection checkpoint를 기준으로 표시합니다. 이 화면 자체는 외부 API를 호출하거나 수집 범위를 변경하지 않습니다.</p>
<div class="notice"><b>자동 확인:</b> 수집 실행 중에는 5초, 대기·완료·오류 상태에서는 30초마다 새로고침합니다. RUNNING이 5분 이상 갱신되지 않으면 <b>갱신중단</b>으로 표시합니다.</div>
<div class="grid">
<div class="kpi"><b>{int(summary['running']):,}</b><span>현재 실행중</span></div>
<div class="kpi"><b>{int(summary['complete']):,} / {int(summary['stage_count']):,}</b><span>최근 완료 상태</span></div>
<div class="kpi"><b>{int(summary['errors']):,}</b><span>오류·중단 확인 필요</span></div>
<div class="kpi"><b>{int(summary['total_raw']):,}</b><span>모니터 대상 전체 저장건</span></div>
</div>
<p class="muted">전체 최근 활동: {esc(summary.get('last_activity') or '없음')}</p></section>
<section class="card"><h3>수집 실행</h3>
{(
'<div class="notice ok"><b>호환 결과서버:</b> 원천수집은 실행하지 않습니다.</div>'
if is_result_server()
else
auto_collection_notice
+ '<div class="notice ok"><b>API 분리:</b> 나라장터와 지방재정365는 서로 다른 API·키·호출한도를 사용합니다. 한 원천의 호출한도·오류가 다른 원천을 막지 않습니다.</div>'
+ '<div class="grid">'
+ f'<div class="kpi"><b>{esc(auto_sync_label)}</b><span>자동수집</span><small>UNIFIED 기본 ON · 긴급중지만 별도</small></div>'
+ f'<div class="kpi"><b>{esc(auto_worker_label)}</b><span>자동수집 worker</span><small>{"checkpoint 자동 재개" if auto_sync_enabled else "자동주기 중지"}</small></div>'
+ f'<div class="kpi"><b>{esc(shopping_run_label)}</b><span>나라장터 실행상태</span><small>{esc(runtime_sources.get("shopping_last_error") or "")}</small></div>'
+ f'<div class="kpi"><b>{esc(budget_run_label)}</b><span>지방재정365 실행상태</span><small>{esc(runtime_sources.get("budget_last_error") or (("유지보수 경고 · " + str(runtime_sources.get("budget_maintenance_warning"))) if runtime_sources.get("budget_maintenance_warning") else ""))}</small></div>'
+ f'<div class="kpi"><b>{shopping_quota["used"]:,} / {shopping_quota["limit"]:,}</b><span>나라장터 API 호출량</span><small>{"확인불가 · " + esc(shopping_quota["error"]) if shopping_quota["error"] else "잔여 " + format(shopping_quota["remaining"], ",") + "회"}</small></div>'
+ f'<div class="kpi"><b>{budget_quota["used"]:,} / {budget_quota["limit"]:,}</b><span>지방재정365 API 호출량</span><small>{"확인불가 · " + esc(budget_quota["error"]) if budget_quota["error"] else "잔여 " + format(budget_quota["remaining"], ",") + "회"}</small></div>'
+ '</div>'
'<div class="actions">'
'<form method="post" action="/collect/shopping-recent">'
+ csrf_input(request,'/collect/shopping-recent')
+ shopping_button
+ '</form>'
'<form method="post" action="/collect/budget">'
+ csrf_input(request,'/collect/budget')
+ budget_button
+ '</form>'
'<form method="post" action="/collect/match-rollover">'
+ csrf_input(request,'/collect/match-rollover')
+ match_backfill_button
+ '</form>'
'</div>'
'<div class="grid">'
+ f'<div class="kpi"><b>{esc(match_backfill_state)}</b><span>과거매칭 rollover 상태</span><small>{esc(match_backfill.get("last_error") or "")}</small></div>'
+ f'<div class="kpi"><b>{esc(match_years_label)}</b><span>자동 대상 fiscal year</span></div>'
+ f'<div class="kpi"><b>{esc(match_evidence_label)}</b><span>연도별 compact evidence</span></div>'
+ f'<div class="kpi"><b>{esc(population_years_label)}</b><span>검증 population 완료 연도</span></div>'
+ f'<div class="kpi"><b>{int(match_backfill.get("pattern_count") or 0):,}</b><span>이번 기관패턴 생성수</span></div>'
+ f'<div class="kpi"><b>{esc(match_backfill.get("patterns_updated_at") or "미갱신")}</b><span>기관패턴 갱신시각</span></div>'
+ legacy_match_html
+ '</div>'
)}
</section>
<section class="card"><h3>수집 단계별 현황</h3><div class="stage-grid">{stages}</div></section>
<section class="card"><h3>최근 실행 내역</h3>
<div class="collection-recent-desktop table"><table><tr><th>갱신시각</th><th>자료</th><th>수집범위</th><th>상태</th><th>페이지</th><th>저장</th><th>오류</th></tr>
{recent_rows or '<tr><td colspan="7">아직 collection checkpoint 실행 내역이 없습니다.</td></tr>'}
</table></div>
<div class="collection-recent-mobile">
{recent_cards or '<div class="muted">아직 collection checkpoint 실행 내역이 없습니다.</div>'}
</div></section>
<section class="card"><div class="notice"><b>수집 안전경계:</b> 일반 운영수집은 예산 정규화 자료 + 2026-01-01 이후 조명·등주 사업자료만 사용합니다. 과거매칭은 저장된 최근 2개 fiscal year를 자동 선택해 compact evidence와 기관패턴만 갱신하며, 2025 전용 backfill은 2025가 rollover 창에 포함되고 근거가 부족할 때만 호환 실행합니다. 용역·입찰·낙찰·계약 일반수집, generic bulk historical, APPROVED_HISTORICAL, 교육청 live transport는 계속 HOLD입니다.</div></section>
"""
    return layout(
        "수집 상태",
        body,
        "수집 상태",
        user,
        refresh_seconds=monitor_refresh_seconds,
    )


@app.post("/collect/shopping-recent")
async def collect_shopping_recent(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    if is_result_server():
        return JSONResponse({"ok": False, "error": "COLLECTION_RUNS_ON_LOCAL_PC"}, status_code=409)
    data = await form_data(request)
    if not valid_csrf(request, "/collect/shopping-recent", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    schedule_manual_collection("shopping")
    return RedirectResponse("/collection-monitor", 303)


@app.post("/collect/budget")
async def collect_budget_manual(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    if is_result_server():
        return JSONResponse(
            {"ok": False, "error": "COLLECTION_RUNS_ON_LOCAL_PC"},
            status_code=409,
        )
    data = await form_data(request)
    if not valid_csrf(request, "/collect/budget", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    schedule_manual_collection("budget")
    return RedirectResponse("/collection-monitor", 303)


@app.post("/collect/match-rollover")
async def collect_match_rollover(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    if is_result_server():
        return JSONResponse(
            {"ok": False, "error": "COLLECTION_RUNS_ON_LOCAL_PC"},
            status_code=409,
        )
    data = await form_data(request)
    if not valid_csrf(
        request,
        "/collect/match-rollover",
        data.get("_csrf"),
    ):
        return HTMLResponse("CSRF validation failed", status_code=403)
    schedule_match_rollover(force=True)
    return RedirectResponse("/collection-monitor", 303)


@app.post("/collect/match-backfill-2025")
async def collect_match_backfill_2025(request: Request):
    """Compatibility endpoint for old rendered forms/bookmarks."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    if is_result_server():
        return JSONResponse(
            {"ok": False, "error": "COLLECTION_RUNS_ON_LOCAL_PC"},
            status_code=409,
        )
    data = await form_data(request)
    if not valid_csrf(
        request,
        "/collect/match-backfill-2025",
        data.get("_csrf"),
    ):
        return HTMLResponse("CSRF validation failed", status_code=403)
    schedule_match_backfill_2025()
    return RedirectResponse("/collection-monitor", 303)


def _result_snapshot_shopping_page_rows(
    *,
    category,
    query,
    region,
    start_date,
    end_date,
    limit,
):
    """Read RESULT_SERVER shopping rows without a large prefetch.

    Date/category/query filtering stays in SQLite. Region matching is applied
    incrementally in small pages so the 256 MiB web process never materializes
    thousands of rows just to discard other regions.
    """
    target = max(1, min(int(limit), 1000))
    page_size = max(1, min(target, 250))
    rows = []
    offset = 0
    while len(rows) < target:
        batch = result_snapshot_vnext.query_rows(
            "shopping",
            categories=(category,),
            query=query,
            start_date=start_date,
            end_date=end_date,
            limit=page_size,
            offset=offset,
        )
        if not batch:
            break
        offset += len(batch)
        for row in batch:
            if region and str(row.get("demand_region") or "") != region:
                continue
            rows.append(row)
            if len(rows) >= target:
                break
        if len(batch) < page_size:
            break
    return rows


@app.get("/shopping")
def shopping_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)

    import procurement_read_vnext as read

    q = str(request.query_params.get("q", "") or "").strip()
    category = str(request.query_params.get("category", "LIGHTING") or "LIGHTING").upper()
    if category not in {"LIGHTING", "POLE"}:
        category = "LIGHTING"
    if "region" in request.query_params:
        region = str(request.query_params.get("region", "") or "").strip()
    else:
        region = "인천광역시"
    if region and region not in read.REGIONS:
        region = "인천광역시"
    start_date, end_date = _shopping_date_range(request)
    try:
        limit = max(10, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200

    if is_result_server() and result_snapshot_vnext.snapshot_available():
        rows = _result_snapshot_shopping_page_rows(
            category=category,
            query=q,
            region=region,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    else:
        rows = read.shopping_rows(
            categories=(category,),
            query=q,
            region=region,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

    region_options = ['<option value="">전국</option>'] + [
        f'<option value="{esc(name)}"{" selected" if region == name else ""}>{esc(name)}</option>'
        for name in read.REGIONS
    ]
    category_options = [
        f'<option value="LIGHTING"{" selected" if category=="LIGHTING" else ""}>LED 조명</option>',
        f'<option value="POLE"{" selected" if category=="POLE" else ""}>등주</option>',
    ]
    total_amount = sum(int(row.get("amount") or 0) for row in rows)
    total_qty = sum(float(row.get("quantity") or 0) for row in rows)
    vendors = len({str(row.get("vendor_name") or "") for row in rows if row.get("vendor_name")})
    trs = "".join(
        f"<tr><td class='nowrap'>{esc(r.get('source_date'))}</td>"
        f"<td class='text-cell'>{esc(r.get('demand_region'))}<br><span class='muted'>{esc(r.get('demand_org'))}</span></td>"
        f"<td class='text-cell'>{esc(r.get('detail_item_no'))}<br><span class='muted'>{esc(r.get('detail_item_name'))}</span></td>"
        f"<td class='text-cell'>{esc(r.get('item_id'))}<br><b>{esc(r.get('item_name'))}</b><br><span class='muted'>{esc(r.get('model_name'))}</span></td>"
        f"<td class='text-cell'>{esc(r.get('vendor_name'))}</td>"
        f"<td class='num'>{float(r.get('quantity') or 0):,.2f}</td>"
        f"<td class='num'>{money(r.get('unit_price'))}<br><span class='muted'>{'계산단가' if r.get('unit_price_basis') == 'CALCULATED_AMOUNT_DIV_QUANTITY' else ''}</span></td>"
        f"<td class='num'>{money(r.get('amount'))}</td></tr>"
        for r in rows
    )
    title = "LED 조명 조달내역" if category == "LIGHTING" else "등주 조달내역"
    active = "LED 조명" if category == "LIGHTING" else "등주"
    body = f"""
<section class="card"><h2>{title}</h2>
<p class="muted">2026-01-01 이후 전국 나라장터 납품요구를 저장자료에서 조회합니다. 기본 조회기간은 해당 연도 1월 1일 ~ 12월 31일이며 시작일·종료일을 직접 바꿔 검색할 수 있습니다. 기본 조회지역은 인천광역시입니다.</p>
<form class="row" method="get">
<label>시작일<input name="start_date" type="date" min="2026-01-01" value="{esc(start_date)}"></label>
<label>종료일<input name="end_date" type="date" min="2026-01-01" value="{esc(end_date)}"></label>
<label>지역<select name="region">{''.join(region_options)}</select></label>
<label>품목<select name="category">{''.join(category_options)}</select></label>
<label>검색<input name="q" value="{esc(q)}" placeholder="기관·제품·업체·식별번호·모델"></label>
<label>표시<input name="limit" type="number" min="10" max="1000" value="{limit}"></label>
<button class="primary">조회</button></form></section>
<div class="grid">
<div class="kpi"><b>{esc(start_date)}<br>~ {esc(end_date)}</b><span>조회기간</span></div>
<div class="kpi"><b>{len(rows):,}</b><span>조회 납품건</span></div>
<div class="kpi"><b>{total_qty:,.0f}</b><span>조회 수량</span></div>
<div class="kpi"><b>{money(total_amount)}</b><span>조회 금액</span></div>
<div class="kpi"><b>{vendors:,}</b><span>납품업체</span></div>
</div>
<section class="card"><div class="table shopping-table"><table>
<tr><th>일자</th><th>지역 / 수요기관</th><th>세부품명</th><th>제품 / 식별번호 / 모델</th><th>업체</th><th>수량</th><th>단가</th><th>금액</th></tr>
{trs or '<tr><td colspan="8">현재 조건의 조달내역 없음</td></tr>'}
</table></div></section>
"""
    return layout(title, body, active, user)


def _result_snapshot_vendor_section(region=""):
    name = str(region or "").strip()
    return f"vendors:{name}" if name else "vendors"


def _result_snapshot_vendor_rows(*, query="", region="", limit=200):
    return result_snapshot_vnext.query_rows(
        _result_snapshot_vendor_section(region),
        query=query,
        limit=max(1, min(int(limit), 1000)),
    )


@app.get("/vendors")
def vendors_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)

    import procurement_read_vnext as read

    q = str(request.query_params.get("q", "") or "").strip()
    if "region" in request.query_params:
        region = str(request.query_params.get("region", "") or "").strip()
    else:
        region = "인천광역시"
    if region and region not in read.REGIONS:
        region = "인천광역시"
    try:
        limit = max(10, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200

    # RESULT_SERVER is snapshot-only. Regional vendor totals are generated by the
    # local collector and must never fall through to production PostgreSQL.
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        rows = _result_snapshot_vendor_rows(
            query=q,
            region=region,
            limit=limit,
        )
    else:
        rows = read.vendor_rows(query=q, region=region, limit=limit)

    region_options = ['<option value="">전국</option>'] + [
        f'<option value="{esc(name)}"{" selected" if region == name else ""}>{esc(name)}</option>'
        for name in read.REGIONS
    ]
    trs = "".join(
        f"<tr><td><b>{esc(r.get('vendor_name'))}</b><br><span class='muted'>{esc(r.get('vendor_bizno'))}</span></td>"
        f"<td class='num'>{int(r.get('shopping_rows') or 0):,}</td>"
        f"<td class='num'>{int(r.get('demand_org_count') or 0):,}</td>"
        f"<td>{esc(', '.join(r.get('categories') or []))}</td>"
        f"<td class='num'>{money(r.get('shopping_amount'))}</td></tr>"
        for r in rows
    )
    body = f"""
<section class="card"><h2>업체 · 수주 분석</h2>
<p class="muted">용역 계약은 제외하고 2026-01-01 이후 조명·등주 납품실적만 업체별로 집계합니다.</p>
<form class="row" method="get">
<label>지역<select name="region">{''.join(region_options)}</select></label>
<label>업체검색<input name="q" value="{esc(q)}" placeholder="업체명·사업자번호"></label>
<label>표시<input name="limit" type="number" min="10" max="1000" value="{limit}"></label>
<button class="primary">조회</button></form></section>
<section class="card"><div class="table"><table>
<tr><th>업체</th><th>납품건</th><th>수요기관</th><th>품목분류</th><th>납품금액</th></tr>
{trs or '<tr><td colspan="5">현재 조건의 업체 실적 없음</td></tr>'}
</table></div></section>
"""
    return layout("업체·단가 분석", body, "업체·단가 분석", user)


def _budget_category_label(value):
    code = str(value or "").upper().strip()
    labels = {
        **CATEGORY_LABELS,
        "OTHER": "기타",
        "UNCLASSIFIED": "미분류",
    }
    return labels.get(code, code or "미분류")


def _budget_current_row_html(row, linked_details=None):
    import budget_read_vnext

    r = dict(row or {})
    links = list(linked_details or [])
    layer = str(r.get("source_layer") or "").upper()
    region = budget_read_vnext.row_region(r)
    org_name = str(r.get("org_name") or r.get("institution_name") or "").strip()
    dept_name = str(r.get("dept_name") or "").strip()
    project_name = str(r.get("project_name") or "").strip()
    project_code = str(r.get("project_code") or "").strip()
    field_name = str(r.get("field_name") or "").strip()
    section_name = str(r.get("section_name") or "").strip()
    account_name = str(r.get("account_name") or "").strip()
    snapshot_date = str(r.get("snapshot_date") or "").strip()

    if layer == "APPROPRIATION":
        type_label = "기능별 구조예산"
        type_class = "appropriation"
        type_note = "세부사업 아님"
    elif layer == "EDUCATION":
        type_label = "교육청 예산"
        type_class = "education"
        type_note = snapshot_date
    else:
        type_label = "세부사업·집행"
        type_class = "detail"
        type_note = snapshot_date

    org_html = (
        f"<span class='budget-region'>{esc(region or '지역 미확인')}</span>"
        f"<div class='budget-org'>{esc(org_name or '기관 미확인')}</div>"
    )
    if layer != "APPROPRIATION":
        dept_display = dept_name or "미수집"
        org_html += (
            f"<div class='budget-meta'>담당부서 · {esc(dept_display)}</div>"
        )

    if layer not in {"APPROPRIATION", "EDUCATION"}:
        budget_amount = int(
            r.get("budget_amount") or r.get("appropriation_amount") or 0
        )
        executed_amount = int(r.get("executed_amount") or 0)
        remaining_amount = int(r.get("remaining_amount") or 0)
        if executed_amount <= 0:
            execution_label = "미집행"
        elif remaining_amount > 0:
            execution_label = "부분집행"
        else:
            execution_label = "전액집행"
        execution_rate = (
            min(100.0, max(0.0, executed_amount / budget_amount * 100.0))
            if budget_amount > 0
            else None
        )
        status_note = execution_label
        if execution_rate is not None:
            status_note += f" · 집행률 {execution_rate:.1f}%"
        if snapshot_date:
            status_note += f" · 기준일 {snapshot_date}"
        type_note = status_note

    type_html = (
        f"<span class='budget-type {type_class}'>{esc(type_label)}</span>"
        + (f"<div class='budget-meta'>{esc(type_note)}</div>" if type_note else "")
    )

    structure_rows = []
    if field_name:
        structure_rows.append(("분야", field_name))
    if section_name:
        structure_rows.append(("부문", section_name))
    if account_name:
        structure_rows.append(("회계", account_name))
    structure_html = (
        "<div class='budget-structure'>"
        + "".join(
            f"<b>{esc(label)}</b><span>{esc(value)}</span>"
            for label, value in structure_rows
        )
        + "</div>"
        if structure_rows else ""
    )

    if layer == "APPROPRIATION":
        project_html = (
            "<div class='budget-project'>기능별 세출 구조예산</div>"
            + structure_html
        )
        unique_links = []
        seen = set()
        for link in links:
            name = str(link.get("detail_project_name") or "").strip()
            code = str(link.get("detail_project_code") or "").strip()
            dept = str(link.get("detail_dept_name") or "").strip()
            key = (name, code, dept)
            if not name or key in seen:
                continue
            seen.add(key)
            unique_links.append(link)
        if unique_links:
            shown = unique_links[:3]
            linked_rows = []
            for link in shown:
                name = str(link.get("detail_project_name") or "").strip()
                dept = str(link.get("detail_dept_name") or "").strip()
                amount = int(link.get("detail_budget_amount") or 0)
                detail = f" · {esc(dept)}" if dept else ""
                amount_text = f" · {money(amount)}" if amount else ""
                linked_rows.append(
                    f"<div><b>{esc(name)}</b>{detail}{amount_text}</div>"
                )
            more = len(unique_links) - len(shown)
            if more > 0:
                linked_rows.append(
                    f"<div class='muted'>외 {more:,}개 연결 세부사업</div>"
                )
            project_html += (
                "<div class='budget-linked'><strong>연결된 실제 QWGJK 세부사업</strong>"
                + "".join(linked_rows)
                + "</div>"
            )
        else:
            project_html += (
                "<div class='budget-linked'><strong>실제 세부사업명 없음</strong>"
                "<div class='muted'>AIDFA 자체는 기능·부문별 편성 총액입니다. "
                "같은 구조의 QWGJK 세부사업이 수집되면 여기에 연결해 표시합니다.</div></div>"
            )
        budget_html = (
            f"{money(r.get('budget_amount') or r.get('appropriation_amount'))}"
            "<div class='budget-note'>구조 편성총액</div>"
        )
        executed_html = "<span class='muted'>해당 없음</span>"
        remaining_html = "<span class='muted'>해당 없음</span>"
    else:
        project_title = esc(project_name or "사업명 미수집")
        detail_rows = [
            ("담당부서", dept_name or "미수집"),
            ("사업코드", project_code or "미수집"),
            ("분야", field_name or "미수집"),
            ("부문", section_name or "미수집"),
            ("회계", account_name or "미수집"),
            ("기준일", snapshot_date or "미수집"),
        ]
        project_html = (
            "<details class='budget-inline-detail'>"
            f"<summary class='budget-project'>{project_title}</summary>"
            "<div class='budget-structure'>"
            + "".join(
                f"<b>{esc(label)}</b><span>{esc(value)}</span>"
                for label, value in detail_rows
            )
            + "</div></details>"
        )
        budget_html = money(
            r.get("budget_amount") or r.get("appropriation_amount")
        )
        executed_html = money(r.get("executed_amount"))
        remaining_html = money(r.get("remaining_amount"))

    row_class = (
        "budget-row-appropriation"
        if layer == "APPROPRIATION"
        else "budget-row-detail"
    )
    return (
        f"<tr class='{row_class}'><td class='nowrap'>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{org_html}</td>"
        f"<td>{type_html}</td>"
        f"<td>{project_html}</td>"
        f"<td>{esc(_budget_category_label(r.get('primary_category')))}</td>"
        f"<td class='num'>{budget_html}</td>"
        f"<td class='num'>{executed_html}</td>"
        f"<td class='num'>{remaining_html}</td></tr>"
    )


def _budget_current_card_html(row):
    import budget_read_vnext

    r = dict(row or {})
    region = budget_read_vnext.row_region(r)
    org_name = str(r.get("org_name") or r.get("institution_name") or "기관 미확인")
    dept_name = str(r.get("dept_name") or "").strip()
    project_name = str(r.get("project_name") or "사업명 미수집")
    project_code = str(r.get("project_code") or "").strip()
    field_name = str(r.get("field_name") or "").strip()
    section_name = str(r.get("section_name") or "").strip()
    account_name = str(r.get("account_name") or "").strip()
    snapshot_date = str(r.get("snapshot_date") or "").strip()
    category = str(r.get("primary_category") or "").upper()
    budget_amount = int(r.get("budget_amount") or r.get("appropriation_amount") or 0)
    executed_amount = int(r.get("executed_amount") or 0)
    remaining_amount = int(r.get("remaining_amount") or 0)
    if executed_amount <= 0:
        state = "UNEXECUTED"
        state_label = "미집행"
        state_class = "unexecuted"
    elif remaining_amount > 0:
        state = "PARTIAL"
        state_label = "부분집행"
        state_class = "partial"
    else:
        state = "FULL"
        state_label = "전액집행"
        state_class = "full"
    rate = (
        min(100.0, max(0.0, executed_amount / budget_amount * 100.0))
        if budget_amount > 0 else 0.0
    )
    sales_badge = (
        "<span class='budget-sales-badge'>조명·등주 · 잔액 있음</span>"
        if category in {"LIGHTING", "POLE"} and remaining_amount > 0
        else ""
    )
    dept_html = (
        f"<div class='budget-project-dept'>담당부서 · "
        f"{esc(dept_name or '미수집')}</div>"
    )
    detail_rows = [
        ("담당부서", dept_name or "미수집"),
        ("사업코드", project_code or "미수집"),
        ("분야", field_name or "미수집"),
        ("부문", section_name or "미수집"),
        ("회계", account_name or "미수집"),
        ("기준일", snapshot_date or "미수집"),
    ]
    project_heading = (
        "<details class='budget-inline-detail'>"
        f"<summary><h4>{esc(project_name)}</h4></summary>"
        "<div class='budget-structure'>"
        + "".join(
            f"<b>{esc(label)}</b><span>{esc(value)}</span>"
            for label, value in detail_rows
        )
        + "</div></details>"
    )
    return (
        "<article class='budget-project-card'>"
        "<div class='budget-project-card-head'><div>"
        f"<span class='budget-region'>{esc(region or '지역 미확인')}</span>"
        f"{project_heading}"
        f"<div class='budget-project-org'>{esc(org_name)}</div>{dept_html}"
        f"{sales_badge}</div>"
        f"<span class='budget-status-badge {state_class}'>{state_label}</span></div>"
        "<div class='budget-money-grid'>"
        f"<div class='budget-money'><b>{money(budget_amount)}</b><small>예산</small></div>"
        f"<div class='budget-money'><b>{money(executed_amount)}</b><small>집행</small></div>"
        f"<div class='budget-money'><b>{money(remaining_amount)}</b><small>잔액</small></div>"
        "</div>"
        f"<div class='budget-exec-bar'><span style='width:{rate:.1f}%'></span></div>"
        f"<div class='budget-meta'>집행률 {rate:.1f}% · 분류 {_budget_category_label(category)}</div>"
        "</article>"
    )


def _budget_history_date_range(request, year):
    import datetime as _dt

    floor = _dt.date(2026, 1, 1)
    try:
        year_value = max(2026, int(year))
    except (TypeError, ValueError):
        year_value = 2026
    default_start = _dt.date(year_value, 1, 1)
    default_end = _dt.date(year_value, 12, 31)

    def parse(name, default):
        raw = str(request.query_params.get(name, "") or "").strip()
        if not raw:
            return default
        try:
            value = _dt.date.fromisoformat(raw)
        except ValueError:
            return default
        return max(floor, value)

    start = parse("history_start_date", default_start)
    end = parse("history_end_date", default_end)
    if start > end:
        start, end = default_start, default_end
    return start.isoformat(), end.isoformat()


def _budget_change_html(value):
    if value is None:
        return "<span class='change-flat'>비교기준 없음</span>"
    amount = int(value or 0)
    if amount > 0:
        return f"<span class='change-up'>+{money(amount)}</span>"
    if amount < 0:
        return f"<span class='change-down'>-{money(abs(amount))}</span>"
    return "<span class='change-flat'>변동 없음</span>"


def _future_sales_evidence_html(row):
    level = str(row.get("historical_evidence_level") or "")
    labels = {
        "STRONG_HISTORY": "과거 구매근거 강함",
        "HISTORY_PRESENT": "과거 구매근거 있음",
        "LIMITED_HISTORY": "과거 구매근거 제한",
        "NO_HISTORY": "과거 구매근거 없음",
        "OUTSIDE_LED_POLE": "LED·등주 근거대상 아님",
    }
    label = labels.get(level, level or "과거 구매근거 없음")
    score = int(row.get("historical_evidence_score") or 0)
    projects = int(row.get("historical_high_projects") or 0)
    total_projects = int(
        row.get("historical_total_budget_projects") or 0
    )
    high_rate = row.get("historical_high_match_project_rate")
    amount_ratio = float(
        row.get("historical_shopping_to_budget_amount_ratio") or 0
    )
    population_complete = bool(
        row.get("historical_population_complete")
    )
    shopping_amount = int(
        row.get("historical_actual_shopping_amount") or 0
    )
    lag = row.get("historical_average_lag_days")
    years = ", ".join(
        str(value)
        for value in (row.get("historical_evidence_years") or [])
    )
    verified_years = ", ".join(
        str(value)
        for value in (
            row.get("historical_population_complete_years") or []
        )
    )
    partial_years = ", ".join(
        str(value)
        for value in (row.get("historical_evidence_only_years") or [])
    )
    signals = ", ".join(
        str(value)
        for value in (row.get("historical_shared_signals") or [])
    )
    pattern_org = str(row.get("historical_pattern_org") or "")
    pattern_match_basis = str(
        row.get("historical_pattern_match_basis") or ""
    )
    historical_org_names = ", ".join(
        str(value)
        for value in (
            row.get("historical_pattern_historical_org_names") or []
        )
    )
    lineage_note = ""
    if bool(row.get("historical_organization_lineage_applied")) and pattern_org:
        lineage_note = (
            f"기관계보 {historical_org_names or '과거기관'} → {pattern_org}"
        )
    elif pattern_match_basis.startswith("AMBIGUOUS_RETIRED_"):
        lineage_note = "행정구역 변경 후 현재 기관 귀속 불명확 · 과거점수 미적용"
    details = [
        f"근거점수 {score}" if level not in {"NO_HISTORY", "OUTSIDE_LED_POLE"} else "",
        (
            f"과거 예산사업 {total_projects:,}건 중 높은일치 {projects:,}건 "
            f"({float(high_rate) * 100:.1f}%)"
            if population_complete and total_projects and high_rate is not None
            else (f"높은 일치 과거사업 {projects:,}건" if projects else "")
        ),
        f"과거 실제조달 {money(shopping_amount)}" if shopping_amount else "",
        (
            f"실제조달/높은일치예산 금액비 {amount_ratio * 100:.1f}%"
            if amount_ratio > 0 else ""
        ),
        f"평균 시차 {lag}일" if lag is not None else "",
        f"근거연도 {years}" if years else "",
        f"전체검증연도 {verified_years}" if verified_years else "",
        (
            f"부분 evidence 연도 {partial_years} · 다년보너스 제외"
            if partial_years else ""
        ),
        f"반복신호 {signals}" if signals else "",
        lineage_note,
    ]
    return (
        f"<b>{esc(label)}</b>"
        + (
            "<div class='budget-meta'>"
            + "<br>".join(esc(value) for value in details if value)
            + "</div>"
            if any(details)
            else ""
        )
    )


def _result_snapshot_budget_rows(
    *,
    section,
    categories,
    fiscal_year,
    region,
    limit,
):
    """Read RESULT_SERVER budget rows without duplicate post-filter lists."""
    import budget_read_vnext

    target = max(1, min(int(limit), 500))
    if not str(region or "").strip():
        return result_snapshot_vnext.query_rows(
            section,
            categories=categories,
            fiscal_year=fiscal_year,
            limit=target,
        )

    page_size = max(1, min(target, 100))
    rows = []
    offset = 0
    while len(rows) < target:
        batch = result_snapshot_vnext.query_rows(
            section,
            categories=categories,
            fiscal_year=fiscal_year,
            limit=page_size,
            offset=offset,
        )
        if not batch:
            break
        offset += len(batch)
        for row in batch:
            if not budget_read_vnext.region_matches(row, region):
                continue
            rows.append(row)
            if len(rows) >= target:
                break
        if len(batch) < page_size:
            break
    return rows


@app.get("/budget")
def budget_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)

    import datetime as _dt
    import budget_storage
    import budget_read_vnext

    year_text = str(request.query_params.get("year", "") or "").strip()
    year = int(year_text) if year_text.isdigit() else _dt.date.today().year
    category = str(request.query_params.get("category", "") or "").upper().strip()
    sales_priority = str(
        request.query_params.get("sales_priority", "") or ""
    ).strip() == "1"
    categories = (category,) if category in TARGET_CATEGORIES else None
    if sales_priority:
        categories = ("LIGHTING", "POLE")
    budget_query = str(
        request.query_params.get("budget_q", "") or ""
    ).strip()
    preset_query = str(
        request.query_params.get("preset_q", "") or ""
    ).strip()
    effective_budget_query = " ".join(
        value for value in (preset_query, budget_query) if value
    ).strip()
    execution_status = str(
        request.query_params.get("execution_status", "") or ""
    ).strip().upper()
    if execution_status not in {"", "UNEXECUTED", "PARTIAL", "FULL"}:
        execution_status = ""
    sort_order = str(
        request.query_params.get("sort", "REMAINING_DESC") or "REMAINING_DESC"
    ).strip().upper()
    if sort_order not in {"REMAINING_DESC", "BUDGET_DESC", "RECENT", "ORG_ASC"}:
        sort_order = "REMAINING_DESC"
    sort_labels = {
        "REMAINING_DESC": "잔액 큰 순",
        "BUDGET_DESC": "예산 큰 순",
        "RECENT": "최근 갱신순",
        "ORG_ASC": "기관명순",
    }
    if sales_priority:
        execution_status = ""
        sort_order = "REMAINING_DESC"
    try:
        detail_page = max(
            1, int(request.query_params.get("detail_page", 1) or 1)
        )
    except (TypeError, ValueError):
        detail_page = 1
    detail_page_size = 200
    detail_total_pages = None
    detail_offset = (detail_page - 1) * detail_page_size
    if "region" in request.query_params:
        region = str(request.query_params.get("region", "") or "").strip()
    else:
        region = "인천광역시"
    if region and region not in budget_read_vnext.REGIONS:
        region = "인천광역시"

    institution_filter = str(
        request.query_params.get("institution_filter", "") or ""
    ).strip()
    department_name = str(
        request.query_params.get("department_name", "") or ""
    ).strip()

    # 4.1.211 uses one visible institution selector. Preserve 4.1.209/4.1.210
    # deep links without letting stale Incheon filters leak into another region.
    legacy_institution_name = str(
        request.query_params.get("institution_name", "") or ""
    ).strip()
    legacy_institution_scope = str(
        request.query_params.get("institution_scope", "") or ""
    ).strip()
    institution_scope = ""
    institution_name = ""
    if institution_filter.startswith("scope:") and region == "인천광역시":
        import incheon_budget_scope_vnext
        institution_scope = incheon_budget_scope_vnext.normalize_scope(
            institution_filter.split(":", 1)[1]
        )
        institution_filter = "scope:" + institution_scope
    elif institution_filter.startswith("org:"):
        institution_name = institution_filter.split(":", 1)[1].strip()
        institution_filter = (
            "org:" + institution_name if institution_name else ""
        )
    elif legacy_institution_name:
        institution_name = legacy_institution_name
        institution_filter = "org:" + institution_name
    elif region == "인천광역시" and legacy_institution_scope:
        import incheon_budget_scope_vnext
        institution_scope = incheon_budget_scope_vnext.normalize_scope(
            legacy_institution_scope
        )
        institution_filter = "scope:" + institution_scope
    else:
        institution_filter = ""

    history_start_date, history_end_date = _budget_history_date_range(
        request, year
    )
    history_query = str(
        request.query_params.get("history_q", "") or ""
    ).strip()
    history_requested = str(
        request.query_params.get("history_submit", "") or ""
    ).strip() == "1"
    analysis_requested = str(
        request.query_params.get("analysis_submit", "") or ""
    ).strip() == "1"
    match_requested = str(
        request.query_params.get("match_submit", "") or ""
    ).strip() == "1"
    pattern_requested = str(
        request.query_params.get("pattern_submit", "") or ""
    ).strip() == "1"

    current_rows = []
    targets = []
    prebid = []
    future_rows = []
    appropriation_context = []
    history_rows = []
    match_summary = {}
    match_rows = []
    pattern_rows = []
    institution_names = []
    department_names = []
    detail_has_next = False
    condition_summary = {}
    summary_error = ""
    error = ""
    storage = {}
    dataset_counts = {}
    try:
        backend = str(budget_storage.backend_name())
        configured = bool(budget_storage.storage_configured())
        if budget_storage.using_postgres() and not configured:
            error = "예산 PostgreSQL 연결이 아직 설정되지 않았습니다."
        elif is_result_server() and result_snapshot_vnext.snapshot_available():
            # Snapshot compatibility stays read-only and bounded.
            if analysis_requested:
                targets = _result_snapshot_budget_rows(
                    section="budget_targets",
                    categories=categories,
                    fiscal_year=year,
                    region=region,
                    limit=300,
                )
                prebid = _result_snapshot_budget_rows(
                    section="budget_prebid",
                    categories=categories,
                    fiscal_year=year,
                    region=region,
                    limit=300,
                )
        else:
            # Institution choices are derived from already-stored current facts.
            # No source API call or dataset materialization is performed.
            institution_names = budget_read_vnext.budget_institution_names(
                fiscal_year=year,
                region=region,
                source_layers=("DETAIL_EXECUTION",),
            )
            department_names = budget_read_vnext.budget_department_names(
                fiscal_year=year,
                region=region,
                institution_scope=institution_scope,
                institution_name=institution_name,
                source_layers=("DETAIL_EXECUTION",),
            )

            # Critical web-path rule: never run the full fiscal-year analysis on
            # simple /budget navigation. Read only bounded current-state slices.
            try:
                condition_summary = budget_read_vnext.screen_budget_summary(
                    fiscal_year=year,
                    source_layers=("DETAIL_EXECUTION", "EDUCATION"),
                    categories=categories,
                    region=region,
                    institution_scope=institution_scope,
                    institution_name=institution_name,
                    department_name=department_name,
                    query=effective_budget_query,
                    execution_status=execution_status,
                    remaining_positive=sales_priority,
                )
                summary_count = max(
                    0,
                    int(condition_summary.get("project_count") or 0),
                )
                detail_total_pages = max(
                    1,
                    (summary_count + detail_page_size - 1)
                    // detail_page_size,
                )
                if detail_page > detail_total_pages:
                    detail_page = detail_total_pages
                    detail_offset = (
                        detail_page - 1
                    ) * detail_page_size
            except Exception as exc:
                summary_error = type(exc).__name__

            detail_current_rows = budget_read_vnext.screen_budget_rows(
                fiscal_year=year,
                source_layers=("DETAIL_EXECUTION", "EDUCATION"),
                categories=categories,
                region=region,
                institution_scope=institution_scope,
                institution_name=institution_name,
                department_name=department_name,
                query=effective_budget_query,
                execution_status=execution_status,
                remaining_positive=sales_priority,
                sort_order=sort_order,
                limit=detail_page_size + 1,
                offset=detail_offset,
            )
            detail_has_next = len(detail_current_rows) > detail_page_size
            if detail_has_next:
                detail_current_rows = detail_current_rows[:detail_page_size]
            structural_current_rows = (
                []
                if sales_priority
                else budget_read_vnext.screen_budget_rows(
                    fiscal_year=year,
                    source_layers=("APPROPRIATION",),
                    categories=categories,
                    region=region,
                    institution_scope=institution_scope,
                    institution_name=institution_name,
                    department_name=department_name,
                    limit=100,
                )
            )
            current_rows = detail_current_rows + structural_current_rows

            # Link only the bounded rows already loaded for this screen. This keeps
            # the explanatory AIDFA context without scanning the whole fiscal year.
            import budget_organization_vnext
            appropriation_context = (
                budget_organization_vnext.exact_appropriation_detail_links_from_rows(
                    current_rows,
                    fiscal_year=year,
                )
            )

            if analysis_requested:
                target_categories = (
                    categories if categories is not None else TARGET_CATEGORIES
                )
                targets = budget_read_vnext.screen_budget_rows(
                    fiscal_year=year,
                    source_layers=("DETAIL_EXECUTION", "EDUCATION"),
                    categories=target_categories,
                    region=region,
                    institution_scope=institution_scope,
                    query=budget_query,
                    execution_status=execution_status,
                    sort_order="REMAINING_DESC",
                    limit=300,
                    offset=0,
                )
                prebid = sorted(
                    [
                        row for row in targets
                        if int(row.get("remaining_amount") or 0) > 0
                    ],
                    key=lambda row: (
                        -int(row.get("remaining_amount") or 0),
                        str(row.get("org_name") or ""),
                        str(row.get("project_name") or ""),
                    ),
                )[:300]
                import future_sales_evidence_vnext
                future_rows = future_sales_evidence_vnext.future_budget_rows(
                    fiscal_year=_dt.date.today().year + 1,
                    categories=target_categories,
                    region=region,
                    institution_scope=institution_scope,
                    limit=500,
                    result_limit=200,
                )

            if history_requested:
                history_rows = budget_read_vnext.qwgjk_history_rows(
                    start_date=history_start_date,
                    end_date=history_end_date,
                    region=region,
                    institution_scope=institution_scope,
                    institution_name=institution_name,
                    department_name=department_name,
                    query=history_query,
                    categories=categories,
                    limit=300,
                )

            if match_requested:
                if not TEST_MODE and memory_guard.low_memory_web_hold():
                    error = (
                        "256MB 메모리 안전모드에서는 예산-조달 일괄 매칭을 "
                        "웹 프로세스에서 실행하지 않습니다."
                    )
                else:
                    import budget_shopping_match_vnext
                    match_summary = budget_shopping_match_vnext.historical_match_summary(
                        fiscal_year=year,
                        region=region,
                        categories=("LIGHTING", "POLE"),
                        budget_limit=300,
                        shopping_limit=3000,
                        candidates_per_project=3,
                    )
                    match_rows = list(match_summary.get("matches") or [])[:100]

            if pattern_requested:
                import budget_shopping_match_store
                pattern_rows = budget_shopping_match_store.organization_patterns(
                    fiscal_years=(2025, 2026),
                    region=region,
                    min_score=80,
                    limit=100,
                )
    except Exception as exc:
        error = f"예산 저장소 준비 중 ({type(exc).__name__})"

    opts = ['<option value="">전체 대상</option>'] + [
        f'<option value="{code}"{" selected" if category==code else ""}>{CATEGORY_LABELS[code]}</option>'
        for code in TARGET_CATEGORIES
    ]
    region_options = ['<option value="">전국</option>'] + [
        f'<option value="{esc(name)}"{" selected" if region == name else ""}>{esc(name)}</option>'
        for name in budget_read_vnext.REGIONS
    ]
    if institution_name and institution_name not in institution_names:
        institution_names = [institution_name] + list(institution_names)
    institution_datalist = "".join(
        f'<option value="{esc(name)}"></option>'
        for name in institution_names
    )
    selected_institution_label = (
        institution_name
        if institution_name
        else ("전국 전체기관" if not region else f"{region} 전체기관")
    )

    appropriation_links = {}
    for link in appropriation_context:
        key = str(link.get("appropriation_raw_key") or "").strip()
        if key:
            appropriation_links.setdefault(key, []).append(link)

    detail_current_rows = [
        row for row in current_rows
        if str(row.get("source_layer") or "").upper() != "APPROPRIATION"
    ]
    structural_current_rows = [
        row for row in current_rows
        if str(row.get("source_layer") or "").upper() == "APPROPRIATION"
    ]
    detail_budget_cards_html = "".join(
        _budget_current_card_html(r) for r in detail_current_rows
    )
    visible_budget_total = sum(
        int(r.get("budget_amount") or r.get("appropriation_amount") or 0)
        for r in detail_current_rows
    )
    visible_executed_total = sum(
        int(r.get("executed_amount") or 0) for r in detail_current_rows
    )
    visible_remaining_total = sum(
        int(r.get("remaining_amount") or 0) for r in detail_current_rows
    )
    visible_unexecuted = sum(
        int(r.get("executed_amount") or 0) <= 0 for r in detail_current_rows
    )
    visible_partial = sum(
        int(r.get("executed_amount") or 0) > 0
        and int(r.get("remaining_amount") or 0) > 0
        for r in detail_current_rows
    )
    visible_sales_ready = sum(
        str(r.get("primary_category") or "").upper() in {"LIGHTING", "POLE"}
        and int(r.get("remaining_amount") or 0) > 0
        for r in detail_current_rows
    )
    summary_is_full = (
        str(condition_summary.get("scope") or "")
        == "FULL_FILTERED_CURRENT"
    )
    summary_project_count = (
        int(condition_summary.get("project_count") or 0)
        if summary_is_full else len(detail_current_rows)
    )
    summary_budget_total = (
        int(condition_summary.get("budget_total") or 0)
        if summary_is_full else visible_budget_total
    )
    summary_executed_total = (
        int(condition_summary.get("executed_total") or 0)
        if summary_is_full else visible_executed_total
    )
    summary_remaining_total = (
        int(condition_summary.get("remaining_total") or 0)
        if summary_is_full else visible_remaining_total
    )
    summary_unexecuted = (
        int(condition_summary.get("unexecuted_count") or 0)
        if summary_is_full else visible_unexecuted
    )
    summary_partial = (
        int(condition_summary.get("partial_count") or 0)
        if summary_is_full else visible_partial
    )
    summary_sales_ready = (
        int(condition_summary.get("sales_ready_count") or 0)
        if summary_is_full else visible_sales_ready
    )
    summary_sales_ready_remaining = (
        int(condition_summary.get("sales_ready_remaining") or 0)
        if summary_is_full else sum(
            int(r.get("remaining_amount") or 0)
            for r in detail_current_rows
            if (
                str(r.get("primary_category") or "").upper()
                in {"LIGHTING", "POLE"}
                and int(r.get("remaining_amount") or 0) > 0
            )
        )
    )
    summary_classified_count = (
        int(condition_summary.get("classified_count") or 0)
        if summary_is_full else len(detail_current_rows)
    )
    summary_classification_pending = (
        int(condition_summary.get("classification_pending_count") or 0)
        if summary_is_full else 0
    )
    classification_coverage_notice = (
        '<div class="notice"><b>분류 진행중:</b> '
        f'전체 조건 {summary_project_count:,}건 중 '
        f'{summary_classified_count:,}건이 현재 분류완료이고 '
        f'{summary_classification_pending:,}건은 분류대기입니다. '
        '조명·등주 잔액 후보 수와 후보 잔액은 '
        '<b>분류완료 건 기준</b>으로 표시합니다.</div>'
        if (
            summary_is_full
            and not category
            and summary_classification_pending > 0
        )
        else ""
    )
    sales_ready_note = (
        f"분류완료 기준 · 분류대기 {summary_classification_pending:,}건 · "
        f"후보 잔액 {money(summary_sales_ready_remaining)}"
        if (
            summary_is_full
            and not category
            and summary_classification_pending > 0
        )
        else f"후보 잔액 {money(summary_sales_ready_remaining)}"
    )
    summary_scope_label = "전체 조건" if summary_is_full else "현재 페이지"
    classification_scope_label = (
        "전체조건 분류완료"
        if summary_is_full else "현재페이지 분류표시"
    )
    summary_scope_note = (
        "PostgreSQL 전체 조건 집계 · 목록은 200건씩 표시"
        if summary_is_full
        else (
            "전체 집계 일시 대기 · 현재 페이지 기준"
            + (f" · {summary_error}" if summary_error else "")
        )
    )
    detail_budget_rows_html = "".join(
        _budget_current_row_html(r)
        for r in detail_current_rows
    )
    structural_budget_rows_html = "".join(
        _budget_current_row_html(
            r,
            appropriation_links.get(str(r.get("raw_source_key") or ""), ()),
        )
        for r in structural_current_rows
    )

    history_rows_html = "".join(
        f"<tr><td class='nowrap'>{esc(r.get('source_date') or r.get('snapshot_date'))}</td>"
        f"<td><span class='budget-region'>{esc(budget_read_vnext.row_region(r) or '지역 미확인')}</span>"
        f"<div class='budget-org'>{esc(r.get('org_name') or '기관 미확인')}</div></td>"
        f"<td>{esc(r.get('dept_name') or '부서 미수집')}</td>"
        f"<td><div class='budget-project'>{esc(r.get('project_name') or '사업명 미수집')}</div>"
        f"<div class='budget-meta'>분류 · {esc(_budget_category_label(r.get('primary_category')))}<br>"
        f"{esc('분야 · ' + str(r.get('field_name'))) if r.get('field_name') else ''}"
        f"{'<br>' if r.get('field_name') and r.get('section_name') else ''}"
        f"{esc('부문 · ' + str(r.get('section_name'))) if r.get('section_name') else ''}"
        f"</div></td>"
        f"<td class='num'>{money(r.get('budget_amount'))}</td>"
        f"<td class='num'>{money(r.get('executed_amount'))}</td>"
        f"<td class='num'>{money(r.get('remaining_amount'))}</td>"
        f"<td><div>예산 {_budget_change_html(r.get('budget_change'))}</div>"
        f"<div>집행 {_budget_change_html(r.get('executed_change'))}</div>"
        f"<div>잔액 {_budget_change_html(r.get('remaining_change'))}</div></td></tr>"
        for r in history_rows
    )

    match_rows_html = "".join(
        f"<tr><td><span class='pill'>{esc('높은 일치' if r.get('level') == 'HIGH' else '검토 후보')}</span>"
        f"<div class='budget-meta'>점수 {int(r.get('score') or 0)}</div></td>"
        f"<td><b>{esc(r.get('budget_project_name'))}</b>"
        f"<div class='budget-meta'>{esc(r.get('budget_org'))} · {money(r.get('budget_amount'))}</div></td>"
        f"<td><b>{esc(r.get('shopping_item') or r.get('shopping_delivery_name'))}</b>"
        f"<div class='budget-meta'>{esc(r.get('shopping_org'))} · {esc(r.get('shopping_date'))}<br>"
        f"{esc(r.get('shopping_vendor'))} · {money(r.get('shopping_amount'))}</div></td>"
        f"<td>{esc(', '.join(r.get('shared_signals') or []))}"
        f"<div class='budget-meta'>{esc(' / '.join(r.get('evidence') or []))}</div></td>"
        f"<td>{esc(str(r.get('lag_days')) + '일' if r.get('lag_days') is not None else '날짜 비교 불가')}</td></tr>"
        for r in match_rows
    )
    match_rate = float(match_summary.get("project_match_rate") or 0) * 100
    expansion_notice = (
        '<div class="notice bad"><b>2025년 확장 권고:</b> 현재 2026년 표본만으로 기관별 예산→실제 조달 패턴을 학습하기에 근거가 부족합니다. '
        '2025년 QWGJK 예산과 LED·등주 조달내역을 추가 수집해 검증 표본을 넓히는 단계로 진행합니다.</div>'
        if match_requested and bool(match_summary.get("expand_2025_recommended"))
        else (
            '<div class="notice ok"><b>현재 표본 충분:</b> 2026년 저장자료에서 과거 예산→실제 조달 패턴을 분석할 최소 표본이 확보됐습니다.</div>'
            if match_requested else ''
        )
    )

    pattern_rows_html = "".join(
        f"<tr><td><b>{esc(r.get('org_name'))}</b>"
        f"<div class='budget-meta'>근거연도 · {esc(', '.join(str(y) for y in (r.get('evidence_years') or [])))}</div></td>"
        f"<td class='num'>{int(r.get('historical_budget_projects') or 0):,}</td>"
        f"<td class='num'>{int(r.get('high_matched_budget_projects') or 0):,}</td>"
        f"<td class='num'>{((str(round(float(r.get('high_match_project_rate') or 0) * 100, 1)) + '%') if r.get('population_complete') and r.get('high_match_project_rate') is not None else '분모 미확보')}"
        f"<div class='budget-meta'>{'전체 예산사업 기준' if r.get('population_complete') else '기존 evidence만 보존'}</div></td>"
        f"<td class='num'>{int(r.get('high_matched_shopping_rows') or 0):,}</td>"
        f"<td class='num'>{money(r.get('matched_budget_amount'))}</td>"
        f"<td class='num'>{money(r.get('actual_shopping_amount'))}</td>"
        f"<td class='num'>{float(r.get('shopping_to_budget_amount_ratio') or 0) * 100:.1f}%</td>"
        f"<td>{esc(str(r.get('average_nonnegative_lag_days')) + '일' if r.get('average_nonnegative_lag_days') is not None else '시차 근거 부족')}</td>"
        f"<td>{esc(', '.join((r.get('signal_counts') or {}).keys()))}</td></tr>"
        for r in pattern_rows
    )

    target_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{esc(budget_read_vnext.row_region(r))}<br><span class='muted'>{esc(r.get('org_name') or r.get('institution_name'))}</span></td>"
        f"<td><b>{esc(r.get('project_name'))}</b></td>"
        f"<td>{esc(_budget_category_label(r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('budget_amount'))}</td>"
        f"<td class='num'>{money(r.get('executed_amount'))}</td>"
        f"<td class='num'>{money(r.get('remaining_amount'))}</td></tr>"
        for r in targets
    )
    prebid_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{esc(budget_read_vnext.row_region(r))}<br><span class='muted'>{esc(r.get('org_name') or r.get('institution_name'))}</span></td>"
        f"<td><b>{esc(r.get('project_name'))}</b></td>"
        f"<td>{esc(_budget_category_label(r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('remaining_amount'))}</td></tr>"
        for r in prebid
    )
    future_budget_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{esc(budget_read_vnext.row_region(r))}<br><span class='muted'>{esc(r.get('org_name') or r.get('region_name'))}</span></td>"
        f"<td><b>{esc(r.get('project_name') or (' > '.join(value for value in (str(r.get('field_name') or ''), str(r.get('section_name') or '')) if value)) or '구조예산')}</b></td>"
        f"<td>{esc(_budget_category_label(r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('budget_amount') or r.get('appropriation_amount'))}</td>"
        f"<td>{_future_sales_evidence_html(r)}</td></tr>"
        for r in future_rows
    )
    backend = str(locals().get("backend") or budget_storage.backend_name())
    qwg_current = sum(
        1 for row in detail_current_rows
        if str(row.get("source_layer") or "") == "DETAIL_EXECUTION"
    )
    education_current = sum(
        1 for row in detail_current_rows
        if str(row.get("source_layer") or "") == "EDUCATION"
    )
    aidfa_current = len(structural_current_rows)
    current_records = len(current_rows)
    observations = len(history_rows)
    notice = (
        f'<div class="notice bad">{esc(error)}</div>' if error else
        '<div class="notice ok"><b>예산 중심 운영:</b> 원문 JSON은 저장하지 않고 기관·사업·예산·집행 등 필요한 필드와 변경 hash만 PostgreSQL에 보존합니다.</div>'
    )

    def budget_filter_url(
        next_category=None,
        next_execution=None,
        next_sort=None,
        *,
        analysis=False,
    ):
        category_value = category if next_category is None else str(next_category)
        execution_value = execution_status if next_execution is None else str(next_execution)
        sort_value = sort_order if next_sort is None else str(next_sort)
        values = [
            ("year", str(year)),
            ("region", region),
            ("category", category_value),
            ("institution_name", institution_name),
            ("budget_q", budget_query),
            ("execution_status", execution_value),
            ("sort", sort_value),
        ]
        if analysis:
            values.append(("analysis_submit", "1"))
        return "/budget?" + "&".join(
            f"{quote(str(key))}={quote(str(value))}"
            for key, value in values
        )

    def budget_search_url(term):
        values = [
            ("year", str(year)),
            ("region", region),
            ("institution_name", institution_name),
            # Keyword search intentionally clears category so OTHER projects such
            # as roads, parks and new buildings are not hidden.
            ("category", ""),
            ("budget_q", str(term or "")),
            ("execution_status", execution_status),
            ("sort", sort_order),
        ]
        return "/budget?" + "&".join(
            f"{quote(str(key))}={quote(str(value))}"
            for key, value in values
        )

    lighting_search_terms = (
        "조명", "LED", "가로등", "보안등", "실내조명", "평판등",
        "다운라이트", "투광등", "터널등", "등주", "경관조명",
    )
    project_search_terms = (
        "신축", "건립", "증축", "리모델링", "도로개설", "도로정비",
        "도로개선", "공원", "주차장", "터널", "교량", "도시재생",
        "경관개선", "보행환경",
    )
    lighting_quick_html = "".join(
        f'<a class="{"on" if budget_query == term and not category else ""}" '
        f'href="{esc(budget_search_url(term))}">{esc(term)}</a>'
        for term in lighting_search_terms
    )
    project_quick_html = "".join(
        f'<a class="{"on" if budget_query == term and not category else ""}" '
        f'href="{esc(budget_search_url(term))}">{esc(term)}</a>'
        for term in project_search_terms
    )
    clear_search_url = budget_search_url("")

    quick_category_html = "".join(
        f'<a class="{"on" if category == code else ""}" href="{esc(budget_filter_url(code, execution_status))}">{label}</a>'
        for code, label in (
            ("", "전체"),
            ("LIGHTING", "조명"),
            ("POLE", "등주"),
            ("ELECTRICAL", "전기"),
            ("SOLAR", "태양광"),
        )
    )
    quick_execution_html = "".join(
        f'<a class="{"on" if execution_status == code else ""}" href="{esc(budget_filter_url(category, code))}">{label}</a>'
        for code, label in (
            ("", "전체 집행"),
            ("UNEXECUTED", "미집행"),
            ("PARTIAL", "부분집행"),
            ("FULL", "전액집행"),
        )
    )
    quick_sort_html = "".join(
        f'<a class="{"on" if sort_order == code else ""}" href="{esc(budget_filter_url(category, execution_status, code))}">{label}</a>'
        for code, label in (
            ("REMAINING_DESC", "잔액 큰 순"),
            ("BUDGET_DESC", "예산 큰 순"),
            ("RECENT", "최근 갱신순"),
            ("ORG_ASC", "기관명순"),
        )
    )

    excel_values = [
        ("year", str(year)),
        ("region", region),
        ("institution_name", institution_name),
        ("category", category),
        ("budget_q", budget_query),
        ("execution_status", execution_status),
        ("sort", sort_order),
    ]
    if sales_priority:
        excel_values.append(("sales_priority", "1"))
    budget_excel_url = "/budget/export.xlsx?" + "&".join(
        f"{quote(str(key))}={quote(str(value))}"
        for key, value in excel_values
    )

    def detail_page_url(page):
        values = [
            ("year", str(year)),
            ("region", region),
            ("category", category),
            ("institution_name", institution_name),
            ("budget_q", budget_query),
            ("execution_status", execution_status),
            ("sort", sort_order),
            ("detail_page", str(max(1, int(page)))),
        ]
        if sales_priority:
            values.append(("sales_priority", "1"))
        return "/budget?" + "&".join(
            f"{quote(str(key))}={quote(str(value))}"
            for key, value in values
        )

    sales_priority_values = [
        ("year", str(year)),
        ("region", region),
        ("institution_name", institution_name),
        ("budget_q", budget_query),
        ("sales_priority", "1"),
    ]
    sales_priority_url = "/budget?" + "&".join(
        f"{quote(str(key))}={quote(str(value))}"
        for key, value in sales_priority_values
    )
    normal_budget_url = budget_filter_url("", "", "REMAINING_DESC")
    sales_priority_control = (
        f'<a class="btn" href="{esc(normal_budget_url)}">일반 예산 보기</a>'
        if sales_priority
        else f'<a class="btn primary" href="{esc(sales_priority_url)}">영업우선 보기</a>'
    )
    sales_priority_notice = (
        '<div class="notice ok"><b>영업우선 보기:</b> 조명·등주 세부사업 중 '
        '잔액이 남은 사업만 잔액 큰 순으로 표시합니다. 미집행·부분집행 사업을 '
        '바로 영업 검토할 수 있으며 전액집행 사업은 제외합니다.</div>'
        if sales_priority else ""
    )

    detail_prev = (
        f'<a class="btn" href="{esc(detail_page_url(detail_page - 1))}">← 이전 200건</a>'
        if detail_page > 1 else ""
    )
    detail_next = (
        f'<a class="btn" href="{esc(detail_page_url(detail_page + 1))}">다음 200건 →</a>'
        if detail_has_next else ""
    )
    detail_page_label = (
        f"세부사업 페이지 {detail_page:,} / {int(detail_total_pages):,} · "
        f"전체 {summary_project_count:,}건"
        if detail_total_pages is not None and summary_is_full
        else f"세부사업 페이지 {detail_page:,}"
    )
    detail_paging = (
        f'<div class="row"><span class="muted">{esc(detail_page_label)}</span>'
        f'{detail_prev}{detail_next}</div>'
    )
    auxiliary_match_html = (
        f"""<section class="card"><h3>보조 검증 · 과거 QWGJK 예산 ↔ 실제 LED·등주 조달</h3>
<p class="muted">영업판단의 핵심 목록이 아니라 저장자료 품질을 확인할 때만 사용하는 참고 기능입니다. 외부 API를 호출하지 않습니다.</p>
{expansion_notice}
<div class="grid">
<div class="kpi"><b>{int(match_summary.get('budget_projects_scanned') or 0):,}</b><span>검증 예산사업</span></div>
<div class="kpi"><b>{int(match_summary.get('shopping_rows_scanned') or 0):,}</b><span>비교 조달건</span></div>
<div class="kpi"><b>{int(match_summary.get('high_matched_budget_projects') or 0):,}</b><span>높은 일치 예산사업</span></div>
<div class="kpi"><b>{match_rate:.1f}%</b><span>연결후보 사업 비율</span></div>
</div>
<div class="table"><table>
<tr><th>판정</th><th>QWGJK 예산사업</th><th>실제 LED·등주 조달</th><th>일치근거</th><th>예산→조달 시차</th></tr>
{match_rows_html}</table></div></section>"""
        if match_requested else ""
    )
    auxiliary_pattern_html = (
        f"""<section class="card"><h3>보조 참고 · 기관별 예산 → 실제 LED·등주 구매 패턴</h3>
<p class="muted">과거 매칭 evidence를 확인하기 위한 참고표이며 영업후보 선정의 주목록이 아닙니다. <b>높은 일치율은 직접 재원전환율이나 수주확률이 아니며</b>, 저장자료의 참고 지표입니다.</p>
<div class="table"><table>
<tr><th>기관</th><th>과거 예산사업</th><th>높은 일치 사업</th><th>높은 일치율</th><th>실제 조달건</th><th>높은일치 예산규모</th><th>실제 조달금액</th><th>조달/예산 금액비</th><th>평균 예산→조달 시차</th><th>반복 신호</th></tr>
{pattern_rows_html}</table></div></section>"""
        if pattern_requested else ""
    )
    analysis_sections_html = (
        f"""<section class="card"><h3>{_dt.date.today().year + 1} 다음연도 편성예산</h3>
<p class="muted">다음연도 편성자료에서 조명·등주 영업 검토 신호를 확인합니다. 전체 과거 예산사업 분모가 확보된 기관은 높은 일치율도 근거점수에 반영합니다. <b>과거구매근거 점수와 높은 일치율은 수주확률이 아니며</b>, 영업 우선검토를 위한 참고값입니다. AIDFA 구조예산은 세부사업이 아니므로 근거점수를 최대 75로 제한합니다.</p>
<div class="table"><table><tr><th>연도</th><th>지역 / 기관</th><th>사업·예산구조</th><th>분류</th><th>편성예산</th><th>과거 실제구매 근거</th></tr>
{future_budget_rows}</table></div></section>
<section class="card"><h3>우선 영업후보 · 잔액 있는 사업</h3>
<p class="muted">현재 세부사업 중 잔액이 남은 조명·등주·전기·태양광 관련 사업을 잔액 큰 순서로 표시합니다.</p>
<div class="table"><table><tr><th>연도</th><th>지역 / 기관</th><th>사업명</th><th>분류</th><th>잔액</th></tr>
{prebid_rows}</table></div></section>
<section class="card budget-tech"><details><summary>분석 대상 예산사업 전체 보기</summary><div class="table"><table>
<tr><th>연도</th><th>지역 / 기관</th><th>사업명</th><th>분류</th><th>예산</th><th>집행</th><th>잔액</th></tr>
{target_rows}</table></div></details></section>"""
        if analysis_requested
        else (
            '<section class="card"><h3>영업후보·다음연도 예산</h3>'
            '<p class="muted">기본 화면은 빠른 조회를 위해 현재 세부사업만 보여줍니다. '
            '영업후보와 다음연도 편성예산을 보고 싶을 때만 분석을 실행하세요.</p>'
            f'<a class="btn primary" href="{esc(budget_filter_url(analysis=True))}">영업후보·다음연도 예산 보기</a>'
            '</section>'
        )
    )

    body = f"""
<section class="card"><h2>예산 · 영업후보</h2>
{notice}
<form class="row" method="get">
<input type="hidden" name="sort" value="{esc(sort_order)}">
<label>연도<input name="year" value="{year}" inputmode="numeric"></label>
<label>지역<select name="region" onchange="this.form.elements['institution_name'].value='';this.form.submit()">{''.join(region_options)}</select></label>
<label>기관·부서<input name="institution_name" list="budget-institutions" value="{esc(institution_name)}" placeholder="선택 지역 기관·부서 입력·선택"></label>
<datalist id="budget-institutions">{institution_datalist}</datalist>
<label>분류<select name="category">{''.join(opts)}</select></label>
<label>기관·사업 검색<input name="budget_q" value="{esc(budget_query)}" placeholder="사업명·기관·부서·분야·부문 검색"></label>
<label>집행상태<select name="execution_status">
<option value=""{" selected" if not execution_status else ""}>전체</option>
<option value="UNEXECUTED"{" selected" if execution_status=="UNEXECUTED" else ""}>미집행</option>
<option value="PARTIAL"{" selected" if execution_status=="PARTIAL" else ""}>부분집행</option>
<option value="FULL"{" selected" if execution_status=="FULL" else ""}>전액집행</option>
</select></label>
<button class="primary">세부사업 조회</button></form>
<div class="actions" style="margin-top:12px">{sales_priority_control}<a class="btn" href="{esc(budget_excel_url)}">엑셀 다운로드</a></div><div class="muted">엑셀은 현재 검색조건 기준 최대 10,000건까지 저장자료에서 생성합니다.</div>
{sales_priority_notice}
<p class="muted"><b>전국 지역·기관·부서를 저장된 예산자료에서 선택할 수 있습니다.</b> 지역을 바꾸면 해당 지역의 기관 목록을 다시 불러옵니다. 현재 선택 · {esc(selected_institution_label)}. 사업명을 누르면 담당부서·예산·집행·잔액 상세를 확인할 수 있습니다.</p>
<div><b>조명 빠른검색</b><div class="budget-quick"><a href="{esc(clear_search_url)}">전체</a>{lighting_quick_html}</div></div>
<div><b>사업유형 빠른검색</b><div class="budget-quick">{project_quick_html}</div></div>
<div><b>정확 분류 필터</b><div class="budget-quick">{quick_category_html}</div></div>
<div><b>집행상태 빠른선택</b><div class="budget-quick">{quick_execution_html}</div></div>
<div><b>보기 순서</b><div class="budget-quick">{quick_sort_html}</div></div>
</section>
<section class="card"><h3>현재 조건 한눈에 보기</h3>
<p class="muted"><b>{esc(summary_scope_label)} 기준</b>입니다. 현재 보기 · <b>{'영업우선' if sales_priority else '일반 예산'}</b> · 현재 정렬 <b>{esc(sort_labels[sort_order])}</b>. {esc(summary_scope_note)}. 전체 행을 웹 메모리에 올리지 않고 PostgreSQL COUNT/SUM으로 집계합니다.</p>
<div class="budget-overview-grid">
<div class="budget-overview-card"><b>{summary_project_count:,}건</b><span>총 세부사업</span><small>{esc(detail_page_label)}</small></div>
<div class="budget-overview-card"><b>{money(summary_budget_total)}</b><span>총 예산</span><small>현재 검색·기관·분류 조건</small></div>
<div class="budget-overview-card"><b>{money(summary_executed_total)}</b><span>총 집행</span><small>누적 집행액</small></div>
<div class="budget-overview-card"><b>{money(summary_remaining_total)}</b><span>총 잔액</span><small>예산 - 집행 기준</small></div>
<div class="budget-overview-card"><b>{summary_unexecuted:,}건</b><span>미집행</span><small>집행액 0원</small></div>
<div class="budget-overview-card"><b>{summary_partial:,}건</b><span>부분집행</span><small>집행 후 잔액 남음</small></div>
<div class="budget-overview-card"><b>{summary_sales_ready:,}건</b><span>조명·등주 · 잔액 있음</span><small>{esc(sales_ready_note)}</small></div>
</div>
{classification_coverage_notice}
<details class="budget-tech"><summary>수집자료 상세 숫자 보기</summary>
<div class="grid">
<div class="kpi"><b>{qwg_current:,}</b><span>QWGJK 현재자료</span></div>
<div class="kpi"><b>{aidfa_current:,}</b><span>AIDFA 현재자료</span></div>
<div class="kpi"><b>{education_current:,}</b><span>교육청 현재자료</span></div>
<div class="kpi"><b>{current_records:,}</b><span>현재 조건 조회자료</span></div>
<div class="kpi"><b>{summary_classified_count:,} / {summary_project_count:,}</b><span>{esc(classification_scope_label)}</span><small>분류대기 {summary_classification_pending:,}건</small></div>
<div class="kpi"><b>{observations:,}</b><span>{'변경이력 조회건' if history_requested else '변경이력 미조회'}</span></div>
<div class="kpi"><b>{esc(backend)}</b><span>저장소</span></div>
</div></details></section>
<section class="card"><h3>현재 예산사업</h3>
<p class="muted"><b>수집된 현재 예산자료 · 실제 세부사업</b>을 사업명·담당부서·예산·집행·잔액 순으로 쉽게 확인합니다. 이 화면은 외부 API를 호출하지 않습니다.</p>
<div class="budget-section-note">
<span><b>세부사업·집행</b> = 실제 사업명과 집행액이 있는 QWGJK 자료</span>
<span><b>미집행</b> = 집행액 0원</span>
<span><b>부분집행</b> = 집행액이 있고 잔액도 남음</span>
<span><b>전액집행</b> = 집행액이 있고 잔액이 없음</span>
<span><b>기타</b> = 조명·등주·전기·태양광 분류에 해당하지 않는 예산</span>
</div>
<div class="budget-current-desktop table budget-table"><table>
<tr><th>연도</th><th>지역 · 기관</th><th>예산유형</th><th>실제 사업 · 예산내용</th><th>분류</th><th>예산액</th><th>집행액</th><th>잔액</th></tr>
{detail_budget_rows_html or '<tr><td colspan="8">현재 조건의 세부사업 자료 없음</td></tr>'}
</table></div>
<div class="budget-current-mobile">
{detail_budget_cards_html or '<div class="muted">현재 조건의 세부사업 자료 없음</div>'}
</div>
{detail_paging}</section>


<section class="card"><h3>QWGJK 예산 변경이력 · 날짜조회</h3>
<p class="muted">저장된 QWGJK revision 이력을 조회합니다. 외부 API를 호출하지 않으며 AIDFA 구조예산은 이 날짜이력 표에 포함하지 않습니다. 예산·집행·잔액이 이전 저장 revision과 얼마나 바뀌었는지도 함께 표시합니다.</p>
<form class="row" method="get">
<input type="hidden" name="year" value="{year}">
<input type="hidden" name="category" value="{esc(category)}">
<input type="hidden" name="institution_name" value="{esc(institution_name)}">
<input type="hidden" name="history_submit" value="1">
<label>시작일<input name="history_start_date" type="date" min="2026-01-01" value="{esc(history_start_date)}"></label>
<label>종료일<input name="history_end_date" type="date" min="2026-01-01" value="{esc(history_end_date)}"></label>
<label>지역<select name="region">{''.join(region_options)}</select></label>
<label>기관·사업 검색<input name="history_q" value="{esc(history_query)}" placeholder="기관명·부서·사업명"></label>
<button class="primary">이력 조회</button></form>
<div class="budget-section-note">
<span><b>조회기간</b> {esc(history_start_date)} ~ {esc(history_end_date)}</span>
<span><b>조회건수</b> {len(history_rows):,}건</span>
<span><b>보존범위</b> QWGJK revision 최근 365일</span>
</div>
<div class="table budget-history-table"><table>
<tr><th>기준일</th><th>지역 · 기관</th><th>담당부서</th><th>실제 사업명</th><th>예산액</th><th>집행액</th><th>잔액</th><th>이전 revision 대비 변경</th></tr>
{history_rows_html if history_requested else '<tr><td colspan="8">날짜·지역·검색조건을 확인한 뒤 이력 조회 버튼을 누르면 저장된 QWGJK 변경이력을 조회합니다.</td></tr>'}
</table></div></section>

<section class="card budget-tech"><details><summary>편성 근거 보기 · AIDFA 기능별 구조예산 · 참고용</summary>
<p class="muted"><b>AIDFA 구조예산은 세부사업 예산이 아닙니다.</b> 분야·부문·회계별 편성 총액이며, 실제 QWGJK 세부사업과 구조가 정확히 맞을 때만 연결된 실제 QWGJK 세부사업으로 표시합니다.</p>
<div class="table budget-table"><table>
<tr><th>연도</th><th>지역 · 기관</th><th>예산유형</th><th>예산구조 · 연결 실제사업</th><th>분류</th><th>편성총액</th><th>집행액</th><th>잔액</th></tr>
{structural_budget_rows_html or '<tr><td colspan="8">현재 조건의 편성 구조예산 자료 없음</td></tr>'}
</table></div></details></section>
"""
    return layout("예산·영업후보", body, "예산·영업후보", user)


@app.get("/budget/project")
def budget_project_page(request: Request):
    """Read-only detail view for one stored current budget project."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)

    dataset = str(request.query_params.get("dataset", "") or "").strip()
    record_key = str(
        request.query_params.get("record_key", "") or ""
    ).strip()
    if not dataset or not record_key:
        return HTMLResponse("예산사업 식별정보가 없습니다.", status_code=400)

    import budget_read_vnext

    try:
        row = budget_read_vnext.budget_project_detail(dataset, record_key)
    except (ValueError, RuntimeError):
        row = None
    if not row:
        return HTMLResponse("저장된 현재 예산사업을 찾을 수 없습니다.", status_code=404)

    project_name = str(row.get("project_name") or "사업명 미수집")
    region = budget_read_vnext.row_region(row) or "지역 미확인"
    org_name = str(
        row.get("org_name") or row.get("institution_name") or "기관 미확인"
    )
    dept_name = str(row.get("dept_name") or "담당부서 미수집")
    budget_amount = int(
        row.get("budget_amount") or row.get("appropriation_amount") or 0
    )
    executed_amount = int(row.get("executed_amount") or 0)
    remaining_amount = int(row.get("remaining_amount") or 0)
    rate = (
        min(100.0, max(0.0, executed_amount / budget_amount * 100.0))
        if budget_amount > 0
        else 0.0
    )
    body = f"""
<section class="card"><div class="actions"><a class="btn" href="/budget">← 예산사업으로</a></div>
<h2>{esc(project_name)}</h2>
<p class="muted">저장된 현재 예산자료 상세입니다. 외부 API를 호출하지 않습니다.</p>
<div class="grid">
<div class="kpi"><b>{esc(region)}</b><span>지역</span></div>
<div class="kpi"><b>{esc(org_name)}</b><span>기관</span></div>
<div class="kpi"><b>{esc(dept_name)}</b><span>담당부서</span></div>
<div class="kpi"><b>{esc(_budget_category_label(row.get("primary_category")))}</b><span>분류</span></div>
</div></section>
<section class="card"><h3>사업 정보</h3><div class="table"><table>
<tr><th>사업코드</th><td>{esc(row.get("project_code") or "미수집")}</td><th>기준일</th><td>{esc(row.get("snapshot_date") or row.get("source_date") or "미수집")}</td></tr>
<tr><th>분야</th><td>{esc(row.get("field_name") or "미수집")}</td><th>부문</th><td>{esc(row.get("section_name") or "미수집")}</td></tr>
<tr><th>회계</th><td>{esc(row.get("account_name") or "미수집")}</td><th>최근 저장</th><td>{esc(row.get("last_seen_at") or row.get("updated_at") or "미수집")}</td></tr>
</table></div></section>
<section class="card"><h3>예산 · 집행</h3><div class="budget-overview-grid">
<div class="budget-overview-card"><b>{money(budget_amount)}</b><span>예산액</span></div>
<div class="budget-overview-card"><b>{money(executed_amount)}</b><span>집행액</span></div>
<div class="budget-overview-card"><b>{money(remaining_amount)}</b><span>잔액</span></div>
<div class="budget-overview-card"><b>{rate:.1f}%</b><span>집행률</span></div>
</div></section>
"""
    return layout("예산사업 상세", body, "예산·영업후보", user)


@app.get("/budget/export.xlsx")
def budget_export_xlsx(request: Request):
    """Export the current budget search result to a bounded XLSX workbook."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)

    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZoneInfo
    import budget_excel_vnext
    import budget_read_vnext

    year_text = str(request.query_params.get("year", "") or "").strip()
    year = int(year_text) if year_text.isdigit() else _dt.date.today().year
    region = str(
        request.query_params.get("region", "인천광역시") or ""
    ).strip()
    if region and region not in budget_read_vnext.REGIONS:
        region = "인천광역시"
    institution_name = str(
        request.query_params.get("institution_name", "") or ""
    ).strip()
    institution_scope = ""
    legacy_scope = str(
        request.query_params.get("institution_scope", "") or ""
    ).strip()
    if not institution_name and region == "인천광역시" and legacy_scope:
        import incheon_budget_scope_vnext
        institution_scope = incheon_budget_scope_vnext.normalize_scope(
            legacy_scope
        )
    category = str(
        request.query_params.get("category", "") or ""
    ).upper().strip()
    sales_priority = str(
        request.query_params.get("sales_priority", "") or ""
    ).strip() == "1"
    categories = (category,) if category in TARGET_CATEGORIES else None
    if sales_priority:
        categories = ("LIGHTING", "POLE")
    budget_query = str(
        request.query_params.get("budget_q", "") or ""
    ).strip()
    execution_status = str(
        request.query_params.get("execution_status", "") or ""
    ).strip().upper()
    if execution_status not in {"", "UNEXECUTED", "PARTIAL", "FULL"}:
        execution_status = ""
    sort_order = str(
        request.query_params.get("sort", "REMAINING_DESC") or "REMAINING_DESC"
    ).strip().upper()
    if sort_order not in {"REMAINING_DESC", "BUDGET_DESC", "RECENT", "ORG_ASC"}:
        sort_order = "REMAINING_DESC"

    export_limit = 10000
    page_size = 500
    rows = []
    offset = 0
    while len(rows) < export_limit:
        batch = budget_read_vnext.screen_budget_rows(
            fiscal_year=year,
            source_layers=("DETAIL_EXECUTION", "EDUCATION"),
            categories=categories,
            region=region,
            institution_scope=institution_scope,
            institution_name=institution_name,
            query=budget_query,
            execution_status=execution_status,
            remaining_positive=sales_priority,
            sort_order=sort_order,
            limit=min(page_size, export_limit - len(rows)),
            offset=offset,
        )
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < page_size:
            break

    export_rows = []
    for row in rows:
        item = dict(row)
        budget_amount = int(
            item.get("budget_amount") or item.get("appropriation_amount") or 0
        )
        executed_amount = int(item.get("executed_amount") or 0)
        item["region_display"] = budget_read_vnext.row_region(item)
        item["org_display"] = str(
            item.get("org_name") or item.get("institution_name") or ""
        )
        item["category_label"] = _budget_category_label(
            item.get("primary_category")
        )
        item["execution_rate"] = (
            executed_amount / budget_amount if budget_amount > 0 else 0.0
        )
        export_rows.append(item)

    data = budget_excel_vnext.build_budget_xlsx(
        export_rows,
        sheet_name=f"{year} 예산사업",
    )
    stamp = _dt.datetime.now(_ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    area = region or "전국"
    filename = f"예산사업_{year}_{area}_{stamp}.xlsx"
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''" + quote(filename)
            ),
            "X-G2B-Export-Rows": str(len(export_rows)),
            "X-G2B-Export-Limit": str(export_limit),
        },
    )


@app.get("/raw")
def raw_page(request: Request):
    """Retired in 4.1; stale bookmarks go to collection status."""
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    return RedirectResponse("/collection-monitor", 302)


def _credential_fingerprint(value):
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def _source_connection_display(prefix, credential):
    if not str(credential or "").strip():
        return "미설정", "API 키를 먼저 저장하세요"
    status = str(get_setting(f"{prefix}_api_connection_status", "") or "").upper()
    code = str(get_setting(f"{prefix}_api_connection_code", "") or "")
    stamp = str(get_setting(f"{prefix}_api_connection_at", "") or "")
    recorded_fingerprint = str(
        get_setting(f"{prefix}_api_connection_fingerprint", "") or ""
    )
    if status and recorded_fingerprint != _credential_fingerprint(credential):
        return "저장됨", "현재 API 키가 변경되어 실제 원천 API 재확인이 필요합니다"
    if status == "OK":
        detail = "실제 원천 API 응답 확인"
        if stamp:
            detail += " · " + stamp
        return "연결확인", detail
    if status == "FAILED":
        detail = "실제 API 확인 실패"
        if code:
            detail += " · " + code
        if stamp:
            detail += " · " + stamp
        return "오류", detail
    if status == "BLOCKED":
        detail = "키 저장됨 · 로컬 호출한도 때문에 재확인 대기"
        if stamp:
            detail += " · " + stamp
        return "대기", detail
    return "저장됨", "키는 저장됨 · 실제 원천 API 응답은 아직 확인하지 않음"


@app.get("/settings")
def settings_page(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    import budget_storage
    import g2b_database
    import lofin_vnext_http
    import readiness_vnext
    snapshot_meta = (
        result_snapshot_vnext.snapshot_metadata()
        if result_snapshot_vnext.snapshot_available()
        else {}
    )
    if is_result_server() and snapshot_meta:
        report = snapshot_meta.get("readiness") or {
            "status": "RESULT_SNAPSHOT",
            "deployment_state": "RESULT_SERVER",
        }
    else:
        report = readiness_vnext.build_readiness_report()
    snapshot_manifest = (
        snapshot_meta.get("manifest")
        if isinstance(snapshot_meta.get("manifest"), dict)
        else {}
    )
    sync_token_ready = bool(get_result_sync_token(""))
    budget_pg_configured = bool(budget_storage.storage_configured())
    budget_pg_ready = bool(budget_storage.storage_ready()) if budget_pg_configured else False
    budget_pg_error = str(budget_storage.storage_error_code() or "")
    budget_pg_state = "OK" if budget_pg_ready else ("연결대기" if budget_pg_configured else "미설정")
    db_source = (
        str(g2b_database.database_source_label() or "")
        if budget_pg_configured
        else ""
    )
    db_source_help = {
        "G2B_DATABASE_URL": "직접 G2B_DATABASE_URL",
        "DB_*": "Cafe24 DB_* 자동변수",
        "PG*": "PostgreSQL PG* 자동변수",
        "POSTGRES_URL": "플랫폼 POSTGRES_URL",
        "POSTGRESQL_URL": "플랫폼 POSTGRESQL_URL",
        "DATABASE_URL": "플랫폼 DATABASE_URL",
    }.get(db_source, db_source or "PostgreSQL 연결정보")
    budget_pg_help = (
        f"PostgreSQL 연결됨 · {db_source_help}"
        if budget_pg_ready
        else (
            f"{db_source_help} 감지됨 · "
            + (budget_pg_error or "연결 확인 필요")
            if budget_pg_configured
            else "PostgreSQL 연결정보 필요 · G2B_DATABASE_URL 또는 Cafe24 자동 DB 변수"
        )
    )
    g2b_key = get_service_key("")
    lofin_key = lofin_vnext_http.get_lofin_key()
    g2b_ready = bool(g2b_key)
    lofin_ready = bool(lofin_key)
    eduinfo_ready = bool(source_credential_configured("eduinfo_api_key"))
    g2b_state, g2b_help = _source_connection_display("g2b", g2b_key)
    lofin_state, lofin_help = _source_connection_display("lofin", lofin_key)
    eduinfo_help = (
        "키 설정됨 · live transport 검증 전 HOLD"
        if eduinfo_ready
        else "17개 시·도교육청용 API 키를 입력하세요"
    )
    backend_diag = backend_status()
    fresh_marker_ok = bool(backend_diag.get("fresh_start_marker_ok"))
    fresh_marker_value = str(
        backend_diag.get("fresh_start_marker_value") or ""
    )
    fresh_flag_enabled = str(
        os.getenv("G2B_V41_FRESH_START", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}
    fresh_marker_state = "정상" if fresh_marker_ok else "미확인"
    fresh_marker_help = (
        fresh_marker_value
        + (
            " · G2B_V41_FRESH_START 제거 가능"
            if fresh_flag_enabled
            else " · 재초기화 방지 marker 적용"
        )
        if fresh_marker_ok
        else "startup marker 미확인 · /ready 상태 확인"
    )
    saved = request.query_params.get("saved", "")
    error = request.query_params.get("error", "")
    flash = ""
    if str(saved).startswith("probe_"):
        source_label = "나라장터" if saved == "probe_g2b" else "지방재정365"
        flash = (
            '<div class="notice ok"><b>연결확인 완료:</b> '
            + esc(source_label)
            + ' 원천 API에서 정상 응답을 확인했습니다.</div>'
        )
    elif saved:
        flash = '<div class="notice"><b>저장 완료:</b> 입력한 API 키를 반영했습니다.</div>'
    elif error:
        flash = f'<div class="notice bad"><b>처리 실패:</b> {esc(error)}</div>'
    persistence_note = (
        '<p class="muted">저장한 키는 운영 저장소에 유지되며 화면에는 다시 표시하지 않습니다. '
        '배포 환경변수가 따로 설정되어 있으면 환경변수 값이 우선합니다.</p>'
        if db_is_persistent()
        else '<div class="notice bad"><b>주의:</b> 현재 DB가 비영구 경로라 재기동 시 관리자 저장키가 사라질 수 있습니다.</div>'
    )
    role_help = (
        "Cafe24 통합 운영"
        if is_unified()
        else ("Cafe24 호환 결과서버" if is_result_server() else "호환 로컬 수집기")
    )
    compatibility_kpis = (
        f'<div class="kpi"><b>{"SYNC" if snapshot_manifest else "대기"}</b>'
        f'<span>호환 결과 스냅샷</span><small>{esc(snapshot_manifest.get("generated_at_utc") or "없음")}</small></div>'
        f'<div class="kpi"><b>{"OK" if sync_token_ready else "미발급"}</b><span>호환 동기화 토큰</span></div>'
        if is_result_server() else ""
    )
    compatibility_section = (
        '<section class="card"><h3>호환 RESULT_SERVER 동기화</h3>'
        '<p class="muted">4.1 기본 운영경로가 아닙니다. 기존 분리형 배포를 되돌릴 때만 사용합니다.</p>'
        '<form method="post" action="/settings/result-sync-token">'
        + csrf_input(request,'/settings/result-sync-token')
        + '<button>호환 동기화 토큰 발급</button></form></section>'
        if is_result_server() else ""
    )
    body = f"""
{flash}
<section class="card"><h2>설정 · 운영상태</h2>
<div class="grid">
<div class="kpi"><b>{esc(runtime_role())}</b><span>실행 역할</span><small>{esc(role_help)}</small></div>
<div class="kpi"><b>{esc(APP_VERSION)}</b><span>운영 버전</span></div>
<div class="kpi"><b>{esc(build_commit_label())}</b><span>배포 HEAD</span><small>{'환경 SHA 확인' if runtime_build_commit() else 'G2B_BUILD_COMMIT 또는 GITHUB_SHA 필요'}</small></div>
<div class="kpi"><b>{'OK' if db_is_persistent() else '주의'}</b><span>웹 영구저장소</span><small>{'Cafe24 user_data 사용' if db_is_persistent() else '재기동 시 데이터 유실 가능'}</small></div>
<div class="kpi"><b>{esc(budget_pg_state)}</b><span>예산 PostgreSQL</span><small>{esc(budget_pg_help)}</small></div>
<div class="kpi"><b>{esc(fresh_marker_state)}</b><span>4.1 fresh-start marker</span><small>{esc(fresh_marker_help)}</small></div>
<div class="kpi"><b>{esc(g2b_state)}</b><span>나라장터 서비스키</span><small>{esc(g2b_help)}</small></div>
<div class="kpi"><b>{esc(lofin_state)}</b><span>지방재정365 키</span><small>{esc(lofin_help)}</small></div>
<div class="kpi"><b>{'KEY' if eduinfo_ready else '미설정'}</b><span>지방교육재정알리미 키</span><small>{esc(eduinfo_help)}</small></div>
<div class="kpi"><b>HOLD</b><span>교육청 live transport</span></div>
<div class="kpi"><b>HOLD</b><span>bulk historical</span></div>
{compatibility_kpis}
</div>
<div class="notice"><b>4.1 수집범위:</b> 예산은 정규화 필드만 PostgreSQL에 저장하고, 사업자료는 2026-01-01 이후 전국 조명·등주만 저장합니다. 용역·입찰 수집은 NO1로 분리했습니다.</div>
<p>readiness: <span class="pill">{esc(report.get('status'))}</span> · deployment: <span class="pill">{esc(report.get('deployment_state'))}</span></p></section>
{compatibility_section}
<section class="card"><h3>API 키 설정</h3>
{persistence_note}
<form method="post" action="/settings/keys">
{csrf_input(request,'/settings/keys')}
<label>나라장터 서비스키
<input type="password" name="g2b_service_key" autocomplete="off" placeholder="새 서비스키 입력 · 빈칸은 기존값 유지">
</label>
<label>지방재정365 API 키
<input type="password" name="lofin_api_key" autocomplete="off" placeholder="새 API 키 입력 · 빈칸은 기존값 유지">
</label>
<label>지방교육재정알리미 API 키
<input type="password" name="eduinfo_api_key" autocomplete="off" placeholder="17개 시·도교육청 API 키 입력 · 빈칸은 기존값 유지">
</label>
<p class="muted">{esc("호환 RESULT_SERVER에서는 원천수집을 실행하지 않습니다." if is_result_server() else "Cafe24 통합 운영에서 나라장터 조명·등주와 지방재정 예산을 직접 수집합니다. 교육청 예산 live 수집은 검증 전까지 HOLD입니다.")}</p>
<div class="actions">
<button class="primary" name="action" value="save">입력한 키 저장</button>
<button name="action" value="clear_g2b">나라장터 저장키 삭제</button>
<button name="action" value="clear_lofin">지방재정365 저장키 삭제</button>
<button name="action" value="clear_eduinfo">교육재정 저장키 삭제</button>
</div>
</form>
<div class="notice"><b>실제 연결 확인:</b> 아래 버튼은 저장 없이 원천 API를 각각 1회만 조회하여 인증·통신 상태를 확인합니다. 수집자료는 만들지 않습니다.</div>
<div class="actions">
<form method="post" action="/settings/probe-source" style="display:inline">
{csrf_input(request,'/settings/probe-source')}
<input type="hidden" name="source" value="g2b">
<button>나라장터 API 연결 확인</button>
</form>
<form method="post" action="/settings/probe-source" style="display:inline">
{csrf_input(request,'/settings/probe-source')}
<input type="hidden" name="source" value="lofin">
<button>지방재정365 API 연결 확인</button>
</form>
</div></section>
<section class="card"><h3>저장정책</h3><div class="notice">원문 JSON 비저장 · 과거 예산 변경이력 1년 · 미래예산 보호 · 조명·등주 사업자료 2026-01-01 이후 · 27개월 보관</div></section>
"""
    return layout("설정", body, "설정", user)


@app.post("/settings/probe-source")
async def settings_probe_source(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if not valid_csrf(request, "/settings/probe-source", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    if is_result_server():
        return RedirectResponse(
            "/settings?error=" + quote("RESULT_SERVER에서는 원천 API 연결확인을 실행하지 않습니다."),
            303,
        )

    source = str(data.get("source") or "").strip().lower()
    try:
        import datetime as _dt
        from zoneinfo import ZoneInfo as _ZoneInfo
        from vnext_source_guard import (
            operational_budget_source_context,
            operational_recent_source_context,
        )

        today = _dt.datetime.now(_ZoneInfo("Asia/Seoul")).date()
        if source == "g2b":
            if not get_service_key(""):
                raise ValueError("나라장터 서비스키가 설정되지 않았습니다.")
            day = today - _dt.timedelta(days=1)
            import shopping_vnext
            with operational_recent_source_context(
                collection_date=day.isoformat(),
                max_requests=1,
            ):
                shopping_vnext.fetch_page(
                    day.isoformat(),
                    day.isoformat(),
                    page=1,
                    rows=1,
                )
        elif source == "lofin":
            import lofin_vnext_http
            if not lofin_vnext_http.get_lofin_key():
                raise ValueError("지방재정365 API 키가 설정되지 않았습니다.")
            import budget_vnext
            with operational_budget_source_context(
                snapshot_date=today.isoformat(),
                max_requests=1,
            ):
                budget_vnext.fetch_page(
                    today.year,
                    today.isoformat(),
                    page=1,
                    size=1,
                )
        else:
            raise ValueError("지원하지 않는 연결확인 대상입니다.")
    except ValueError as exc:
        return RedirectResponse("/settings?error=" + quote(str(exc)), 303)
    except Exception as exc:
        import vnext_collection
        safe = vnext_collection._safe_error_label(exc)
        return RedirectResponse(
            "/settings?error=" + quote("API 연결확인 실패: " + safe),
            303,
        )
    return RedirectResponse("/settings?saved=probe_" + source, 303)


@app.post("/settings/result-sync-token")
async def settings_result_sync_token(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if not valid_csrf(request, "/settings/result-sync-token", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    if not is_result_server():
        return HTMLResponse("RESULT_SYNC_TOKEN_IS_FOR_RESULT_SERVER", status_code=409)
    token = secrets.token_urlsafe(48)
    set_source_credential("result_sync_token", token)
    base = str(request.base_url).rstrip("/")
    command = (
        "python scripts/local_collector.py "
        "--g2b-key YOUR_G2B_KEY "
        f"--server {base} "
        f"--token {token} "
        "--interval-minutes 120"
    )
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>결과 동기화 토큰</title><style>{STYLE}</style></head><body><main class="wrap">
<section class="card"><h2>결과 동기화 토큰 발급 완료</h2>
<div class="notice bad"><b>이 토큰은 지금 복사해 두십시오.</b> 다시 표시되지 않으며 새로 발급하면 기존 토큰은 즉시 무효화됩니다.</div>
<label>동기화 토큰<input value="{esc(token)}" readonly></label>
<label>로컬 PC 실행 예시<input value="{esc(command)}" readonly style="width:100%"></label>
<p class="muted">YOUR_G2B_KEY 부분에 공공데이터포털 나라장터 키를 넣습니다. 로컬 DB 기본 위치는 local_data/g2b-local.sqlite3 입니다.</p>
<p><a class="btn" href="/settings">설정으로 돌아가기</a></p>
</section></main></body></html>"""
    )


@app.post("/settings/compact-result-server")
async def compact_result_server(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if not valid_csrf(request, "/settings/compact-result-server", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    if not is_result_server():
        return HTMLResponse("COMPACTION_IS_FOR_RESULT_SERVER", status_code=409)
    if str(data.get("confirm") or "").strip() != "RESULT_ONLY":
        return RedirectResponse("/settings?error=" + quote("RESULT_ONLY를 정확히 입력해 주세요."), 303)
    import result_server_maintenance
    try:
        result = result_server_maintenance.compact_result_server_source_data()
    except RuntimeError as exc:
        if str(exc) == "RESULT_SNAPSHOT_REQUIRED_BEFORE_COMPACTION":
            return RedirectResponse(
                "/settings?error=" + quote("먼저 로컬 결과 스냅샷을 동기화해 주세요."),
                303,
            )
        raise

    if str(result.get("status") or "") == "SKIPPED_POSTGRESQL":
        return HTMLResponse(
            f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>결과서버 경량화 불필요</title><style>{STYLE}</style></head><body><main class='wrap'>
<section class='card'><h2>결과서버 경량화 불필요</h2>
<div class='notice ok'>현재 운영 저장소는 PostgreSQL입니다. 원천 SQLite 테이블을 삭제하거나 VACUUM할 대상이 없으므로 아무 작업도 수행하지 않았습니다.</div>
<p><a class='btn' href='/settings'>설정으로 돌아가기</a></p>
</section></main></body></html>"""
        )

    freed = max(0, int(result.get("bytes_before") or 0) - int(result.get("bytes_after") or 0))
    return HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>결과서버 경량화 완료</title><style>{STYLE}</style></head><body><main class="wrap">
<section class="card"><h2>결과서버 경량화 완료</h2>
<div class="grid">
<div class="kpi"><b>{len(result.get('dropped_tables') or []):,}</b><span>정리한 원천 테이블</span></div>
<div class="kpi"><b>{freed / (1024*1024):.1f} MB</b><span>회수된 파일 용량</span></div>
<div class="kpi"><b>{'완료' if result.get('vacuumed') else '보류'}</b><span>VACUUM</span></div>
</div>
<div class="notice ok">관리자·설정·동기화 토큰과 compact 결과 스냅샷은 유지했습니다. 4.1 운영은 원문 JSON을 보관하지 않습니다.</div>
<p><a class="btn" href="/settings">설정으로 돌아가기</a></p>
</section></main></body></html>"""
    )


def _result_sync_bearer(request: Request):
    value = str(request.headers.get("Authorization", "") or "")
    prefix = "Bearer "
    return value[len(prefix):].strip() if value.startswith(prefix) else ""


async def _spool_bounded_result_sync_body(request):
    """Stream a bounded upload directly to a temporary file.

    The web process must not retain the compressed snapshot while a child process
    expands and parses it. This keeps RESULT_SERVER memory close to its idle RSS.
    """
    fd, path = tempfile.mkstemp(prefix="g2b-result-sync-", suffix=".bin")
    total = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_RESULT_SYNC_COMPRESSED_BYTES:
                    raise ValueError("COMPRESSED_SNAPSHOT_TOO_LARGE")
                handle.write(chunk)
        return path, total
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


async def _read_bounded_result_sync_body(request):
    """Compatibility test helper; production endpoint uses disk spooling."""
    path, _size = await _spool_bounded_result_sync_body(request)
    try:
        return open(path, "rb").read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _result_sync_memory_ok():
    state = memory_guard.snapshot(collect=True)
    return bool(
        state.get("guard_ok", False)
        and int(state.get("cgroup_oom_group") or 0) == 0
    )


def _run_isolated_result_sync_worker(input_path, encoding):
    fd, result_path = tempfile.mkstemp(
        prefix="g2b-result-sync-result-", suffix=".json"
    )
    os.close(fd)
    try:
        env = os.environ.copy()
        env["G2B_AUTO_SYNC"] = "0"
        env["G2B_AUTO_SYNC_DISABLE"] = "1"
        env["G2B_POST_BOOT_MAINTENANCE_ENABLE"] = "0"
        env["G2B_MATCH_ROLLOVER_AUTO_ENABLE"] = "0"
        env["G2B_V41_FRESH_START"] = "0"
        env["G2B_SERVING_DB_PATH"] = result_snapshot_vnext.serving_db_path()
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-u",
                "-m",
                "g2b_result_sync_worker",
                str(input_path),
                str(encoding or ""),
                str(result_path),
            ],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode == 0:
            with open(result_path, "r", encoding="utf-8") as handle:
                manifest = json.loads(handle.read())
            return 200, {"ok": True, "manifest": manifest}
        if completed.returncode < 0:
            print(
                "G2B_RESULT_SYNC_WORKER_SIGNAL",
                completed.returncode,
                flush=True,
            )
            return 503, {"ok": False, "error": "MEMORY_PRESSURE"}
        if completed.returncode in {75, 76}:
            return 503, {"ok": False, "error": "MEMORY_PRESSURE"}
        if completed.returncode == 73:
            return 400, {"ok": False, "error": "INVALID_SNAPSHOT"}
        print(
            "G2B_RESULT_SYNC_WORKER_FAILED",
            completed.returncode,
            str(completed.stdout or "")[-500:],
            flush=True,
        )
        return 500, {"ok": False, "error": "RESULT_SYNC_FAILED"}
    except subprocess.TimeoutExpired:
        print("G2B_RESULT_SYNC_WORKER_TIMEOUT", flush=True)
        return 503, {"ok": False, "error": "RESULT_SYNC_TIMEOUT"}
    finally:
        for candidate in (input_path, result_path):
            try:
                os.unlink(candidate)
            except OSError:
                pass


def _decode_result_sync_body(body, encoding):
    if len(body) > MAX_RESULT_SYNC_COMPRESSED_BYTES:
        raise ValueError("COMPRESSED_SNAPSHOT_TOO_LARGE")
    if str(encoding or "").lower().strip() == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body), mode="rb") as handle:
                raw = handle.read(MAX_RESULT_SYNC_JSON_BYTES + 1)
        except OSError:
            raise ValueError("INVALID_GZIP_SNAPSHOT") from None
    else:
        raw = body
    if len(raw) > MAX_RESULT_SYNC_JSON_BYTES:
        raise ValueError("SNAPSHOT_JSON_TOO_LARGE")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise ValueError("INVALID_SNAPSHOT_JSON") from None
    return payload


@app.post("/api/result-sync")
async def api_result_sync(request: Request):
    if not is_result_server():
        return JSONResponse({"ok": False, "error": "NOT_RESULT_SERVER"}, 409)
    expected = str(get_result_sync_token("") or "")
    supplied = _result_sync_bearer(request)
    if len(expected) < 32:
        return JSONResponse({"ok": False, "error": "SYNC_TOKEN_NOT_CONFIGURED"}, 503)
    if not supplied or not secrets.compare_digest(expected, supplied):
        return JSONResponse({"ok": False, "error": "SYNC_AUTH_FAILED"}, 401)
    length = str(request.headers.get("Content-Length", "") or "").strip()
    if length.isdigit() and int(length) > MAX_RESULT_SYNC_COMPRESSED_BYTES:
        return JSONResponse({"ok": False, "error": "SNAPSHOT_TOO_LARGE"}, 413)
    try:
        if not _result_sync_memory_ok():
            return JSONResponse(
                {"ok": False, "error": "MEMORY_PRESSURE"},
                status_code=503,
            )
        input_path, _size = await _spool_bounded_result_sync_body(request)
        status_code, outcome = await asyncio.to_thread(
            _run_isolated_result_sync_worker,
            input_path,
            request.headers.get("Content-Encoding", ""),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:120]}, 400)
    except Exception as exc:
        print("G2B_RESULT_SYNC_FAILED", type(exc).__name__, flush=True)
        return JSONResponse({"ok": False, "error": "RESULT_SYNC_FAILED"}, 500)

    if status_code != 200:
        return JSONResponse(outcome, status_code=status_code)
    manifest = dict(outcome.get("manifest") or {})
    return {
        "ok": True,
        "snapshot_id": manifest["snapshot_id"],
        "generated_at_utc": manifest["generated_at_utc"],
        "row_counts": manifest["row_counts"],
        "total_rows": manifest["total_rows"],
    }


def _save_source_key_updates(*, g2b_key="", lofin_key="", eduinfo_key=""):
    """Persist entered source keys and wake collection when a live source becomes ready."""
    changed = False
    should_wake = False
    if str(g2b_key or "").strip():
        set_source_credential("g2b_service_key", str(g2b_key).strip())
        changed = True
        should_wake = True
    if str(lofin_key or "").strip():
        set_source_credential("lofin_api_key", str(lofin_key).strip())
        changed = True
        should_wake = True
    if str(eduinfo_key or "").strip():
        set_source_credential("eduinfo_api_key", str(eduinfo_key).strip())
        changed = True
    if should_wake:
        _RECENT_COLLECTION_WAKE.set()
    return changed


@app.post("/settings/keys")
async def settings_keys_submit(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    if not valid_csrf(request, "/settings/keys", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    action = str(data.get("action") or "save")
    try:
        if action == "clear_g2b":
            set_source_credential("g2b_service_key", "")
        elif action == "clear_lofin":
            set_source_credential("lofin_api_key", "")
        elif action == "clear_eduinfo":
            set_source_credential("eduinfo_api_key", "")
        elif action == "save":
            changed = _save_source_key_updates(
                g2b_key=data.get("g2b_service_key"),
                lofin_key=data.get("lofin_api_key"),
                eduinfo_key=data.get("eduinfo_api_key"),
            )
            if not changed:
                raise ValueError("저장할 키를 하나 이상 입력해 주세요.")
        else:
            raise ValueError("지원하지 않는 설정 작업입니다.")
    except ValueError as exc:
        return RedirectResponse("/settings?error=" + quote(str(exc)), 303)
    return RedirectResponse("/settings?saved=1", 303)


@app.post("/organize/budget")
async def organize_budget(request: Request):
    user = require_user(request)
    if not user:
        return RedirectResponse("/login", 302)
    if is_result_server():
        return JSONResponse({"ok": False, "error": "ORGANIZE_RUNS_ON_LOCAL_PC"}, status_code=409)
    data = await form_data(request)
    if not valid_csrf(request, "/organize/budget", data.get("_csrf")):
        return HTMLResponse("CSRF validation failed", status_code=403)
    import budget_reorganize_vnext
    budget_reorganize_vnext.reorganize_existing_budget_raw()
    return RedirectResponse("/budget", 303)


@app.get("/api/status")
def api_status(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        meta = result_snapshot_vnext.snapshot_metadata()
        return meta.get("readiness") or {
            "status": "RESULT_SNAPSHOT",
            "status_scope": "LOCAL_COLLECTOR_RESULT_ONLY",
        }
    import readiness_vnext
    return readiness_vnext.build_readiness_report()


@app.get("/api/collection-status")
def api_collection_status(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    return _runtime_collection_snapshot()


@app.get("/api/shopping")
def api_shopping(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    q, _category, categories, limit, _opts = _query_options(request)
    start_date, end_date = _shopping_date_range(request)
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        return result_snapshot_vnext.query_rows(
            "shopping",
            categories=categories,
            query=q,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    import procurement_read_vnext
    return procurement_read_vnext.shopping_rows(
        categories=categories,
        query=q,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


@app.get("/api/vendors")
def api_vendors(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    q = str(request.query_params.get("q", "") or "")
    import procurement_read_vnext
    region = str(request.query_params.get("region", "") or "").strip()
    if region and region not in procurement_read_vnext.REGIONS:
        return JSONResponse({"ok": False, "error": "INVALID_REGION"}, 400)
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        return _result_snapshot_vendor_rows(
            query=q,
            region=region,
            limit=1000,
        )
    return procurement_read_vnext.vendor_rows(
        query=q,
        region=region,
        limit=1000,
    )


@app.get("/api/budget")
def api_budget(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    year_text = str(request.query_params.get("year", "") or "").strip()
    year = int(year_text) if year_text.isdigit() else None
    import budget_read_vnext
    region = str(request.query_params.get("region", "") or "").strip()
    if region and region not in budget_read_vnext.REGIONS:
        return JSONResponse({"ok": False, "error": "INVALID_REGION"}, 400)
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        targets = _result_snapshot_budget_rows(
            section="budget_targets",
            categories=None,
            fiscal_year=year,
            region=region,
            limit=500,
        )
        prebid = _result_snapshot_budget_rows(
            section="budget_prebid",
            categories=None,
            fiscal_year=year,
            region=region,
            limit=500,
        )
        return {
            "target_rows": targets,
            "prebid_rows": prebid,
            "selected_region": region,
            "source": "LOCAL_RESULT_SNAPSHOT",
            "no1_boundary": "입찰·용역·낙찰·계약은 NO1 담당",
        }
    # Ordinary API reads must never materialize a whole fiscal-year
    # analysis snapshot in the 256 MiB web process.
    return budget_read_vnext.bounded_budget_api_model(
        fiscal_year=year,
        region=region,
        limit=500,
    )
