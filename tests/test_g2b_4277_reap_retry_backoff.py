"""V4.1.277: persistent unconfirmed reaps cannot cause a retry/log storm.

In-memory worker and deterministic monotonic clocks only. No production PG,
external source API, checkpoint writes, schema changes or real child signals.
"""
import subprocess
import threading

import pytest

import vnext_clean_app as app


class SourceChild:
    pid = 427700

    def poll(self):
        return None


def _setup(monkeypatch, *, source="shopping"):
    child = SourceChild()
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", source)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_LAST", {"kind": "", "exit_code": None})
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 0, "budget": 0,
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "state": "RUNNING", "last_status": "RUNNING", "last_error": "",
        "shopping_run_state": "RUNNING", "shopping_last_error": "",
        "budget_run_state": "RUNNING", "budget_last_error": "",
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_TERMINAL_REVISION", 0)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", threading.Event())
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("reap backoff must not read PostgreSQL")
        ),
    )
    return child


def test_frequent_unconfirmed_child_reaps_get_exponential_backoff_and_sparse_logs(
    monkeypatch, capsys,
):
    child = _setup(monkeypatch)
    attempts = {"signal": 0, "recheck": 0}
    delays = []

    def escalate(proc, source):
        assert proc is child and source == "shopping"
        attempts["signal"] += 1
        assert app._ISOLATED_HEAVY_PROCESS is child
        if attempts["signal"] > 1:
            raise AssertionError("should not send TERM/KILL again before 30 seconds")
        raise RuntimeError("G2B_ISOLATED_CHILD_REAP_UNCONFIRMED")

    def recheck(proc, source):
        assert proc is child and source == "shopping"
        attempts["recheck"] += 1
        assert app._ISOLATED_HEAVY_PROCESS is child
        if attempts["recheck"] <= 12:
            raise RuntimeError("still unable to read process status")
        return 75, True

    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", escalate)
    monkeypatch.setattr(app, "_recheck_unconfirmed_isolated_child", recheck)
    monkeypatch.setattr(app.time, "sleep", lambda sec: delays.append(sec))
    app._isolated_heavy_supervisor_worker()

    assert attempts == {"signal": 1, "recheck": 13}
    assert delays[:7] == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
    assert len(delays) == 13
    assert max(delays) <= 30
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 1
    printed = capsys.readouterr().out
    # Failures at 1,2,4,8 log; subsequent repeated failures stay quiet.
    assert printed.count("G2B_ISOLATED_CHILD_REAP_RETRY") == 4
    assert "still unable to read process status" not in printed


def test_new_escalation_cannot_repeat_before_thirty_seconds(monkeypatch):
    child = _setup(monkeypatch)
    clock = [100.0]
    calls = []
    delays = []

    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    def sleep(seconds):
        delays.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(app.time, "sleep", sleep)

    def escalate(proc, mode):
        assert proc is child
        calls.append(("signal", clock[0]))
        if len(calls) == 1:
            raise RuntimeError("first unconfirmed")
        return 75, True

    def recheck(proc, mode):
        assert proc is child
        calls.append(("check", clock[0]))
        raise RuntimeError("status still unknown")

    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", escalate)
    monkeypatch.setattr(app, "_recheck_unconfirmed_isolated_child", recheck)
    app._isolated_heavy_supervisor_worker()

    escalation_times = [when for kind, when in calls if kind == "signal"]
    assert escalation_times == [100.0, 131.5]
    assert escalation_times[1] - escalation_times[0] >= 30
    assert len([1 for kind, _ in calls if kind == "check"]) == 5
    assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_MEMORY"


class UncertainChild:
    pid = 427701

    def __init__(self, *, wait_result=None, poll_result=None, wait_error=None):
        self.wait_result = wait_result
        self.poll_result = poll_result
        self.wait_error = wait_error
        self.wait_calls = []
        self.poll_calls = 0

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.wait_error:
            raise self.wait_error
        return self.wait_result

    def poll(self):
        self.poll_calls += 1
        return self.poll_result


def test_bounded_recheck_uses_authoritative_wait_success():
    process = UncertainChild(wait_result=0)
    assert app._recheck_unconfirmed_isolated_child(process, "shopping") == (0, False)
    assert process.wait_calls == [0.5]
    assert process.poll_calls == 0


@pytest.mark.parametrize("error", [
    ChildProcessError("unknown child"), RuntimeError("status unavailable"),
    MemoryError("memory pressure"),
])
def test_wait_error_with_poll_zero_returns_resumable_hold_not_false_complete(error):
    process = UncertainChild(wait_error=error, poll_result=0)
    assert app._recheck_unconfirmed_isolated_child(process, "budget") == (75, True)
    assert process.wait_calls == [0.5]
    assert process.poll_calls == 1


def test_recheck_timeout_with_no_terminal_confirmation_keeps_process_slot():
    process = UncertainChild(
        wait_error=subprocess.TimeoutExpired("g2b_heavy_worker", 0.5),
        poll_result=None,
    )
    with pytest.raises(RuntimeError, match="G2B_ISOLATED_CHILD_REAP_UNCONFIRMED"):
        app._recheck_unconfirmed_isolated_child(process, "shopping")
    assert process.wait_calls == [0.5]
    assert process.poll_calls == 1


class BrokenPoll:
    pid = 427702

    def poll(self):
        raise OSError("sensitive OS path")


def test_status_poll_errors_are_logged_at_most_once_per_minute(monkeypatch, capsys):
    process = BrokenPoll()
    _setup(monkeypatch)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", process)
    monkeypatch.setattr(app, "_ISOLATED_CHILD_STATUS_POLL_WARN_AT", 0.0)
    clock = [10.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    for _ in range(100):
        status = app._isolated_heavy_worker_status()
        assert status["running"] is True
        assert status["pid"] == process.pid
    before = capsys.readouterr().out
    assert before.count("G2B_ISOLATED_CHILD_STATUS_POLL_HOLD") == 1
    assert "sensitive OS path" not in before
    clock[0] = 69.9
    app._isolated_heavy_worker_status()
    assert "G2B_ISOLATED_CHILD_STATUS_POLL_HOLD" not in capsys.readouterr().out
    clock[0] = 70.0
    app._isolated_heavy_worker_status()
    assert capsys.readouterr().out.count("G2B_ISOLATED_CHILD_STATUS_POLL_HOLD") == 1
