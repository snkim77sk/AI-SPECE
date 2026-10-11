"""G2B 4.1.269: force/key notification while auto cycle is busy is not lost.

Deterministic source-free state/notification tests. No real source API, SQL,
checkpoint update or customer data is accessed.
"""
import inspect
import threading

import pytest

import vnext_clean_app as app


class ScriptedWake:
    def __init__(self, action):
        self.action = action
        self.calls = []

    def clear(self):
        return None

    def set(self):
        return None

    def wait(self, seconds):
        self.calls.append(seconds)
        return self.action(len(self.calls), seconds)


def _prepare(monkeypatch, *, shopping="WAITING_MEMORY", budget="COMPLETE"):
    monkeypatch.setattr(app, "_RECENT_COLLECTION_EXPLICIT_WAKE_REVISION", 0)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_TERMINAL_REVISION", 0)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "shopping_run_state": shopping, "budget_run_state": budget,
        "shopping_last_error": "", "budget_last_error": "",
        "state": "WAITING_MEMORY", "last_status": "WAITING_MEMORY",
    })
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 3, "budget": 0,
    })
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("wake must not read historical PostgreSQL errors")
        ),
    )


@pytest.mark.parametrize("order", ["force_then_finish", "finish_then_force"])
def test_force_and_completion_same_sleep_signal_explicit_wins(monkeypatch, order):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)

    def action(n, seconds):
        assert n == 1
        assert seconds == 240
        if order == "force_then_finish":
            app._signal_explicit_automatic_cycle_wake()
            app._isolated_worker_exit_state("shopping", 0)
        else:
            app._isolated_worker_exit_state("shopping", 0)
            app._signal_explicit_automatic_cycle_wake()
        return True

    wake=ScriptedWake(action)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh({}) == "EXPLICIT_WAKE"
    assert wake.calls == [240]
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"


@pytest.mark.parametrize("source", ["force", "keys"])
def test_explicit_during_running_auto_cycle_is_honored_once(monkeypatch, source):
    _prepare(monkeypatch, shopping="WAITING_KEYS")
    state={"active":True,"cycles":0}
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: state["active"])
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 0)
    monkeypatch.setattr(app, "_web_source_isolation_required", lambda: True)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", threading.Event())
    monkeypatch.setattr(app, "_RECENT_COLLECTION_THREAD", threading.current_thread())
    monkeypatch.setattr(app, "set_source_credential", lambda *_: None)

    def automatic():
        state["cycles"]+=1
        if state["cycles"]==1:
            # The currently running cycle does not know about the fresh key
            # or new operator request. It must not discard the wake when
            # transitioning to auto BACKOFF.
            if source=="force":
                assert app.schedule_recent_collection(force=True) is False
            else:
                assert app._save_source_key_updates(g2b_key="CI_NO_NETWORK") is True
            app._isolated_worker_exit_state("shopping",72)
        else:
            state["active"]=False
        assert state["cycles"] <= 2, "duplicate or endless automatic cycle"
        return {"operational_cycle_lease":"ISOLATED_AUTOMATIC"}

    monkeypatch.setattr(app, "_run_low_memory_automatic_cycle", automatic)
    app._recent_collection_worker()
    assert state["cycles"] == 2
    assert app._RECENT_COLLECTION_EXPLICIT_WAKE_REVISION == 1
    assert app._RECENT_COLLECTION_THREAD is None
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_KEYS"


def test_multiple_explicit_wakes_while_running_coalesce_into_one(monkeypatch):
    _prepare(monkeypatch)
    state={"active":True,"count":0}
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: state["active"])
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 0)
    monkeypatch.setattr(app, "_web_source_isolation_required", lambda: True)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", threading.Event())
    monkeypatch.setattr(app, "_RECENT_COLLECTION_THREAD", threading.current_thread())

    def cycle():
        state["count"]+=1
        if state["count"]==1:
            for _ in range(4):
                app._signal_explicit_automatic_cycle_wake()
            app._isolated_worker_exit_state("shopping",0)
        else:
            state["active"]=False
        assert state["count"] <= 2
        return {"operational_cycle_lease":"ISOLATED_AUTOMATIC"}

    monkeypatch.setattr(app, "_run_low_memory_automatic_cycle", cycle)
    app._recent_collection_worker()
    assert state["count"]==2
    assert app._RECENT_COLLECTION_EXPLICIT_WAKE_REVISION==4


def test_explicit_at_wait_entry_preempts_even_when_completion_also_pending(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    wake=ScriptedWake(lambda *_: (_ for _ in ()).throw(
        AssertionError("must not block when explicit revision already advanced")
    ))
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    app._signal_explicit_automatic_cycle_wake()
    app._isolated_worker_exit_state("shopping",0)
    result=app._wait_for_automatic_collection_refresh(
        {"operational_cycle_lease":"ISOLATED_AUTOMATIC"},
        cycle_explicit_revision=0,
    )
    assert result=="EXPLICIT_WAKE"
    assert wake.calls==[]


def test_terminal_only_during_cycle_does_not_start_redundant_fetch(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    app._isolated_worker_exit_state("shopping",0)
    wake=ScriptedWake(lambda _n, _sec: False)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh(
        {}, cycle_explicit_revision=0,
    )=="DEADLINE"
    assert wake.calls==[app.SHOPPING_SYNC_INTERVAL_SECONDS]


def test_outstanding_explicit_signal_is_not_lost_during_delay_calculation(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    real=app._automatic_cycle_wait_seconds

    def computed(outcome):
        seconds=real(outcome)
        app._signal_explicit_automatic_cycle_wake()
        app._isolated_worker_exit_state("shopping",0)
        return seconds

    monkeypatch.setattr(app, "_automatic_cycle_wait_seconds", computed)
    wake=ScriptedWake(lambda *_: True)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", wake)
    assert app._wait_for_automatic_collection_refresh(
        {}, cycle_explicit_revision=0
    )=="EXPLICIT_WAKE"
    assert wake.calls==[240]


def test_waiter_still_uses_original_no_db_revision_contract(monkeypatch):
    source=inspect.getsource(app._wait_for_automatic_collection_refresh)
    assert "_RECENT_COLLECTION_EXPLICIT_WAKE_REVISION" in source
    assert "cycle_explicit_revision" in source
    assert "_RECENT_COLLECTION_WAKE.clear()" in source
    assert "db.connect(" not in source
    assert "schedule_manual_collection(" not in source
