"""4.1.256: restart/rolling-deploy worker singleton admission and boot grace."""

import fcntl
import threading
import time

import pytest

import g2b_heavy_worker as worker
import safe_boot_vnext
import vnext_clean_app as app


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_second_source_worker_rejects_host_lock_without_waiting_60s(
    monkeypatch, tmp_path, mode, capsys
):
    path = tmp_path / "g2b-heavy-restart-singleton.lock"
    monkeypatch.setattr(worker, "LOCK_PATH", path)
    monkeypatch.setattr(worker, "_prepare_environment", lambda: None)
    monkeypatch.setattr(worker, "_deprioritize", lambda: None)
    monkeypatch.setattr(worker, "_start_parent_watchdog", lambda: None)
    monkeypatch.setattr(worker, "_persist_source_failure_detail", lambda *args: (
        _ for _ in ()
    ).throw(AssertionError("lease contention is not a source failure")))

    with path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        result = worker.main([mode])
        elapsed = time.monotonic() - started
        assert result == 77
        assert elapsed < 2, "a duplicate child must not wait for a minute"
        assert "G2B_HEAVY_WORKER_SLOT_LEASE_HELD" in capsys.readouterr().out
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def test_restart_boot_grace_preserves_full_five_minutes_at_256mib():
    assert safe_boot_vnext.boot_grace_remaining(
        100, low_memory=True, test_mode=False, now=100
    ) == 300
    assert safe_boot_vnext.boot_grace_remaining(
        100, low_memory=True, test_mode=False, now=399
    ) == 1
    assert safe_boot_vnext.boot_grace_remaining(
        100, low_memory=True, test_mode=False, now=400
    ) == 0
    assert safe_boot_vnext.boot_grace_remaining(
        100, low_memory=False, test_mode=False, now=100
    ) == 0


def test_auto_worker_never_launches_source_during_boot_grace(monkeypatch):
    # The real waiting loop cannot be sped up by an old wake signal; its
    # monotonic full-grace check is repeated on each wake.
    checks = iter([300, 240, 1, 0])
    scheduled = []
    waits = []
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: next(checks))
    monkeypatch.setattr(app, "_web_source_isolation_required", lambda: True)
    monkeypatch.setattr(
        app, "_run_low_memory_automatic_cycle",
        lambda: scheduled.append("source_cycle") or {
            "operational_cycle_lease": "ISOLATED_AUTOMATIC"
        },
    )
    monkeypatch.setattr(app, "_automatic_cycle_wait_seconds", lambda *_: 7200)

    class StopScheduledCycle(BaseException):
        pass

    class Wake:
        def clear(self):
            pass

        def wait(self, duration):
            waits.append(duration)
            if duration == 7200:
                raise StopScheduledCycle()
            assert scheduled == [], "the previous run's wake bypassed 5min grace"

    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", Wake())
    monkeypatch.setattr(app, "_RECENT_COLLECTION_THREAD", threading.current_thread())
    with pytest.raises(StopScheduledCycle):
        app._recent_collection_worker()
    assert waits == [15, 15, 1, 7200]
    assert scheduled == ["source_cycle"]
    assert app._RECENT_COLLECTION_THREAD is None


def test_duplicate_source_exit_remains_retryable_lease_not_failure(monkeypatch):
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 0, "budget": 0
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "shopping_run_state": "RUNNING", "budget_run_state": "COMPLETE",
        "shopping_last_error": "", "budget_last_error": "",
    })
    app._isolated_worker_exit_state("shopping", 77)
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "LEASE_HELD"
    assert app._RECENT_COLLECTION_STATE["shopping_last_error"] == "LEASE_HELD"
    assert app._automatic_cycle_wait_seconds({
        "operational_cycle_lease": "ISOLATED_AUTOMATIC"
    }) == app.OPERATIONAL_LEASE_RETRY_SECONDS


def test_worker_restart_never_enables_worker_auto_sync_or_db_reset():
    import inspect
    assert 'os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"' in inspect.getsource(
        worker._prepare_environment
    )
    assert 'os.environ["G2B_AUTO_SYNC_DISABLE"] = "1"' in inspect.getsource(
        worker._prepare_environment
    )
    assert 'os.environ["G2B_V41_FRESH_START"] = "0"' in inspect.getsource(
        worker._prepare_environment
    )
    from pathlib import Path
    source = Path("g2b_heavy_worker.py").read_text(encoding="utf-8")
    assert "_worker_slot(timeout=slot_wait)" in source
    assert 'WorkerSlotHeld("G2B_HEAVY_WORKER_SLOT_TIMEOUT")' in source
