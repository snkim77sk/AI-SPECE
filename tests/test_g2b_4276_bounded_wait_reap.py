"""4.1.276: wait/poll/terminate/kill errors never orphan source execution.

Only deterministic disposable fake Popen objects and in-memory state.
No production PG, source API, checkpoint writes or schema modifications.
"""
import subprocess
import threading

import pytest

import vnext_clean_app as app


def _prepare(monkeypatch, *, memory_hold=False):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(
        app.memory_guard, "container_budget_snapshot",
        lambda: {
            "limit_mib": 512, "effective_mib": 449 if memory_hold else 110,
            "block_threshold_mib": 460.8,
            "blocked": False, "oom_group": 0,
        },
    )
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 92)
    monkeypatch.setattr(app.memory_guard, "soft_limit_mib", lambda: 160)
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("reaper must not read a PostgreSQL error")
        ),
    )


class IntermittentWait:
    pid = 427600

    def __init__(self):
        self.wait_calls = 0
        self.terminated = 0
        self.killed = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        assert timeout == 0.5
        if self.wait_calls == 1:
            raise OSError(4, "SECRET_WAIT_ERROR")
        return 0

    def poll(self):
        return None

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1


def test_transient_wait_oserror_retries_without_killing_success(monkeypatch, capsys):
    _prepare(monkeypatch)
    child = IntermittentWait()
    assert app._wait_for_isolated_heavy_child(child, "shopping") == (0, False)
    assert child.wait_calls == 2
    assert child.terminated == 0
    assert child.killed == 0
    printed = capsys.readouterr().out
    assert "G2B_ISOLATED_CHILD_WAIT_DEGRADED" in printed
    assert "SECRET_WAIT_ERROR" not in printed


class RepeatedWaitErrors:
    pid = 427601

    def __init__(self):
        self.attempts = 0
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        self.attempts += 1
        assert timeout in {0.5, 2}
        if not self.terminated:
            raise ChildProcessError("ECHILD sensitive detail")
        return -15

    def poll(self):
        return None if not self.terminated else -15

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_persistent_wait_echild_preempts_only_child_into_resumable_hold(monkeypatch):
    _prepare(monkeypatch)
    child = RepeatedWaitErrors()
    result = app._wait_for_isolated_heavy_child(child, "budget")
    assert result == (75, True)
    assert child.terminated
    assert not child.killed
    assert child.attempts == 4


class UncertainZero:
    pid = 427602

    def __init__(self):
        self.terminated = False

    def wait(self, timeout=None):
        raise OSError(10, "unknown child return status")

    def poll(self):
        return 0

    def terminate(self):
        self.terminated = True


def test_wait_fault_with_poll_zero_never_falsely_publishes_complete(monkeypatch):
    _prepare(monkeypatch)
    child = UncertainZero()
    result = app._wait_for_isolated_heavy_child(child, "shopping")
    assert result == (75, True)
    assert not child.terminated


class KillAfterTimeout:
    pid = 427603

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.timeouts = []

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        if self.killed:
            return -9
        raise subprocess.TimeoutExpired("worker", timeout)

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_memory_terminate_timeout_kill_waits_boundedly(monkeypatch, mode):
    _prepare(monkeypatch, memory_hold=True)
    child = KillAfterTimeout()
    assert app._wait_for_isolated_heavy_child(child, mode) == (-9, True)
    assert child.terminated and child.killed
    assert child.timeouts == [0.5, 2, 2]
    assert all(timeout is not None for timeout in child.timeouts)


class PostKillWaitUnavailable:
    pid = 427604

    def __init__(self, *, poll_after_kill):
        self.poll_after_kill = poll_after_kill
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired("worker", timeout)
        raise OSError(10, "post-kill wait unverified")

    def poll(self):
        if self.killed:
            return -9 if self.poll_after_kill else None
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_post_kill_wait_error_can_use_verified_nonzero_poll(monkeypatch):
    _prepare(monkeypatch, memory_hold=True)
    child = PostKillWaitUnavailable(poll_after_kill=True)
    assert app._wait_for_isolated_heavy_child(child, "shopping") == (75, True)
    assert child.terminated and child.killed


def test_post_kill_wait_and_poll_both_unverified_never_release_slot(monkeypatch):
    _prepare(monkeypatch, memory_hold=True)
    child = PostKillWaitUnavailable(poll_after_kill=False)
    with pytest.raises(RuntimeError, match="G2B_ISOLATED_CHILD_REAP_UNCONFIRMED"):
        app._wait_for_isolated_heavy_child(child, "shopping")
    assert child.terminated and child.killed


class PollRaisesDuringMemoryAbort:
    pid = 427605

    def __init__(self):
        self.terminated = False

    def wait(self, timeout=None):
        if self.terminated:
            return -15
        raise subprocess.TimeoutExpired("worker", timeout)

    def poll(self):
        raise OSError("poll cannot inspect child")

    def terminate(self):
        self.terminated = True


def test_poll_exception_during_memory_hold_still_reaps_child(monkeypatch):
    _prepare(monkeypatch, memory_hold=True)
    child = PollRaisesDuringMemoryAbort()
    assert app._wait_for_isolated_heavy_child(child, "budget") == (-15, True)
    assert child.terminated


def test_supervisor_exception_retries_without_releasing_active_child(monkeypatch):
    _prepare(monkeypatch)
    child = IntermittentWait()
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
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
        "budget_run_state": "IDLE", "budget_last_error": "",
    })

    observed = []
    attempts = [0]

    def fake_wait(proc, kind):
        assert proc is child and kind == "shopping"
        attempts[0] += 1
        if attempts[0] == 1:
            observed.append(app._ISOLATED_HEAVY_PROCESS is child)
            assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "RUNNING"
            raise RuntimeError("unreaped")
        observed.append(app._ISOLATED_HEAVY_PROCESS is child)
        return 0, False

    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", fake_wait)
    # V4.1.277: after the first uncertain wait the same Popen is probed
    # without immediately sending TERM/KILL a second time.
    recheck_calls = []
    def recheck(proc, kind):
        assert proc is child and kind == "shopping"
        recheck_calls.append(True)
        observed.append(app._ISOLATED_HEAVY_PROCESS is child)
        return 0, False

    monkeypatch.setattr(app, "_recheck_unconfirmed_isolated_child", recheck)
    sleeps = []
    monkeypatch.setattr(app.time, "sleep", lambda seconds: sleeps.append(seconds))
    app._isolated_heavy_supervisor_worker()

    assert attempts == [1]
    assert recheck_calls == [True]
    assert observed == [True, True]
    assert sleeps == [0.5]
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_SUPERVISOR is None
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 1


def test_status_poll_error_keeps_child_reserved_without_http_exception(monkeypatch):
    _prepare(monkeypatch)
    child = PollRaisesDuringMemoryAbort()
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", ["budget"])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    status = app._isolated_heavy_worker_status()
    assert status["running"] is True
    assert status["pid"] == child.pid
    assert status["pending"] == ["budget"]
    assert status["supervisor_running"] is True
    assert app._ISOLATED_HEAVY_PROCESS is child
