"""4.1.275: recover the existing isolated Popen after its supervisor dies.

Deterministic in-process fake Popen objects, no source HTTP, PostgreSQL,
credential reads, checkpoint writes, schema changes or real worker spawn.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import vnext_clean_app as app


class Child:
    pid = 427500

    def __init__(self, exit_code=None):
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code

    def wait(self, timeout=None):
        assert self.exit_code is not None, "an active fake must use a blocked wait fixture"
        return self.exit_code


def prepare(monkeypatch, *, exit_code=None, pending=(), supervisor=None):
    child = Child(exit_code)
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "can_collect_sources", lambda: True)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", list(pending))
    monkeypatch.setattr(
        app, "_ISOLATED_HEAVY_SUPERVISOR",
        threading.Thread(name="terminated-reaper")
        if supervisor is None else supervisor,
    )
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR_RECOVERY_RETRY_AT", 0.0)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_LAST", {
        "kind": "", "exit_code": None,
    })
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 0, "budget": 0,
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_TERMINAL_REVISION", 0)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", threading.Event())
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "state": "RUNNING", "last_status": "RUNNING", "last_error": "",
        "shopping_run_state": "RUNNING", "shopping_last_error": "",
        "budget_run_state": "QUEUED" if pending else "IDLE",
        "budget_last_error": "",
    })
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("reaper may not query PostgreSQL")
        ),
    )
    return child


def test_exited_child_is_reaped_once_and_queued_source_is_safely_held(monkeypatch):
    child = prepare(monkeypatch, exit_code=0, pending=("budget",))
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda mode: False)
    release = threading.Event()
    entered = threading.Event()

    def wait_then_finish(proc, mode):
        assert proc is child and mode == "shopping"
        entered.set()
        assert release.wait(timeout=3)
        return 0, False

    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", wait_then_finish)
    app._isolated_heavy_worker_status()  # attaches a real lightweight supervisor
    supervisor = app._ISOLATED_HEAVY_SUPERVISOR
    assert supervisor is not None and entered.wait(timeout=2)
    release.set()
    supervisor.join(timeout=3)
    assert not supervisor.is_alive()

    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._ISOLATED_HEAVY_LAST["kind"] == "shopping"
    assert app._ISOLATED_HEAVY_LAST["exit_code"] == 0
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "WAITING_MEMORY"
    assert app._PARENT_MEMORY_ABORT_STREAK["budget"] == 1
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 2
    assert child.exit_code == 0
    assert app._isolated_heavy_worker_status()["reap_pending"] is False


def test_active_child_survives_reaper_handoff_then_terminal_is_published(monkeypatch):
    child = prepare(monkeypatch, exit_code=None)
    release = threading.Event()
    entered = threading.Event()

    def blocked_wait(proc, mode):
        assert proc is child and mode == "shopping"
        entered.set()
        assert release.wait(timeout=3)
        child.exit_code = 0
        return 0, False

    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", blocked_wait)
    status = app._isolated_heavy_worker_status()
    supervisor = app._ISOLATED_HEAVY_SUPERVISOR
    assert entered.wait(timeout=2)
    assert supervisor is not None and supervisor.is_alive()
    assert status["pid"] == child.pid
    assert app._ISOLATED_HEAVY_PROCESS is child
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "RUNNING"

    release.set()
    supervisor.join(timeout=3)
    assert not supervisor.is_alive()
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._ISOLATED_HEAVY_LAST["exit_code"] == 0


def test_concurrent_status_requests_attach_only_one_reaper(monkeypatch):
    child = prepare(monkeypatch, exit_code=None)
    release = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(
        app, "_wait_for_isolated_heavy_child",
        lambda proc, mode: (
            entered.set(),
            release.wait(timeout=3),
            (0, False),
        )[-1],
    )
    original_start = app._ensure_isolated_heavy_supervisor_locked
    attempts = []

    def start():
        attempts.append("attach")
        return original_start()

    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", start)
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            status = list(pool.map(lambda _: app._isolated_heavy_worker_status(), range(10)))
        assert entered.wait(timeout=2)
        assert attempts == ["attach"]
        assert all(s["pid"] == child.pid for s in status)
        assert all(s["supervisor_running"] for s in status)
        assert app._ISOLATED_HEAVY_PROCESS is child
    finally:
        supervisor = app._ISOLATED_HEAVY_SUPERVISOR
        release.set()
        if supervisor is not None:
            supervisor.join(timeout=3)
    assert app._ISOLATED_HEAVY_PROCESS is None


@pytest.mark.parametrize("error", [
    OSError(12, "ENOMEM detail"), RuntimeError("cannot start"),
    MemoryError("cannot allocate"),
])
def test_reaper_thread_start_failure_is_throttled_without_losing_child(
    monkeypatch, capsys, error,
):
    child = prepare(monkeypatch, exit_code=0, pending=("budget",))
    started = []

    class RefuseStart:
        def __init__(self, **kwargs):
            started.append("constructed")

        def is_alive(self):
            return False

        def start(self):
            raise error

    monkeypatch.setattr(app.threading, "Thread", RefuseStart)
    first = app._isolated_heavy_worker_status()
    second = app._isolated_heavy_worker_status()

    assert len(started) == 1
    assert first["running"] is False
    assert first["reap_pending"] is True
    assert second["reap_pending"] is True
    assert first["pending"] == ["budget"]
    assert app._ISOLATED_HEAVY_PROCESS is child
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "RUNNING"
    assert "G2B_ISOLATED_SUPERVISOR_START_HOLD" in capsys.readouterr().out

    # Simulate expiration of the five-second start failure throttle.
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR_RECOVERY_RETRY_AT", 0.0)
    third = app._isolated_heavy_worker_status()
    assert len(started) == 2
    assert third["reap_pending"] is True
    assert app._ISOLATED_HEAVY_PROCESS is child


def test_manual_other_source_is_queued_not_spawned_with_unreaped_child(monkeypatch):
    child = prepare(monkeypatch, exit_code=0)
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: False)
    monkeypatch.setattr(
        app, "_isolated_worker_admission_ok",
        lambda _: (_ for _ in ()).throw(
            AssertionError("existing unreaped process must not trigger admission")
        ),
    )
    monkeypatch.setattr(
        app, "_launch_isolated_heavy_worker_locked",
        lambda _: (_ for _ in ()).throw(
            AssertionError("cannot spawn a second child behind unreaped first")
        ),
    )
    assert app.schedule_manual_collection("budget") is True
    assert app._ISOLATED_HEAVY_PROCESS is child
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "QUEUED"


def test_auto_batch_waits_for_reap_pending_even_after_child_poll_exit(monkeypatch):
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(app, "schedule_manual_collection", lambda _src: False)
    statuses = iter([
        {"running": False, "pending": [], "supervisor_running": False, "reap_pending": True},
        {"running": False, "pending": [], "supervisor_running": False, "reap_pending": False},
    ])
    sleeps = []
    monkeypatch.setattr(app, "_isolated_heavy_worker_status", lambda: next(statuses))
    monkeypatch.setattr(app.time, "sleep", lambda seconds: sleeps.append(seconds))
    result = app._run_low_memory_automatic_cycle()
    assert result["isolated_automatic"] is True
    assert result["scheduled"] == {"shopping": False, "budget": False}
    assert sleeps == [1.0]


def test_healthy_existing_supervisor_is_not_replaced(monkeypatch):
    child = prepare(monkeypatch, exit_code=None, supervisor=threading.current_thread())
    monkeypatch.setattr(
        app, "_ensure_isolated_heavy_supervisor_locked",
        lambda: (_ for _ in ()).throw(
            AssertionError("existing supervisor already owns child")
        ),
    )
    result = app._isolated_heavy_worker_status()
    assert result["supervisor_running"] is True
    assert result["running"] is True
    assert result["reap_pending"] is False
    assert app._ISOLATED_HEAVY_PROCESS is child
