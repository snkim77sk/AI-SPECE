"""4.1.267: concurrent automatic retries and manual source submissions.

No external API, live database, durable checkpoint or destructive setup is used.
"""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import vnext_clean_app as app


class FakeChild:
    def __init__(self, pid=426700):
        self.pid = pid

    def poll(self):
        return None


def _prepare(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "can_collect_sources", lambda: True)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_LAST", {"kind": "", "exit_code": None})
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {"shopping": 0, "budget": 0})
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "state": "IDLE", "last_status": "IDLE", "last_error": "",
        "shopping_run_state": "IDLE", "shopping_last_error": "",
        "budget_run_state": "IDLE", "budget_last_error": "",
    })
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: None)


def _start(monkeypatch, mode, pid=426700):
    with app._ISOLATED_HEAVY_LOCK:
        app._ISOLATED_HEAVY_PROCESS = FakeChild(pid)
        app._ISOLATED_HEAVY_KIND = mode
        app._publish_isolated_source_start_locked(mode, "RUNNING")


@pytest.mark.parametrize("source", ["shopping", "budget"])
def test_manual_hold_racing_automatic_start_keeps_running_status(monkeypatch, source):
    _prepare(monkeypatch)

    def deny_after_automatic_start(_mode):
        _start(monkeypatch, source)
        return False

    monkeypatch.setattr(app, "_isolated_worker_admission_ok", deny_after_automatic_start)
    assert app.schedule_manual_collection(source) is False
    assert app._RECENT_COLLECTION_STATE[f"{source}_run_state"] == "RUNNING"
    assert app._RECENT_COLLECTION_STATE[f"{source}_last_error"] == ""
    assert app._ISOLATED_HEAVY_KIND == source
    assert app._ISOLATED_HEAVY_PENDING == []


def test_manual_hold_racing_other_source_queues_instead_of_clobbering(monkeypatch):
    _prepare(monkeypatch)

    def deny_after_budget_starts(_mode):
        _start(monkeypatch, "budget")
        return False

    monkeypatch.setattr(app, "_isolated_worker_admission_ok", deny_after_budget_starts)
    assert app.schedule_manual_collection("shopping") is True
    assert app._ISOLATED_HEAVY_KIND == "budget"
    assert app._ISOLATED_HEAVY_PENDING == ["shopping"]
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "RUNNING"
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "QUEUED"
    assert app._RECENT_COLLECTION_STATE["state"] == "RUNNING"


@pytest.mark.parametrize("source", ["shopping", "budget"])
def test_manual_hold_with_no_active_or_pending_remains_safe_waiting_memory(monkeypatch, source):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: False)
    assert app.schedule_manual_collection(source) is False
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._RECENT_COLLECTION_STATE[f"{source}_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_STATE[f"{source}_last_error"] == "LOW_MEMORY_WORKER_UNSAFE"


def test_no_process_but_queued_budget_is_already_reserved_not_launched_again(monkeypatch):
    _prepare(monkeypatch)
    app._ISOLATED_HEAVY_PENDING.append("budget")
    app._RECENT_COLLECTION_STATE["budget_run_state"] = "QUEUED"
    monkeypatch.setattr(
        app, "_isolated_worker_admission_ok",
        lambda _mode: (_ for _ in ()).throw(
            AssertionError("queued source must not probe or launch again")
        ),
    )
    assert app._request_isolated_source_worker("budget") == "ALREADY"
    assert app.schedule_manual_collection("budget") is False
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "QUEUED"
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]


def test_failed_queued_probe_must_not_overwrite_manually_running_same_source(monkeypatch):
    _prepare(monkeypatch)
    first = FakeChild(426701)
    budget = FakeChild(426702)
    app._ISOLATED_HEAVY_PROCESS = first
    app._ISOLATED_HEAVY_KIND = "shopping"
    app._ISOLATED_HEAVY_PENDING = ["budget"]
    app._ISOLATED_HEAVY_SUPERVISOR = threading.current_thread()
    app._RECENT_COLLECTION_STATE.update(
        shopping_run_state="RUNNING", budget_run_state="QUEUED"
    )
    observed = []

    def wait_for_child(process, kind):
        if kind == "budget":
            observed.append(app._RECENT_COLLECTION_STATE["budget_run_state"])
            assert process is budget
        return 0, False

    def admission(mode):
        assert mode == "budget"
        _start(monkeypatch, "budget", pid=budget.pid)
        # Ensure the sentinel is the exact object checked by the supervisor.
        app._ISOLATED_HEAVY_PROCESS = budget
        return False

    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", wait_for_child)
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", admission)
    app._isolated_heavy_supervisor_worker()

    assert observed == ["RUNNING"], "old failed admission clobbered new live budget"
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "COMPLETE"
    assert app._ISOLATED_HEAVY_SUPERVISOR is None
    assert app._ISOLATED_HEAVY_PENDING == []


