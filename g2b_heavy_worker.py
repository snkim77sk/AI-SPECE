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
                    raise TimeoutError("G2B_HEAVY_WORKER_SLOT_TIMEOUT") from None
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


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    mode = str(args[0] if args else "").strip().lower()
    if mode not in ALLOWED_MODES:
        return 2

    _prepare_environment()

    import memory_guard

    memory_guard.apply_default_process_tuning()
    _deprioritize()
    _start_parent_watchdog()

    stage = "worker_slot"
    try:
        with _worker_slot():
            stage = "memory_admission"
            state = memory_guard.snapshot(collect=True)
            if int(state.get("cgroup_oom_group") or 0) == 1:
                print("G2B_HEAVY_WORKER_HOLD OOM_GROUP", flush=True)
                return 76
            memory_guard.wait_for_heavy_work_budget(timeout=30.0)

            stage = "import_app"
            import vnext_clean_app as app

            stage = "backend_init"
            if not app.initialize_backend(force=True):
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
                if source_state == "FAILED" and source_error:
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

            app._match_backfill_worker(mode == "match-legacy")
            print("G2B_HEAVY_WORKER_OK", mode, flush=True)
            return 0
    except memory_guard.MemoryPressureError as exc:
        print("G2B_HEAVY_WORKER_MEMORY_HOLD", mode, str(exc), flush=True)
        return 75
    except Exception as exc:
        _persist_source_failure_detail(
            mode,
            f"WORKER:{stage}:{type(exc).__name__}",
        )
        print(
            "G2B_HEAVY_WORKER_ERROR",
            mode,
            stage,
            type(exc).__name__,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
