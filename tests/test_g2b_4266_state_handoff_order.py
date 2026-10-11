"""4.1.266 isolated-worker generation handoff: no stale RUNNING/QUEUED states.

Real production state functions with faked OS children; never touches
production PostgreSQL, checkpoint rows, source keys or external APIs.
"""

import threading

import pytest

import vnext_clean_app as app


class Finished:
    def __init__(self, code=0, pid=426600):
        self.code = code
        self.pid = pid

    def wait(self, timeout=None):
        return self.code

    def poll(self):
        return self.code


def _setup(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "can_collect_sources", lambda: True)
    monkeypatch.setattr(app, "is_unified", lambda: True)
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


@pytest.mark.parametrize("source", ["shopping", "budget"])
def test_fast_finished_child_cannot_be_resurrected_by_late_manual_started(monkeypatch, source):
    _setup(monkeypatch)
    app._RECENT_COLLECTION_STATE[f"{source}_run_state"] = "COMPLETE"
    monkeypatch.setattr(app, "_request_isolated_source_worker", lambda _src: "STARTED")
    assert app.schedule_manual_collection(source) is True
    assert app._RECENT_COLLECTION_STATE[f"{source}_run_state"] == "COMPLETE"
    assert app._ISOLATED_HEAVY_PROCESS is None


@pytest.mark.parametrize("source", ["shopping", "budget"])
def test_promoted_child_cannot_be_demoted_to_queued_by_late_caller(monkeypatch, source):
    _setup(monkeypatch)
    app._ISOLATED_HEAVY_PROCESS = Finished(code=72)
    app._ISOLATED_HEAVY_KIND = source
    app._RECENT_COLLECTION_STATE[f"{source}_run_state"] = "RUNNING"
    monkeypatch.setattr(app, "_request_isolated_source_worker", lambda _src: "QUEUED")
    assert app.schedule_manual_collection(source) is True
    assert app._RECENT_COLLECTION_STATE[f"{source}_run_state"] == "RUNNING"
    assert not app._ISOLATED_HEAVY_PENDING


def test_previous_terminal_published_before_same_source_new_start(monkeypatch):
    _setup(monkeypatch)
    first = Finished(code=0, pid=426601)
    next_child = Finished(code=72, pid=426602)
    app._ISOLATED_HEAVY_PROCESS = first
    app._ISOLATED_HEAVY_KIND = "shopping"
    app._ISOLATED_HEAVY_SUPERVISOR = threading.current_thread()
    app._RECENT_COLLECTION_STATE["shopping_run_state"] = "RUNNING"
    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", lambda *_: (0, False))
    original_exit = app._isolated_worker_exit_state
    seen = []

    def publish(kind, code, **kwargs):
        assert app._ISOLATED_HEAVY_PROCESS is first
        assert app._request_isolated_source_worker("shopping") == "ALREADY"
        original_exit(kind, code, **kwargs)
        seen.append(app._RECENT_COLLECTION_STATE["shopping_run_state"])

    monkeypatch.setattr(app, "_isolated_worker_exit_state", publish)
    app._isolated_heavy_supervisor_worker()
    assert seen == ["COMPLETE"]
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_SUPERVISOR is None

    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _kind: True)
    def start(mode):
        assert mode == "shopping"
        app._ISOLATED_HEAVY_PROCESS = next_child
        app._ISOLATED_HEAVY_KIND = mode
        return next_child

    monkeypatch.setattr(app, "_launch_isolated_heavy_worker_locked", start)
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: None)
    assert app.schedule_manual_collection("shopping") is True
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "RUNNING"
    assert app._RECENT_COLLECTION_STATE["shopping_last_error"] == ""


def test_source_exit_aggregate_reads_current_other_source_status(monkeypatch):
    _setup(monkeypatch)
    app._RECENT_COLLECTION_STATE.update(
        shopping_run_state="RUNNING", budget_run_state="QUEUED"
    )
    app._isolated_worker_exit_state("shopping", 0)
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "QUEUED"
    assert app._RECENT_COLLECTION_STATE["state"] == "QUEUED"


def test_start_and_queued_statuses_publish_atomically(monkeypatch):
    _setup(monkeypatch)
    with app._ISOLATED_HEAVY_LOCK:
        app._publish_isolated_source_start_locked("budget", "QUEUED")
    assert app._RECENT_COLLECTION_STATE["state"] == "RUNNING"
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "QUEUED"
    with app._ISOLATED_HEAVY_LOCK:
        app._publish_isolated_source_start_locked("budget", "RUNNING")
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "RUNNING"
    assert app._RECENT_COLLECTION_STATE["state"] == "RUNNING"


def test_quota_and_parent_memory_retry_still_publish_original_states(monkeypatch):
    _setup(monkeypatch)
    app._isolated_worker_exit_state("shopping", 73)
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_QUOTA"
    app._isolated_worker_exit_state("shopping", -15, parent_memory_abort=True)
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_MEMORY"
    assert app._PARENT_MEMORY_ABORT_STREAK["shopping"] == 1
    app._isolated_worker_exit_state("shopping", 0)
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._PARENT_MEMORY_ABORT_STREAK["shopping"] == 0
