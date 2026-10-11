"""4.1.268: manual completion rebases auto retry without a redundant collection.

The tests run the production state + scheduler wait helper with deterministic
wake signals. They never contact source APIs or production PostgreSQL.
"""

import inspect

import pytest

import vnext_clean_app as app


class ScriptedWake:
    def __init__(self, action):
        self.action = action
        self.waits = []
        self.clears = 0
        self.sets = 0

    def clear(self):
        self.clears += 1

    def set(self):
        self.sets += 1

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.action(len(self.waits), seconds)


def _prepare(monkeypatch, *, shopping="WAITING_MEMORY", budget="COMPLETE",
             shopping_streak=3, budget_streak=0):
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_TERMINAL_REVISION", 0)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_EXPLICIT_WAKE_REVISION", 0)
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": shopping_streak, "budget": budget_streak,
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "state": "WAITING_MEMORY", "last_status": "WAITING_MEMORY",
        "last_error": "",
        "shopping_run_state": shopping, "shopping_last_error": "",
        "budget_run_state": budget, "budget_last_error": "",
    })
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_args, **_kw: (_ for _ in ()).throw(
            AssertionError("scheduler must not query PostgreSQL")
        ),
    )


def test_manual_shopping_completion_resets_backoff_to_regular_interval(monkeypatch):
    _prepare(monkeypatch)

    def action(call, seconds):
        if call == 1:
            assert seconds == 240
            app._isolated_worker_exit_state("shopping", 0)
            return True
        assert call == 2
        assert seconds == app.SHOPPING_SYNC_INTERVAL_SECONDS
        app._signal_explicit_automatic_cycle_wake()
        return True

    wake = ScriptedWake(action)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    result = app._wait_for_automatic_collection_refresh({
        "operational_cycle_lease": "ISOLATED_AUTOMATIC"
    })
    assert result == "EXPLICIT_WAKE"
    assert wake.waits == [240, app.SHOPPING_SYNC_INTERVAL_SECONDS]
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._PARENT_MEMORY_ABORT_STREAK["shopping"] == 0


def test_manual_completion_preserves_other_sources_memory_backoff(monkeypatch):
    _prepare(monkeypatch, budget="WAITING_MEMORY", budget_streak=2)

    def action(call, seconds):
        if call == 1:
            assert seconds == 240
            app._isolated_worker_exit_state("shopping", 0)
        else:
            assert seconds == 120
            app._signal_explicit_automatic_cycle_wake()
        return True

    wake = ScriptedWake(action)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh({}) == "EXPLICIT_WAKE"
    assert wake.waits == [240, 120]
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "WAITING_MEMORY"


def test_manual_quota_completion_rebases_to_next_kst_boundary(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_seconds_until_next_kst_date", lambda *_: 321)

    def action(call, seconds):
        if call == 1:
            assert seconds == 240
            app._isolated_worker_exit_state("shopping", 73)
        else:
            assert seconds == 321
            app._signal_explicit_automatic_cycle_wake()
        return True

    wake = ScriptedWake(action)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh({}) == "EXPLICIT_WAKE"
    assert wake.waits == [240, 321]
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_QUOTA"


def test_manual_memory_failure_recalculates_existing_capped_backoff(monkeypatch):
    _prepare(monkeypatch)

    def action(call, seconds):
        if call == 1:
            assert seconds == 240
            app._isolated_worker_exit_state(
                "shopping", 75, parent_launch_hold=True
            )
        else:
            assert seconds == 480
            app._signal_explicit_automatic_cycle_wake()
        return True

    wake = ScriptedWake(action)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh({}) == "EXPLICIT_WAKE"
    assert wake.waits == [240, 480]
    assert app._PARENT_MEMORY_ABORT_STREAK["shopping"] == 4


def test_completion_between_interval_calculation_and_wait_is_not_lost(monkeypatch):
    _prepare(monkeypatch)
    real = app._automatic_cycle_wait_seconds
    calls = []

    def compute(outcome=None, **kwargs):
        calls.append("compute")
        if len(calls) == 1:
            old_interval = real(outcome, **kwargs)
            assert old_interval == 240
            app._isolated_worker_exit_state("shopping", 0)
            return old_interval
        return real(outcome, **kwargs)

    monkeypatch.setattr(app, "_automatic_cycle_wait_seconds", compute)

    def action(call, seconds):
        assert call == 1
        assert seconds == app.SHOPPING_SYNC_INTERVAL_SECONDS
        app._signal_explicit_automatic_cycle_wake()
        return True

    wake = ScriptedWake(action)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh({}) == "EXPLICIT_WAKE"
    assert len(calls) == 2
    assert wake.waits == [app.SHOPPING_SYNC_INTERVAL_SECONDS]


def test_operator_force_wake_remains_immediate_not_a_rebase(monkeypatch):
    _prepare(monkeypatch)
    def action(call, seconds):
        assert call == 1
        assert seconds == 240
        app._signal_explicit_automatic_cycle_wake()
        return True
    wake = ScriptedWake(action)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh({}) == "EXPLICIT_WAKE"
    assert wake.waits == [240]
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_MEMORY"


def test_unchanged_deadline_triggers_one_cycle_without_spin(monkeypatch):
    _prepare(monkeypatch)
    wake = ScriptedWake(lambda _call, _secs: False)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh({}) == "DEADLINE"
    assert wake.waits == [240]


def test_worker_and_saved_keys_use_revision_aware_wake(monkeypatch):
    _prepare(monkeypatch)
    assert "_wait_for_automatic_collection_refresh(outcome)" in inspect.getsource(
        app._recent_collection_worker
    )
    wake = ScriptedWake(lambda *_: False)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    monkeypatch.setattr(app, "set_source_credential", lambda *_: None)
    assert app._save_source_key_updates(g2b_key="TEST_NO_NETWORK") is True
    assert app._RECENT_COLLECTION_EXPLICIT_WAKE_REVISION == 1
    assert wake.sets == 1


@pytest.mark.parametrize("source", ["shopping", "budget"])
def test_budget_shopping_completion_updates_revision_after_state(monkeypatch, source):
    _prepare(monkeypatch)
    wake = ScriptedWake(lambda *_: False)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    app._isolated_worker_exit_state(source, 0)
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 1
    assert app._RECENT_COLLECTION_STATE[f"{source}_run_state"] == "COMPLETE"
    assert wake.sets == 1
