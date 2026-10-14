"""Isolated heavy worker for G2B low-memory Cafe24 deployments.

SINSUNG proved the useful pattern: keep expensive work outside the long-lived web
process, serialize workers, bias the kernel to sacrifice the worker before the web
process, and preserve resumable DB checkpoints. This module never deletes or
initializes business data.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path(tempfile.gettempdir()) / "g2b_heavy_background_worker.lock"
LOCK_WAIT_SECONDS = 60.0
ALLOWED_MODES = {"shopping", "budget", "match", "match-legacy"}


class WorkerSlotHeld(TimeoutError):
    """A prior process currently owns the local heavy-worker singleton."""
SOURCE_STATE_EXIT_CODES = {
    "COMPLETE": 0,
    "WAITING_KEYS": 72,
    "WAITING_QUOTA": 73,
    "WAITING_STORAGE": 74,
    "WAITING_PERSISTENT_STORAGE": 74,
    "WAITING_MEMORY": 75,
    "LEASE_HELD": 77,
    "PARTIAL": 78,
    "FAILED": 1,
}

# Match worker must report its actual result, rather than treating every
# returned (or swallowed) state as a successful exit. Unknown and None fail
# closed so the parent never infers success from absent evidence.
MATCH_STATE_EXIT_CODES = {
    "COMPLETE": 0,
    "PARTIAL": 78,
    "WAITING_QUOTA": 73,
    "WAITING_STORAGE": 74,
    "WAITING_MEMORY": 75,
    "LEASE_HELD": 77,
    "FAILED": 1,
}


def _deprioritize():
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass
    try:
        # Positive values are permitted to an unprivileged process on Linux and
        # make this disposable worker a more likely OOM victim than the web app.
        Path("/proc/self/oom_score_adj").write_text("900", encoding="ascii")
    except OSError:
        pass


def _start_parent_watchdog():
    expected_parent = os.getppid()
    if expected_parent <= 1:
        return

    def watch():
        while True:
            time.sleep(2.0)
            if os.getppid() != expected_parent:
                os._exit(75)

    threading.Thread(
        target=watch,
        name="g2b-heavy-worker-parent-watchdog",
        daemon=True,
    ).start()


@contextmanager
def _worker_slot(timeout=LOCK_WAIT_SECONDS):
    try:
        import fcntl
    except ImportError:
        yield
        return

    handle = LOCK_PATH.open("a+b")
    acquired = False
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WorkerSlotHeld("G2B_HEAVY_WORKER_SLOT_TIMEOUT") from None
                time.sleep(0.1)
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _prepare_environment():
    # Worker reads/writes the same PostgreSQL database but must never start its
    # own recurring scheduler or fresh-start path.
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_AUTO_SYNC"] = "0"
    os.environ["G2B_AUTO_SYNC_DISABLE"] = "1"
    os.environ["G2B_POST_BOOT_MAINTENANCE_ENABLE"] = "0"
    os.environ["G2B_MATCH_ROLLOVER_AUTO_ENABLE"] = "0"
    os.environ["G2B_V41_FRESH_START"] = "0"
    os.environ["G2B_ISOLATED_HEAVY_WORKER"] = "1"
    # Always clear a possibly inherited setting. The actual low-tier decision
    # happens after reading the authoritative cgroup, not an operator flag.
    os.environ["G2B_SOURCE_ONLY_RUNTIME"] = "0"

    # Keep the disposable child well below the 256 MiB container ceiling. The
    # cgroup-wide guard remains authoritative and also counts the web process.
    requested = str(os.environ.get("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB", "112") or "112")
    try:
        requested_int = max(96, min(160, int(requested)))
    except (TypeError, ValueError):
        requested_int = 112
    os.environ["G2B_MEMORY_SOFT_LIMIT_MB"] = str(requested_int)


def _persist_source_failure_detail(mode, detail):
    """Persist a compact child failure reason for the parent/web process."""
    source = str(mode or "").strip().lower()
    text = str(detail or "").strip()[:180]
    key = {
        "shopping": "shopping_recent_last_error",
        "budget": "budget_recent_last_error",
    }.get(source)
    if not key or not text:
        return False
    try:
        from db import set_setting

        set_setting(key, text)
        return True
    except Exception as exc:
        print(
            "G2B_HEAVY_WORKER_ERROR_PERSIST_DEGRADED",
            source,
            type(exc).__name__,
            flush=True,
        )
        return False


def _source_only_candidate(mode, memory_state):
    """Only small, measurable cgroups use the isolated source-only import."""
    try:
        limit = float((memory_state or {}).get("cgroup_limit_mib") or 0)
    except (TypeError, ValueError):
        return False
    return mode in {"shopping", "budget"} and 0 < limit <= 320


def _profile_source_imports(mode):
    """Source-free, DB-free memory baseline for CI (not a collector run)."""
    if mode not in {"shopping", "budget"}:
        return 2
    import json
    _prepare_environment()
    os.environ["G2B_SOURCE_ONLY_RUNTIME"] = "1"
    import memory_guard
    baseline = memory_guard.current_rss_mib()
    import vnext_clean_app as app
    app_rss = memory_guard.current_rss_mib()
    if mode == "shopping":
        import shopping_recent_vnext  # noqa: F401
    else:
        import budget_storage  # noqa: F401
        import budget_vnext  # noqa: F401
        import budget_appropriation_vnext  # noqa: F401
        import classification_vnext  # noqa: F401
        import lofin_vnext_http  # noqa: F401
    source_rss = memory_guard.current_rss_mib()
    # Linux ru_maxrss is KiB; provide a genuine process high-water mark,
    # not a second instantaneous RSS mislabeled as a peak.
    try:
        import resource
        peak_rss = round(
            float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0,
            2,
        )
    except (ImportError, OSError, ValueError):
        peak_rss = source_rss
    report = {
        "mode": mode,
        "source_only": bool(app._SOURCE_ONLY_RUNTIME),
        "fastapi_loaded": "fastapi" in sys.modules,
        "rss_before_mib": baseline,
        "rss_app_import_mib": app_rss,
        "rss_source_import_mib": source_rss,
        "rss_peak_mib": round(max(float(source_rss), float(peak_rss)), 2),
        "database_or_api_called": False,
    }
    print("G2B_LIGHT_SOURCE_IMPORT_PROFILE", json.dumps(report, sort_keys=True),
          flush=True)
    return 0 if report["source_only"] and not report["fastapi_loaded"] else 1


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    mode = str(args[0] if args else "").strip().lower()
    if mode == "--profile-imports":
        return _profile_source_imports(
            str(args[1] if len(args) > 1 else "").strip().lower()
        )
    if mode not in ALLOWED_MODES:
        return 2

    _prepare_environment()

    import memory_guard

    memory_guard.apply_default_process_tuning()
    _deprioritize()
    _start_parent_watchdog()

    stage = "worker_slot"
    try:
        # On restart/rolling deployment, an old child may still own this
        # host-local slot. A second source child must NOT consume up to 60s of
        # cgroup memory waiting behind it. Cross-host duplication remains
        # prevented by the PostgreSQL source advisory lease.
        slot_wait = 0.0 if mode in {"shopping", "budget"} else LOCK_WAIT_SECONDS
        with _worker_slot(timeout=slot_wait):
            stage = "memory_admission"
            state = memory_guard.snapshot(collect=True)
            if int(state.get("cgroup_oom_group") or 0) == 1:
                print("G2B_HEAVY_WORKER_HOLD OOM_GROUP", flush=True)
                return 76
            memory_guard.wait_for_heavy_work_budget(timeout=30.0)

            stage = "import_app"
            os.environ["G2B_SOURCE_ONLY_RUNTIME"] = (
                "1" if _source_only_candidate(mode, state) else "0"
            )
            import vnext_clean_app as app

            stage = "backend_init"
            backend_ready = (
                app.initialize_isolated_source_backend_read_only(mode)
                if app._SOURCE_ONLY_RUNTIME
                else app.initialize_backend(force=True)
            )
            if not backend_ready:
                print("G2B_HEAVY_WORKER_BACKEND_NOT_READY", mode, flush=True)
                return 70

            # initialize_backend() honors the worker's AUTO_SYNC_DISABLE gate.
            stage = "memory_before_source"
            memory_guard.wait_for_heavy_work_budget(timeout=30.0)
            if mode in {"shopping", "budget"}:
                stage = "source_run"
                app._run_recent_collection_once(source=mode)
                stage = "source_state"
                source_status = app.recent_collection_status()
                source_state = str(
                    source_status.get(f"{mode}_run_state") or "COMPLETE"
                ).upper()
                source_error = str(
                    source_status.get(f"{mode}_last_error") or ""
                ).strip()
                if source_state in {"FAILED", "WAITING_MEMORY"} and source_error:
                    _persist_source_failure_detail(mode, source_error)
                exit_code = int(
                    SOURCE_STATE_EXIT_CODES.get(source_state, 78)
                )
                print(
                    "G2B_HEAVY_WORKER_SOURCE_STATE",
                    mode,
                    source_state,
                    exit_code,
                    flush=True,
                )
                return exit_code

            stage = "match_run"
            match_outcome = app._match_backfill_worker(mode == "match-legacy")
            match_state = str(match_outcome or "FAILED").strip().upper()
            code = int(MATCH_STATE_EXIT_CODES.get(match_state, 1))
            print(
                "G2B_HEAVY_WORKER_MATCH_STATE",
                mode,
                match_state if match_state in MATCH_STATE_EXIT_CODES else "UNKNOWN",
                code,
                flush=True,
            )
            return code
    except WorkerSlotHeld:
        # This is a competing process, not a failed API source or corrupt
        # checkpoint. Do not initialize DB, call source APIs, or persist an
        # old error. The parent maps code 77 to LEASE_HELD and retries safely.
        if mode in {"shopping", "budget"}:
            print("G2B_HEAVY_WORKER_SLOT_LEASE_HELD", mode, flush=True)
            return 77
        print("G2B_HEAVY_WORKER_SLOT_BUSY", mode, flush=True)
        return 1
    except memory_guard.MemoryPressureError as exc:
        detail = "MEMORY_PRESSURE:" + " ".join(str(exc or "").split())[:140]
        _persist_source_failure_detail(mode, detail)
        print("G2B_HEAVY_WORKER_MEMORY_HOLD", mode, str(exc), flush=True)
        return 75
    except Exception as exc:
        code = " ".join(str(exc or "").split())[:120]
        detail = f"WORKER:{stage}:{type(exc).__name__}"
        if code:
            detail = f"{detail}:{code}"
        _persist_source_failure_detail(mode, detail)
        print(
            "G2B_HEAVY_WORKER_ERROR",
            mode,
            stage,
            type(exc).__name__,
            code,
            flush=True,
        )
        return 1
    finally:
        # No source API, DSN, receipt, or secret is included in this numeric
        # readout. Process VmHWM is a real high-water mark; cgroup figures are
        # only the maximum observed at existing memory checkpoints.
        if mode in {"shopping", "budget"}:
            try:
                import json
                print(
                    "G2B_SOURCE_WORKER_MEMORY", mode, stage,
                    json.dumps(
                        memory_guard.isolated_worker_memory_profile(),
                        sort_keys=True, separators=(",", ":"),
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    "G2B_SOURCE_WORKER_MEMORY_PROBE_DEGRADED",
                    mode, type(exc).__name__, flush=True,
                )


if __name__ == "__main__":
    raise SystemExit(main())
