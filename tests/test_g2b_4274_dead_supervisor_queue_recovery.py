"""4.1.274: a dead isolated supervisor must not strand automatic collection.

Deterministic thread/process mocks only. No production PG, checkpoints, source
HTTP, file cleanup or worker creation is used in these regressions.
"""
import threading

import pytest

import vnext_clean_app as app


class ActiveChild:
    pid = 427400

    def poll(self):
        return None


def _setup(monkeypatch, *, pending=("budget",), child=None, supervisor=None):
    # These pre-stagger (4.1.265-274) tests measure process/memory backoff,
    # not KST slot-boundary waiting. Prevent random failures around xx:00/xx:02.
    # The separate 4.1.289 tests cover the real 2h phase cap.
    monkeypatch.setattr(app, "_bound_automatic_wait_to_stagger",
                        lambda delay, *, now=None: delay)
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "can_collect_sources", lambda: True)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping" if child else "")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", list(pending))
    monkeypatch.setattr(
        app, "_ISOLATED_HEAVY_SUPERVISOR",
        threading.Thread(name="unstarted-dead-supervisor")
        if supervisor is None else supervisor,
    )
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_LAST", {
        "kind": "shopping", "exit_code": 0,
    })
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 0, "budget": 0,
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_TERMINAL_REVISION", 0)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", threading.Event())
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "state": "RUNNING", "last_status": "RUNNING", "last_error": "",
        "shopping_run_state": "COMPLETE" if child is None else "RUNNING",
        "shopping_last_error": "",
        "budget_run_state": "QUEUED", "budget_last_error": "",
    })
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("recovery cannot read historical PostgreSQL errors")
        ),
    )


def test_dead_supervisor_with_no_child_releases_orphaned_queue(monkeypatch):
    _setup(monkeypatch)
    status = app._isolated_heavy_worker_status()

    assert status["running"] is False
    assert status["supervisor_running"] is False
    assert status["pending"] == []
    assert app._ISOLATED_HEAVY_SUPERVISOR is None
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_STATE["budget_last_error"] == "PARENT_WORKER_LAUNCH_HOLD"
    assert app._PARENT_MEMORY_ABORT_STREAK["budget"] == 1
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 1
    assert app._RECENT_COLLECTION_WAKE.is_set()
    assert app._automatic_cycle_wait_seconds({
        "operational_cycle_lease": "ISOLATED_AUTOMATIC",
    }) == 60


def test_repeated_status_poll_does_not_repeat_orphan_hold(monkeypatch):
    _setup(monkeypatch)
    first = app._isolated_heavy_worker_status()
    streak = app._PARENT_MEMORY_ABORT_STREAK["budget"]
    revision = app._RECENT_COLLECTION_TERMINAL_REVISION

    second = app._isolated_heavy_worker_status()
    assert first["supervisor_running"] is False
    assert second["supervisor_running"] is False
    assert app._PARENT_MEMORY_ABORT_STREAK["budget"] == streak == 1
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == revision == 1


@pytest.mark.parametrize("pending", [("shopping",), ("shopping", "budget")])
def test_all_orphaned_sources_are_resumable_without_database(monkeypatch, pending):
    _setup(monkeypatch, pending=pending)
    for mode in pending:
        app._RECENT_COLLECTION_STATE[f"{mode}_run_state"] = "QUEUED"
    status = app._isolated_heavy_worker_status()
    assert status["pending"] == []
    for mode in pending:
        assert app._RECENT_COLLECTION_STATE[f"{mode}_run_state"] == "WAITING_MEMORY"
        assert app._PARENT_MEMORY_ABORT_STREAK[mode] == 1
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == len(pending)


def test_live_supervisor_with_empty_child_slot_still_owns_pending(monkeypatch):
    _setup(monkeypatch, supervisor=threading.current_thread())
    status = app._isolated_heavy_worker_status()
    assert status["pending"] == ["budget"]
    assert status["supervisor_running"] is True
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "QUEUED"
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 0
    assert app._request_isolated_source_worker("budget") == "ALREADY"


def test_dead_supervisor_with_live_child_never_frees_worker_slot(monkeypatch):
    child = ActiveChild()
    _setup(monkeypatch, child=child)
    # Real child reaper restoration is covered in the 4.1.275 regression.
    # This lightweight fake lacks wait(); simulate a temporary start HOLD,
    # proving the process slot and queue cannot be discarded.
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: False)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR_RECOVERY_RETRY_AT", 0.0)
    status = app._isolated_heavy_worker_status()
    assert status["running"] is True
    assert status["kind"] == "shopping"
    assert status["supervisor_running"] is False
    assert status["reap_pending"] is True
    assert status["pending"] == ["budget"]
    assert app._ISOLATED_HEAVY_PROCESS is child
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 0


def test_manual_retry_can_start_after_stale_queue_is_released(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda mode: True)
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: None)
    launched = []

    def launch(mode):
        launched.append(mode)
        child = ActiveChild()
        app._ISOLATED_HEAVY_PROCESS = child
        app._ISOLATED_HEAVY_KIND = mode
        return child

    monkeypatch.setattr(app, "_launch_isolated_heavy_worker_locked", launch)
    assert app.schedule_manual_collection("budget") is True
    assert launched == ["budget"]
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "RUNNING"
    assert app._RECENT_COLLECTION_STATE["budget_last_error"] == ""


def test_auto_cycle_does_not_sleep_forever_for_dead_supervisor(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(app, "schedule_manual_collection", lambda _mode: False)
    monkeypatch.setattr(
        app.time, "sleep",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("drained dead-supervisor queue must not block")
        ),
    )
    outcome = app._run_low_memory_automatic_cycle()
    assert outcome["isolated_automatic"] is True
    assert outcome["scheduled"] == {"shopping": False, "budget": False}
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "WAITING_MEMORY"