def test_failed_queue_probe_requeues_behind_other_manual_child(monkeypatch):
    _prepare(monkeypatch)
    old = FakeChild(426703)
    new_shopping = FakeChild(426704)
    budget = FakeChild(426705)
    app._ISOLATED_HEAVY_PROCESS = old
    app._ISOLATED_HEAVY_KIND = "shopping"
    app._ISOLATED_HEAVY_PENDING = ["budget"]
    app._ISOLATED_HEAVY_SUPERVISOR = threading.current_thread()
    app._RECENT_COLLECTION_STATE.update(
        shopping_run_state="RUNNING", budget_run_state="QUEUED"
    )
    calls = []
    attempts = [0]

    def admission(mode):
        assert mode == "budget"
        attempts[0] += 1
        if attempts[0] == 1:
            with app._ISOLATED_HEAVY_LOCK:
                app._ISOLATED_HEAVY_PROCESS = new_shopping
                app._ISOLATED_HEAVY_KIND = "shopping"
                app._publish_isolated_source_start_locked("shopping", "RUNNING")
            return False
        return True

    def launch(mode):
        assert mode == "budget"
        calls.append(mode)
        app._ISOLATED_HEAVY_PROCESS = budget
        app._ISOLATED_HEAVY_KIND = "budget"
        return budget

    def wait_for_child(proc, mode):
        if proc is budget:
            assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "RUNNING"
            return 72, False
        return 0, False

    monkeypatch.setattr(app, "_isolated_worker_admission_ok", admission)
    monkeypatch.setattr(app, "_launch_isolated_heavy_worker_locked", launch)
    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", wait_for_child)
    app._isolated_heavy_supervisor_worker()

    assert calls == ["budget"]
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "WAITING_KEYS"
    assert app._ISOLATED_HEAVY_SUPERVISOR is None


def test_manual_and_automatic_batch_share_one_worker_and_one_queued_budget(monkeypatch):
    _prepare(monkeypatch)
    created = []
    rendezvous = threading.Barrier(2)
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(
        app, "_isolated_heavy_worker_status",
        lambda: {"running": False, "pending": [], "supervisor_running": False},
    )
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: True)

    def launch(mode):
        child = FakeChild(426706 + len(created))
        created.append(mode)
        app._ISOLATED_HEAVY_PROCESS = child
        app._ISOLATED_HEAVY_KIND = mode
        return child

    monkeypatch.setattr(app, "_launch_isolated_heavy_worker_locked", launch)

    def auto():
        rendezvous.wait(timeout=3)
        return app._run_low_memory_automatic_cycle()

    def manual():
        rendezvous.wait(timeout=3)
        return app.schedule_manual_collection("shopping")

    with ThreadPoolExecutor(max_workers=2) as pool:
        auto_future = pool.submit(auto)
        manual_future = pool.submit(manual)
        scheduled = auto_future.result(timeout=5)
        manual_result = manual_future.result(timeout=5)

    assert scheduled["scheduled"]["budget"] is True
    assert scheduled["scheduled"]["shopping"] is not manual_result
    assert created == ["shopping"]
    assert app._ISOLATED_HEAVY_KIND == "shopping"
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "RUNNING"
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "QUEUED"


def test_existing_parent_memory_backoff_not_reset_by_manual_denial(monkeypatch):
    _prepare(monkeypatch)
    app._RECENT_COLLECTION_STATE["shopping_run_state"] = "WAITING_MEMORY"
    app._PARENT_MEMORY_ABORT_STREAK["shopping"] = 3
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: False)
    assert app._automatic_cycle_wait_seconds({
        "operational_cycle_lease": "ISOLATED_AUTOMATIC"
    }) == 240
    assert app.schedule_manual_collection("shopping") is False
    assert app._PARENT_MEMORY_ABORT_STREAK["shopping"] == 3
    assert app._automatic_cycle_wait_seconds({
        "operational_cycle_lease": "ISOLATED_AUTOMATIC"
    }) == 240
