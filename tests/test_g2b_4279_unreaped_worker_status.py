"""4.1.279: reserved Popen and finalization must never look COMPLETE in UI.

In-memory production status/mode functions and fake subprocesses only.
No real source keys, PostgreSQL, business-data mutation, or external APIs.
"""
import inspect
import threading

import pytest

import vnext_clean_app as app


class Child:
    pid = 427900

    def __init__(self, exit_code=None, poll_error=None):
        self.exit_code = exit_code
        self.poll_error = poll_error
        self.poll_calls = 0

    def poll(self):
        self.poll_calls += 1
        if self.poll_error:
            raise self.poll_error
        return self.exit_code


def _setup(monkeypatch, *, kind="shopping", child=None, supervisor=True,
           state="COMPLETE", pending=()):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "can_collect_sources", lambda: True)
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: False)
    monkeypatch.setattr(app, "_memory_status_fields", lambda **_: {})
    monkeypatch.setattr(app, "post_boot_maintenance_enabled", lambda: False)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_THREAD", None)
    monkeypatch.setattr(
        app, "_RECENT_COLLECTION_STATE", {
            "state": state, "last_status": state,
            "shopping_run_state": state,
            "shopping_last_status": state,
            "budget_run_state": state,
            "budget_last_status": state,
            "shopping_last_error": "", "budget_last_error": "",
        },
    )
    monkeypatch.setattr(
        app, "_MANUAL_COLLECTION_THREADS",
        {"shopping": None, "budget": None},
    )
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", kind if child else "")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", list(pending))
    monkeypatch.setattr(
        app, "_ISOLATED_HEAVY_SUPERVISOR",
        threading.current_thread() if supervisor else None,
    )
    monkeypatch.setattr(
        app, "_ISOLATED_HEAVY_LAST",
        {"kind": "", "exit_code": None},
    )
    monkeypatch.setattr(
        app, "_ISOLATED_HEAVY_SUPERVISOR_RECOVERY_RETRY_AT",
        float("inf") if not supervisor and child else 0.0,
    )
    monkeypatch.setattr(app, "_ISOLATED_CHILD_STATUS_POLL_WARN_AT", 0.0)
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("status must never query PostgreSQL")
        ),
    )


@pytest.mark.parametrize("kind", ["shopping", "budget"])
@pytest.mark.parametrize("exit_code", [0, 75, 137])
def test_exited_but_unreaped_worker_is_never_presented_complete(
    monkeypatch, kind, exit_code,
):
    child = Child(exit_code)
    _setup(monkeypatch, kind=kind, child=child)
    snapshot = app.recent_collection_status()

    isolated = snapshot["isolated_heavy_worker"]
    assert isolated["running"] is False
    assert isolated["exit_code"] == exit_code
    assert isolated["slot_reserved"] is True
    assert isolated["exit_confirmation_pending"] is True
    assert isolated["reap_pending"] is True
    assert isolated["supervisor_running"] is True
    assert snapshot[f"{kind}_run_state"] == "REAP_PENDING"
    assert snapshot[f"{kind}_last_status"] == "REAP_PENDING"
    assert snapshot[f"manual_{kind}_running"] is True
    assert snapshot[f"manual_{kind}_reap_pending"] is True
    assert snapshot["state"] == "RUNNING"
    assert snapshot["manual_sources_running"] == 1
    assert app._ISOLATED_HEAVY_PROCESS is child
    # Reporting must not convert a synthetic completed checkout into actual
    # durable state or mutate the existing checkpoint/user data.
    assert app._RECENT_COLLECTION_STATE[f"{kind}_run_state"] == "COMPLETE"
    assert child.poll_calls >= 1


@pytest.mark.parametrize("kind", ["shopping", "budget"])
def test_same_source_manual_retry_is_rejected_during_unreaped_finalization(
    monkeypatch, kind,
):
    child = Child(0)
    _setup(monkeypatch, kind=kind, child=child)
    monkeypatch.setattr(
        app, "_isolated_worker_admission_ok",
        lambda _: (_ for _ in ()).throw(
            AssertionError("unreaped Popen may not trigger worker admission")
        ),
    )
    monkeypatch.setattr(
        app, "_launch_isolated_heavy_worker_locked",
        lambda _: (_ for _ in ()).throw(
            AssertionError("unreaped Popen may not be replaced")
        ),
    )

    assert app.schedule_manual_collection(kind) is False
    assert app._ISOLATED_HEAVY_PROCESS is child
    assert app._ISOLATED_HEAVY_PENDING == []


def test_other_source_is_queued_behind_unreaped_existing_child(monkeypatch):
    child = Child(0)
    _setup(monkeypatch, kind="shopping", child=child)
    monkeypatch.setattr(
        app, "_isolated_worker_admission_ok",
        lambda _: (_ for _ in ()).throw(
            AssertionError("cannot launch while first Popen is unreaped")
        ),
    )
    assert app.schedule_manual_collection("budget") is True
    assert app._ISOLATED_HEAVY_PROCESS is child
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]
    status = app.recent_collection_status()
    assert status["shopping_run_state"] == "REAP_PENDING"
    assert status["budget_run_state"] == "QUEUED"
    assert status["manual_shopping_running"] is True
    assert status["manual_budget_running"] is True
    assert status["manual_budget_queued"] is True


