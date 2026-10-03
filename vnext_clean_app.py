"""Production web runtime for SINSUNG G2B vNext 4.1.

Production serves normalized budget/business records and read models. Source JSON
RAW is not an operating storage layer. External source traffic remains safety-gated
and is never triggered by read-only pages.
"""
from __future__ import annotations

import gzip
import hashlib
import html
import io
import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
    can_collect_sources,
    is_local_collector,
    is_result_server,
    is_unified,
    runtime_role,
)
import result_snapshot_vnext
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
MAX_RESULT_SYNC_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_RESULT_SYNC_JSON_BYTES = 128 * 1024 * 1024


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
BUDGET_SYNC_MAX_PAGES = _env_int(
    "G2B_BUDGET_SYNC_MAX_PAGES", 256, lower=1, upper=512
)
BUDGET_SYNC_MAX_REQUESTS = _env_int(
    "G2B_BUDGET_SYNC_MAX_REQUESTS", 320, lower=1, upper=512
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
    # Start operational shopping collection only after storage/schema are ready.
    # Tests and deployments with G2B_AUTO_SYNC=0 remain source-I/O free.
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


def _auto_sync_enabled():
    # Fail closed: recurring collection runs only after an explicit enable.
    raw = str(os.getenv("G2B_AUTO_SYNC", "0") or "0").lower().strip()
    return (
        can_collect_sources()
        and not TEST_MODE
        and raw in ("1", "true", "yes", "on")
    )


def recent_collection_status():
    with _RECENT_COLLECTION_LOCK:
        state = dict(_RECENT_COLLECTION_STATE)
        thread = _RECENT_COLLECTION_THREAD
    state["thread_alive"] = bool(thread and thread.is_alive())
    with _MANUAL_COLLECTION_LOCK:
        manual_shopping_running = bool(
            _MANUAL_COLLECTION_THREADS.get("shopping")
            and _MANUAL_COLLECTION_THREADS["shopping"].is_alive()
        )
        manual_budget_running = bool(
            _MANUAL_COLLECTION_THREADS.get("budget")
            and _MANUAL_COLLECTION_THREADS["budget"].is_alive()
        )
    state["manual_shopping_running"] = manual_shopping_running
    state["manual_budget_running"] = manual_budget_running
    state["manual_sources_running"] = int(
        manual_shopping_running
    ) + int(manual_budget_running)
    if state["manual_sources_running"] > 0:
        # The compatibility/global state must never report COMPLETE while one of
        # the independent manual source workers is still active.
        state["state"] = "RUNNING"
    state["auto_sync_enabled"] = _auto_sync_enabled()
    state["order"] = "FORWARD"
    state["start_date"] = "2026-09-01"
    state["interval_seconds"] = SHOPPING_SYNC_INTERVAL_SECONDS
    state["shopping_scope"] = "LIGHTING_AND_POLE_ONLY"
    state["budget_scope"] = "NORMALIZED_BUDGET_POSTGRESQL"
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
    if value in {"RUNNING", "PARTIAL", "INCOMPLETE"}:
        return "PARTIAL"
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
        "WAITING_KEYS",
        "WAITING_QUOTA",
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

    # 1) Shopping: nationwide scan, normalized lighting/pole records only.
    if run_shopping and get_service_key(""):
        try:
            import shopping_recent_vnext
            shopping = shopping_recent_vnext.collect_forward(
                start_date="2026-09-01",
                max_days=SHOPPING_SYNC_DAYS_PER_RUN,
                recheck_days=SHOPPING_RECHECK_DAYS,
                longtail_recheck_days_per_run=(
                    SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN
                ),
                retention_days=SHOPPING_RETENTION_DAYS,
                # Production shopping is classified deterministically while each
                # normalized row is persisted. Avoid repeated post-classification
                # calls for every completed date during large catch-up runs.
                defer_classification=True,
            )
            outcomes["shopping"] = shopping
            _set_recent_collection_state(
                shopping_status=str(shopping.get("status") or "COMPLETE")
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
    if run_shopping:
        try:
            import shopping_store_v41
            outcomes["shopping_retention"] = shopping_store_v41.purge_history(
                SHOPPING_RETENTION_DAYS,
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

    if not budget_ready:
        _set_recent_collection_state(
            future_budget_status="WAITING_POSTGRES",
            current_appropriation_status="WAITING_POSTGRES",
            budget_status="WAITING_POSTGRES",
            budget_history_status="WAITING_POSTGRES",
        )
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
        any_budget_collected = False
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
                pending_day = budget_vnext.pending_nationwide_snapshot_date(
                    today=today
                )
                snapshot_day = pending_day or today
                outcomes["budget_snapshot_date"] = snapshot_day.isoformat()
                outcomes["budget_resume_pending"] = bool(pending_day)
                _set_recent_collection_state(
                    budget_snapshot_date=snapshot_day.isoformat()
                )
                with operational_budget_source_context(
                    snapshot_date=snapshot_day.isoformat(),
                    max_requests=current_request_budget,
                ):
                    budget = budget_vnext.collect_full_budget(
                        snapshot_day.year,
                        snapshot_day.isoformat(),
                        page_size=1000,
                        max_pages=min(
                            BUDGET_SYNC_MAX_PAGES,
                            current_request_budget,
                        ),
                        resume=True,
                    )
                outcomes["budget"] = budget
                current_status = str(budget.get("status") or "COMPLETE")
                any_budget_collected = True
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
                            max_pages=min(
                                BUDGET_SYNC_MAX_PAGES,
                                history_remaining,
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

        if any_budget_collected:
            # One pass rebuilds projection/classification for current and future
            # normalized budget state without making additional source requests.
            budget_reorganize_vnext.reorganize_existing_budget_raw()

        states = {
            future_status,
            current_appropriation_status,
            current_status,
            history_status,
        }
        if "FAILED" in states:
            combined_budget_status = "FAILED"
        elif states & {"RUNNING", "PARTIAL", "INCOMPLETE", "WAITING_QUOTA"}:
            combined_budget_status = "PARTIAL"
        elif states == {"COMPLETE"}:
            combined_budget_status = "COMPLETE"
        else:
            combined_budget_status = "PARTIAL"
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
            if int(purged.get("expired_current_records") or 0) > 0:
                import budget_projection_vnext
                outcomes["budget_read_model_prune"] = (
                    budget_projection_vnext.prune_stale_budget_read_model()
                )
        except Exception as exc:
            failures.append(("budget_retention", type(exc).__name__))
            _set_recent_collection_state(
                last_error=f"BUDGET_RETENTION:{type(exc).__name__}",
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
            if name != "shopping"
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
    print(
        "G2B_OPERATIONAL_SYNC",
        state,
        _RECENT_COLLECTION_STATE.get("shopping_status"),
        _RECENT_COLLECTION_STATE.get("budget_status"),
        flush=True,
    )
    return outcomes


def _run_recent_collection_once(source="all"):
    """Run at most one selected source cycle across overlapping processes."""
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

        # Manual API-specific cycles share the global gate with each other, but
        # conflict with the exclusive automatic all-source cycle. Their own
        # exclusive source lease still prevents duplicate shopping or budget runs.
        # The lease belongs to the canonical DB, not the budget schema.
        with g2b_database.operational_cycle_lease(
            "g2b_v41_operational_cycle",
            shared=True,
        ) as global_shared:
            if not global_shared:
                return lease_held_result()
            with g2b_database.operational_cycle_lease(
                f"g2b_v41_manual_{source}",
                shared=False,
            ) as acquired:
                if not acquired:
                    return lease_held_result()
                lease_acquired = True
                return _run_recent_collection_once_impl(source=source)
    except Exception as exc:
        # Once the process lease has been acquired, failures belong to the cycle
        # itself and must reach the existing worker-level safety net unchanged.
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


def _recent_collection_worker():
    global _RECENT_COLLECTION_THREAD
    current_thread = threading.current_thread()
    try:
        while True:
            outcome = None
            try:
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

            # force=True is also used for a manual one-shot while AUTO_SYNC=0.
            # In that mode the first cycle must not silently turn into a recurring
            # background collector.
            if not _auto_sync_enabled():
                return

            # During a rolling deploy the replacement process may briefly lose the
            # cross-process advisory lease to the old process. Retry that condition
            # promptly instead of sleeping for the normal multi-hour collection interval.
            lease_state = (
                str((outcome or {}).get("operational_cycle_lease") or "")
                if isinstance(outcome, dict)
                else ""
            )
            wait_seconds = (
                OPERATIONAL_LEASE_RETRY_SECONDS
                if lease_state in {"HELD_BY_OTHER_PROCESS", "UNAVAILABLE"}
                else SHOPPING_SYNC_INTERVAL_SECONDS
            )

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
@media(max-width:640px){.wrap{padding:10px}.card{padding:14px}.top{padding:14px}.brand{font-size:19px}th,td{padding:9px;font-size:12px}}
"""


@app.middleware("http")
async def backend_gate(request: Request, call_next):
    # Platform liveness/root probes must never wait on persistent storage.
    if request.url.path not in {"/", "/health", "/__ai_space_health", "/live", "/ready"}:
        state = backend_status()
        if not state["backend_ok"]:
            schedule_backend_init()
            state = backend_status()
            return _secure(
                HTMLResponse(
                    "<h2>G2B vNext 저장소 초기화 대기</h2>"
                    "<p>웹 프로세스는 정상 기동했습니다. 데이터 저장소 연결을 백그라운드에서 준비 중입니다.</p>"
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
<div class="sub">미래예산 수집 → 기관·사업 정리 → 조명·등주 후보 · 사업자료 2026-09-01 이후</div></header>
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
        "runtime": "G2B_VNEXT_CLEAN",
        "version": APP_VERSION,
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
        "result_snapshot_active": result_snapshot_vnext.snapshot_available(),
        "version": APP_VERSION,
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
        return HTMLResponse(
            "<!doctype html><html lang='ko'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>SINSUNG G2B vNext</title>"
            "<body style='font-family:sans-serif;padding:32px'>"
            "<h2>SINSUNG G2B vNext</h2>"
            "<p>웹 서버가 기동되었습니다. 데이터 저장소를 준비 중입니다.</p>"
            "<p><a href='/health'>상태 확인</a></p></body></html>",
            status_code=200,
        )
    # Keep the platform root probe DB-free. /login resolves setup/session state.
    return RedirectResponse("/login", 302)


@app.get("/setup")
def setup_page(request: Request):
    if not users_empty():
        return RedirectResponse("/login", 302)
    error = request.query_params.get("error", "")
    flash = f'<div class="notice bad">{esc(error)}</div>' if error else ""
    setup_token = secrets.token_urlsafe(32)
    response = HTMLResponse(
        f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>G2B vNext 관리자 설정</title><style>{STYLE}</style></head><body><section class="auth">
<h2>G2B vNext 최초 관리자</h2>
<p class="muted">관리자 계정이 아직 없을 때만 이 화면이 열립니다. 아이디와 비밀번호를 입력해 최초 관리자를 생성하세요.</p>
{flash}<form method="post" action="/setup">
<input type="hidden" name="_setup_csrf" value="{esc(setup_token)}">
<label>아이디<input name="username" minlength="4" required></label>
<label>비밀번호<input type="password" name="password" minlength="10" required></label>
<label>비밀번호 확인<input type="password" name="confirm" minlength="10" required></label>
<button class="primary">관리자 생성</button></form></section></body></html>"""
    )
    response.set_cookie(
        SETUP_COOKIE,
        setup_token,
        max_age=15 * 60,
        httponly=True,
        secure=not TEST_MODE,
        samesite="strict",
        path="/setup",
    )
    return response


@app.post("/setup")
async def setup_submit(request: Request):
    if not users_empty():
        return RedirectResponse("/login", 302)
    data = await form_data(request)
    cookie_token = str(request.cookies.get(SETUP_COOKIE, "") or "")
    form_token = str(data.get("_setup_csrf") or "")
    if not cookie_token or not form_token or not secrets.compare_digest(cookie_token, form_token):
        return HTMLResponse("SETUP_CSRF_VALIDATION_FAILED", status_code=403)
    if data.get("password") != data.get("confirm"):
        return RedirectResponse("/setup?error=" + quote("비밀번호 확인이 일치하지 않습니다."), 302)
    try:
        create_admin(data.get("username"), data.get("password"))
    except ValueError as exc:
        return RedirectResponse("/setup?error=" + quote(str(exc)), 302)
    response = RedirectResponse("/login", 302)
    response.delete_cookie(
        SETUP_COOKIE,
        path="/setup",
        secure=not TEST_MODE,
        httponly=True,
        samesite="strict",
    )
    return response


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
<div class="notice"><b>운영 원칙:</b> {esc("호환 RESULT_SERVER: 로컬 결과 스냅샷만 표시합니다." if is_result_server() else ("Cafe24 통합 운영: 예산은 정규화해 PostgreSQL에 저장하고, 사업자료는 2026-09-01 이후 전국 조명·등주만 저장합니다." if is_unified() else "호환 로컬 수집기 모드입니다."))}</div>
{warning_html}</section>
<div class="grid">
<div class="kpi"><b>{esc(APP_VERSION)}</b><span>운영 버전</span></div>
<div class="kpi"><b>{'OK' if db_is_persistent() else '주의'}</b><span>영구 저장소</span></div>
<div class="kpi"><b>{total:,}</b><span>현재 유효 저장자료</span></div>
<div class="kpi"><b>{target.get('shopping_delivery',0):,}</b><span>현재 대상 납품요구</span></div>
<div class="kpi"><b>{history_by_name.get('shopping_delivery',0):,}</b><span>보존 납품요구 이력</span></div>
<div class="kpi"><b>{inactive_by_name.get('shopping_delivery',0):,}</b><span>비활성 납품요구 이력</span></div>
<div class="kpi"><b>{target.get('budget',0)+target.get('education_budget',0):,}</b><span>대상 예산사업</span></div>
</div>
<section class="card"><h3>수집 준비상태</h3>
<p><span class="pill">{esc(readiness.get("status"))}</span> · {esc(readiness.get("status_scope"))}</p>
<p class="muted">예산 정규화 자료와 2026-09-01 이후 조명·등주 사업자료만 운영수집합니다. 용역·입찰은 NO1 담당이며 bulk historical과 교육청 live transport는 HOLD입니다.</p>
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
    return f"""
<div class="stage-card">
  <div class="stage-head">
    <div><div class="stage-number">{esc(stage.get('number'))} · {esc(stage.get('group'))}</div>
    <div class="stage-title">{esc(stage.get('label'))}</div></div>
    <span class="stage-state {_collector_state_class(stage.get('state'))}">{esc(stage.get('state_label'))}</span>
  </div>
  <div class="progress"><span style="width:{max(0.0,min(width,100.0)):.1f}%"></span></div>
  <div class="stage-message">{esc(stage.get('message'))}</div>
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
            and str(stage.get("state") or "") in {"RUNNING", "STALE", "PARTIAL"}
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
    shopping_running = bool(runtime_sources.get("manual_shopping_running"))
    budget_running = bool(runtime_sources.get("manual_budget_running"))
    source_state_labels = {
        "IDLE": "대기",
        "RUNNING": "실행중",
        "COMPLETE": "완료",
        "PARTIAL": "부분완료",
        "FAILED": "오류",
        "WAITING_KEYS": "키대기",
        "WAITING_STORAGE": "저장소대기",
        "WAITING_QUOTA": "호출한도대기",
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
    summary = snapshot.get("summary") or {
        "running": 0, "complete": 0, "stage_count": 0,
        "errors": 0, "total_raw": 0, "last_activity": "",
    }
    stages = "".join(_collector_stage_html(stage) for stage in (snapshot.get("stages") or []))
    recent_rows = "".join(
        f"<tr><td>{esc(row['updated_at'])}</td><td>{esc(row['label'])}</td>"
        f"<td>{esc(row['scope'])}</td><td>{esc(row['status_label'])}</td>"
        f"<td class='num'>{int(row['pages_processed']):,}</td>"
        f"<td class='num'>{int(row['saved_count']):,}</td>"
        f"<td>{esc(row['last_error'])}</td></tr>"
        for row in (snapshot.get("recent_activity") or [])
    )
    body = f"""
<section class="card"><h2>공식자료 수집 상태</h2>
<p class="muted">실제 정규화 저장건수와 collection checkpoint를 기준으로 표시합니다. 이 화면 자체는 외부 API를 호출하거나 수집 범위를 변경하지 않습니다.</p>
<div class="notice"><b>자동 확인:</b> 5초마다 새로고침합니다. RUNNING이 5분 이상 갱신되지 않으면 <b>갱신중단</b>으로 표시하여 멈춘 작업을 정상 실행처럼 보이지 않게 합니다.</div>
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
'<div class="notice ok"><b>API별 수동 수집:</b> 나라장터와 지방재정365는 서로 다른 API·키·호출한도를 사용합니다. 각 버튼은 해당 원천만 실행합니다.</div>'
'<div class="grid">'
+ f'<div class="kpi"><b>{esc(shopping_run_label)}</b><span>나라장터 실행상태</span><small>{esc(runtime_sources.get("shopping_last_error") or "")}</small></div>'
+ f'<div class="kpi"><b>{esc(budget_run_label)}</b><span>지방재정365 실행상태</span><small>{esc(runtime_sources.get("budget_last_error") or "")}</small></div>'
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
'</div>'
)}
</section>
<section class="card"><h3>수집 단계별 현황</h3><div class="stage-grid">{stages}</div></section>
<section class="card"><h3>최근 실행 내역</h3>
<div class="table"><table><tr><th>갱신시각</th><th>자료</th><th>수집범위</th><th>상태</th><th>페이지</th><th>저장</th><th>오류</th></tr>
{recent_rows or '<tr><td colspan="7">아직 collection checkpoint 실행 내역이 없습니다.</td></tr>'}
</table></div></section>
<section class="card"><div class="notice"><b>수집 안전경계:</b> 예산 정규화 자료 + 2026-09-01 이후 조명·등주 사업자료만 운영수집합니다. 용역·입찰 수집은 제거했고, bulk historical·APPROVED_HISTORICAL·교육청 live transport는 HOLD입니다.</div></section>
"""
    return layout("수집 상태", body, "수집 상태", user, refresh_seconds=5)


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
    try:
        limit = max(10, min(int(request.query_params.get("limit", 200)), 1000))
    except (TypeError, ValueError):
        limit = 200

    if is_result_server() and result_snapshot_vnext.snapshot_available():
        source_rows = result_snapshot_vnext.query_rows(
            "shopping", categories=(category,), query=q, limit=5000
        )
        if region:
            source_rows = [
                row for row in source_rows
                if str(row.get("demand_region") or "") == region
            ]
        rows = source_rows[:limit]
    else:
        rows = read.shopping_rows(
            categories=(category,), query=q, region=region, limit=limit
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
<p class="muted">2026-09-01 이후 전국 나라장터 납품요구를 확인하되 DB에는 조명·등주 세부품명만 저장합니다. 기본 조회지역은 인천광역시입니다.</p>
<form class="row" method="get">
<label>지역<select name="region">{''.join(region_options)}</select></label>
<label>품목<select name="category">{''.join(category_options)}</select></label>
<label>검색<input name="q" value="{esc(q)}" placeholder="기관·제품·업체·식별번호·모델"></label>
<label>표시<input name="limit" type="number" min="10" max="1000" value="{limit}"></label>
<button class="primary">조회</button></form></section>
<div class="grid">
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

    # RESULT_SERVER is retained only for rollback compatibility. V4 Cafe24 defaults
    # to UNIFIED, where region-specific vendor aggregation is computed directly.
    if is_result_server() and result_snapshot_vnext.snapshot_available() and not region:
        rows = result_snapshot_vnext.query_rows("vendors", query=q, limit=limit)
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
<p class="muted">용역 계약은 제외하고 2026-09-01 이후 조명·등주 납품실적만 업체별로 집계합니다.</p>
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
    categories = (category,) if category in TARGET_CATEGORIES else None
    if "region" in request.query_params:
        region = str(request.query_params.get("region", "") or "").strip()
    else:
        region = "인천광역시"
    if region and region not in budget_read_vnext.REGIONS:
        region = "인천광역시"

    current_rows = []
    targets = []
    prebid = []
    future_rows = []
    error = ""
    storage = {}
    dataset_counts = {}
    try:
        storage = budget_storage.status()
        dataset_counts = budget_storage.dataset_counts_all()
        if budget_storage.using_postgres() and not storage.get("configured"):
            error = "예산 PostgreSQL 연결이 아직 설정되지 않았습니다."
        elif is_result_server() and result_snapshot_vnext.snapshot_available():
            targets = result_snapshot_vnext.query_rows(
                "budget_targets", categories=categories, fiscal_year=year, limit=300
            )
            prebid = result_snapshot_vnext.query_rows(
                "budget_prebid", categories=categories, fiscal_year=year, limit=300
            )
            # Legacy RESULT_SERVER snapshots may not contain an all-current budget
            # section. Keep the current-row table empty rather than relabeling
            # target rows as source data.
            current_rows = []
            if region:
                targets = [
                    row for row in targets
                    if budget_read_vnext.region_matches(row, region)
                ]
                prebid = [
                    row for row in prebid
                    if budget_read_vnext.region_matches(row, region)
                ]
        else:
            payload = budget_read_vnext.budget_read_model(
                fiscal_year=year,
                categories=categories,
                region=region,
                limit=300,
            )
            current_rows = payload.get("current_rows") or []
            targets = payload.get("target_rows") or []
            prebid = payload.get("prebid_rows") or []
            future_rows = budget_read_vnext.future_appropriation_rows(
                fiscal_year=_dt.date.today().year + 1,
                categories=categories,
                region=region,
                limit=200,
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
    source_layer_labels = {
        "DETAIL_EXECUTION": "지방재정365 세부사업·집행",
        "APPROPRIATION": "지방재정365 세출예산(AIDFA)",
        "EDUCATION": "교육청 예산",
    }
    current_budget_rows_html = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{esc(budget_read_vnext.row_region(r))}<br><span class='muted'>{esc(r.get('org_name') or r.get('institution_name'))}</span></td>"
        f"<td>{esc(source_layer_labels.get(str(r.get('source_layer') or ''), r.get('source_layer')))}</td>"
        f"<td><b>{esc(r.get('project_name') or r.get('field_name') or r.get('section_name'))}</b></td>"
        f"<td>{esc(CATEGORY_LABELS.get(r.get('primary_category'),r.get('primary_category') or '미분류'))}</td>"
        f"<td class='num'>{money(r.get('budget_amount') or r.get('appropriation_amount'))}</td>"
        f"<td class='num'>{money(r.get('executed_amount'))}</td>"
        f"<td class='num'>{money(r.get('remaining_amount'))}</td></tr>"
        for r in current_rows
    )

    target_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{esc(budget_read_vnext.row_region(r))}<br><span class='muted'>{esc(r.get('org_name') or r.get('institution_name'))}</span></td>"
        f"<td><b>{esc(r.get('project_name'))}</b></td>"
        f"<td>{esc(CATEGORY_LABELS.get(r.get('primary_category'),r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('budget_amount'))}</td>"
        f"<td class='num'>{money(r.get('executed_amount'))}</td>"
        f"<td class='num'>{money(r.get('remaining_amount'))}</td></tr>"
        for r in targets
    )
    prebid_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{esc(budget_read_vnext.row_region(r))}<br><span class='muted'>{esc(r.get('org_name') or r.get('institution_name'))}</span></td>"
        f"<td><b>{esc(r.get('project_name'))}</b></td>"
        f"<td>{esc(CATEGORY_LABELS.get(r.get('primary_category'),r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('remaining_amount'))}</td></tr>"
        for r in prebid
    )
    future_budget_rows = "".join(
        f"<tr><td>{esc(r.get('fiscal_year'))}</td>"
        f"<td>{esc(budget_read_vnext.row_region(r))}<br><span class='muted'>{esc(r.get('org_name') or r.get('region_name'))}</span></td>"
        f"<td><b>{esc(r.get('project_name'))}</b></td>"
        f"<td>{esc(CATEGORY_LABELS.get(r.get('primary_category'),r.get('primary_category')))}</td>"
        f"<td class='num'>{money(r.get('budget_amount') or r.get('appropriation_amount'))}</td></tr>"
        for r in future_rows
    )
    backend = str(storage.get("backend") or budget_storage.backend_name())
    current_records = int(storage.get("current_records") or 0)
    observations = int(storage.get("observations") or 0)
    qwg_current = int((dataset_counts.get("budget") or {}).get("current_records") or 0)
    aidfa_current = int((dataset_counts.get("budget_appropriation") or {}).get("current_records") or 0)
    education_current = int((dataset_counts.get("education_budget") or {}).get("current_records") or 0)
    notice = (
        f'<div class="notice bad">{esc(error)}</div>' if error else
        '<div class="notice ok"><b>예산 중심 운영:</b> 원문 JSON은 저장하지 않고 기관·사업·예산·집행 등 필요한 필드와 변경 hash만 PostgreSQL에 보존합니다.</div>'
    )
    body = f"""
<section class="card"><h2>예산 · 영업후보</h2>
{notice}
<form class="row" method="get">
<label>연도<input name="year" value="{year}" inputmode="numeric"></label>
<label>지역<select name="region">{''.join(region_options)}</select></label>
<label>분류<select name="category">{''.join(opts)}</select></label>
<button class="primary">조회</button></form>
<p class="muted">전국 또는 17개 시·도별로 지방재정365 예산을 조회합니다. 교육청 예산도 동일 지역 규칙을 사용하며 live 수집은 검증 완료 전까지 HOLD입니다.</p></section>
<div class="grid">
<div class="kpi"><b>{len(current_rows):,}</b><span>현재 조건 조회자료</span></div>
<div class="kpi"><b>{len(targets):,}</b><span>대상 예산사업</span></div>
<div class="kpi"><b>{len(prebid):,}</b><span>영업후보</span></div>
<div class="kpi"><b>{len(future_rows):,}</b><span>미래 편성예산 신호</span></div>
<div class="kpi"><b>{qwg_current:,}</b><span>QWGJK 현재자료</span></div>
<div class="kpi"><b>{aidfa_current:,}</b><span>AIDFA 현재자료</span></div>
<div class="kpi"><b>{education_current:,}</b><span>교육청 현재자료</span></div>
<div class="kpi"><b>{current_records:,}</b><span>전체 현재 저장자료</span></div>
<div class="kpi"><b>{observations:,}</b><span>1년 변경이력</span></div>
<div class="kpi"><b>{esc(backend)}</b><span>예산 저장소</span></div>
</div>
<section class="card"><h3>수집된 현재 예산자료</h3>
<p class="muted">PostgreSQL current state에 저장된 자료를 그대로 표시합니다. AIDFA 구조예산은 영업후보가 아니어도 여기에는 보이며, QWGJK 세부사업·집행과 향후 교육청 예산도 같은 표에서 구분합니다. 이 표는 외부 API를 호출하지 않습니다.</p>
<div class="table"><table>
<tr><th>연도</th><th>지역 / 기관</th><th>자료구분</th><th>사업 / 예산구조</th><th>분류</th><th>예산</th><th>집행</th><th>잔액</th></tr>
{current_budget_rows_html or '<tr><td colspan="8">현재 조건의 저장자료 없음</td></tr>'}
</table></div></section>
<section class="card"><h3>{_dt.date.today().year + 1} 미래 편성예산 신호</h3>
<p class="muted">지방재정365 AIDFA의 구조별·기능별 세출예산 중 조명·등주 등 목표분류에 해당한 항목입니다. 세부사업 확정 전 구조적 예산 신호이므로 직접 영업후보와 분리해 표시합니다.</p>
<div class="table"><table><tr><th>연도</th><th>지역 / 기관</th><th>예산구조</th><th>분류</th><th>편성예산</th></tr>
{future_budget_rows or '<tr><td colspan="5">현재 확인된 미래 목표 예산 없음</td></tr>'}</table></div></section>
<section class="card"><h3>우선 영업후보</h3>
<p class="muted">예산은 확인됐지만 G2B가 입찰·용역을 중복 수집해 진행단계를 추정하지 않습니다. NO1과 역할을 분리합니다.</p>
<div class="table"><table><tr><th>연도</th><th>지역 / 기관</th><th>사업명</th><th>분류</th><th>잔액</th></tr>
{prebid_rows or '<tr><td colspan="5">현재 조건의 후보 없음</td></tr>'}</table></div></section>
<section class="card"><h3>대상 예산사업</h3><div class="table"><table>
<tr><th>연도</th><th>지역 / 기관</th><th>사업명</th><th>분류</th><th>예산</th><th>집행</th><th>잔액</th></tr>
{target_rows or '<tr><td colspan="7">현재 조건의 자료 없음</td></tr>'}</table></div></section>
"""
    return layout("예산·영업후보", body, "예산·영업후보", user)


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
<div class="notice"><b>4.1 수집범위:</b> 예산은 정규화 필드만 PostgreSQL에 저장하고, 사업자료는 2026-09-01 이후 전국 조명·등주만 저장합니다. 용역·입찰 수집은 NO1로 분리했습니다.</div>
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
<section class="card"><h3>저장정책</h3><div class="notice">원문 JSON 비저장 · 과거 예산 변경이력 1년 · 미래예산 보호 · 사업자료 2026-09-01 이후</div></section>
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
    if not result_snapshot_vnext.snapshot_available():
        return RedirectResponse("/settings?error=" + quote("먼저 로컬 결과 스냅샷을 동기화해 주세요."), 303)
    import result_server_maintenance
    result = result_server_maintenance.compact_result_server_source_data()
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
        payload = _decode_result_sync_body(
            await request.body(),
            request.headers.get("Content-Encoding", ""),
        )
        manifest = result_snapshot_vnext.import_snapshot(payload)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:120]}, 400)
    except Exception as exc:
        print("G2B_RESULT_SYNC_FAILED", type(exc).__name__, flush=True)
        return JSONResponse({"ok": False, "error": "RESULT_SYNC_FAILED"}, 500)
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
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        return result_snapshot_vnext.query_rows(
            "shopping", categories=categories, query=q, limit=limit
        )
    import procurement_read_vnext
    return procurement_read_vnext.shopping_rows(categories=categories, query=q, limit=limit)


@app.get("/api/vendors")
def api_vendors(request: Request):
    if not require_user(request):
        return JSONResponse({"ok": False, "error": "AUTH_REQUIRED"}, 401)
    q = str(request.query_params.get("q", "") or "")
    if is_result_server() and result_snapshot_vnext.snapshot_available():
        return result_snapshot_vnext.query_rows("vendors", query=q, limit=1000)
    import procurement_read_vnext
    return procurement_read_vnext.vendor_rows(query=q, limit=1000)


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
        targets = result_snapshot_vnext.query_rows(
            "budget_targets", fiscal_year=year, limit=500
        )
        prebid = result_snapshot_vnext.query_rows(
            "budget_prebid", fiscal_year=year, limit=500
        )
        if region:
            targets = [
                row for row in targets
                if budget_read_vnext.region_matches(row, region)
            ]
            prebid = [
                row for row in prebid
                if budget_read_vnext.region_matches(row, region)
            ]
        return {
            "target_rows": targets,
            "prebid_rows": prebid,
            "selected_region": region,
            "source": "LOCAL_RESULT_SNAPSHOT",
            "no1_boundary": "입찰·용역·낙찰·계약은 NO1 담당",
        }
    import datetime as _dt
    payload = budget_read_vnext.budget_read_model(
        fiscal_year=year,
        region=region,
        limit=500,
    )
    future_year = _dt.date.today().year + 1
    payload["future_fiscal_year"] = future_year
    payload["future_appropriation_rows"] = (
        budget_read_vnext.future_appropriation_rows(
            fiscal_year=future_year,
            region=region,
            limit=500,
        )
    )
    payload["selected_region"] = region
    return payload