def test_live_child_overrides_stale_complete_without_changing_saved_state(monkeypatch):
    child = Child(None)
    _setup(monkeypatch, child=child)
    snapshot = app.recent_collection_status()

    assert snapshot["isolated_heavy_worker"]["running"] is True
    assert snapshot["isolated_heavy_worker"]["reap_pending"] is False
    assert snapshot["shopping_run_state"] == "RUNNING"
    assert snapshot["manual_shopping_running"] is True
    assert snapshot["manual_shopping_reap_pending"] is False
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"


def test_no_reaper_but_reserved_child_is_reported_recovery_hold(monkeypatch):
    child = Child(None)
    _setup(monkeypatch, child=child, supervisor=False)
    snapshot = app.recent_collection_status()
    assert snapshot["isolated_heavy_worker"]["slot_reserved"] is True
    assert snapshot["isolated_heavy_worker"]["reaper_recovery_pending"] is True
    assert snapshot["isolated_heavy_worker"]["reap_pending"] is True
    assert snapshot["shopping_run_state"] == "REAPER_RECOVERY"
    assert snapshot["manual_shopping_running"] is True
    assert app.schedule_manual_collection("shopping") is False


def test_unreadable_poll_is_not_reported_complete_or_http_failure(monkeypatch):
    child = Child(poll_error=OSError("secret process path"))
    _setup(monkeypatch, child=child)
    snapshot = app.recent_collection_status()

    assert snapshot["isolated_heavy_worker"]["status_poll_unavailable"] is True
    assert snapshot["isolated_heavy_worker"]["slot_reserved"] is True
    assert snapshot["isolated_heavy_worker"]["reap_pending"] is True
    assert snapshot["shopping_run_state"] == "WORKER_STATUS_UNKNOWN"
    assert snapshot["manual_shopping_reap_pending"] is True
    assert snapshot["state"] == "RUNNING"


def test_without_reserved_child_legitimate_complete_remains_complete(monkeypatch):
    _setup(monkeypatch, child=None, supervisor=False)
    snapshot = app.recent_collection_status()
    assert snapshot["isolated_heavy_worker"]["slot_reserved"] is False
    assert snapshot["isolated_heavy_worker"]["reap_pending"] is False
    assert snapshot["shopping_run_state"] == "COMPLETE"
    assert snapshot["manual_shopping_running"] is False
    assert snapshot["state"] == "COMPLETE"


def test_runtime_stage_monitor_explains_unreaped_checkpoint_safety():
    source_status = {
        "shopping_run_state": "REAP_PENDING",
        "budget_run_state": "REAPER_RECOVERY",
    }
    stages = [
        {"dataset": "shopping_delivery", "state": "RUNNING", "message": "old"},
        {"dataset": "budget", "state": "STALE", "message": "old"},
        {"dataset": "budget_appropriation", "state": "COMPLETE", "message": "old"},
    ]
    source = {"stages": stages, "summary": {"running": 1, "complete": 1}}
    result = app._apply_runtime_wait_states(source, source_status, {})
    updated = result["stages"]
    assert updated[0]["state"] == "REAP_PENDING"
    assert "checkpoint 보존" in updated[0]["message"]
    assert updated[1]["state"] == "REAPER_RECOVERY"
    # A persisted COMPLETE record is historical evidence, not falsified.
    assert updated[2]["state"] == "COMPLETE"
    assert stages[0]["state"] == "RUNNING"


def test_collection_monitor_renders_a_specific_reap_wait_label_and_disables_buttons():
    source = inspect.getsource(app.collection_monitor_page)
    assert '"REAP_PENDING": "종료 확인 중"' in source
    assert '"REAPER_RECOVERY": "감독 복구 대기"' in source
    assert '"WORKER_STATUS_UNKNOWN": "상태 확인 중"' in source
    assert '"manual_shopping_reap_pending"' in source
    assert '"manual_budget_reap_pending"' in source
    assert 'disabled>나라장터 종료확인 중' in source
    assert 'disabled>지방재정365 종료확인 중' in source


def test_automatic_scheduler_waits_for_exited_child_terminal_publication(monkeypatch):
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(app, "schedule_manual_collection", lambda _: False)
    statuses = iter([
        {
            "running": False, "pending": [], "slot_reserved": True,
            "supervisor_running": True, "reap_pending": True,
        },
        {
            "running": False, "pending": [], "slot_reserved": False,
            "supervisor_running": False, "reap_pending": False,
        },
    ])
    sleeps = []
    monkeypatch.setattr(app, "_isolated_heavy_worker_status", lambda: next(statuses))
    monkeypatch.setattr(app.time, "sleep", lambda delay: sleeps.append(delay))
    outcome = app._run_low_memory_automatic_cycle()
    assert outcome["isolated_automatic"] is True
    assert sleeps == [1.0]
