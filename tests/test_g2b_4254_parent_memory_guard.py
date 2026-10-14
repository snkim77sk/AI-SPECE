"""4.1.254: keep web responsive when a disposable collector grows near OOM."""

import subprocess
import threading

import pytest

import vnext_clean_app as app


class BusyChild:
    pid = 4254

    def __init__(self, *, cooperative=True, completed_code=-15):
        self.running = True
        self.terminated = False
        self.killed = False
        self.cooperative = cooperative
        self.completed_code = completed_code

    def poll(self):
        return None if self.running else self.completed_code

    def terminate(self):
        self.terminated = True
        if self.cooperative:
            self.running = False

    def kill(self):
        self.killed = True
        self.running = False

    def wait(self, timeout=None):
        if self.running:
            if timeout is None:
                raise AssertionError("unbounded process.wait would hang the parent")
            raise subprocess.TimeoutExpired("g2b_heavy_worker", timeout)
        return self.completed_code


def _low_memory_prod(monkeypatch, budget):
    # V4.1.298: these older tests specifically exercise the LEGACY 64MiB
    # admission/runtime behavior. New 25MiB source-start and in-flight 64MiB
    # tests live in test_g2b_4298_source_start_25_mib.py.
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app.memory_guard, "low_memory_web_hold", lambda: True)
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", lambda: dict(budget))
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 91.0)
    monkeypatch.setattr(app.memory_guard, "soft_limit_mib", lambda: 160)


def _budget(effective, *, blocked=False, limit=256, oom_group=0):
    return {
        "limit_mib": limit,
        "effective_mib": effective,
        "block_threshold_mib": 224 if limit == 256 else limit - 32,
        "blocked": blocked,
        "oom_group": oom_group,
    }


def test_supervisor_abort_only_at_existing_256mib_block_threshold():
    assert app._isolated_worker_memory_abort_reason(_budget(223.9)) == ""
    assert app._isolated_worker_memory_abort_reason(_budget(224)) == "CGROUP_BLOCK"
    assert app._isolated_worker_memory_abort_reason(_budget(225, blocked=True)) == "CGROUP_BLOCK"
    assert app._isolated_worker_memory_abort_reason(_budget(120, oom_group=1)) == "CGROUP_OOM_GROUP"
    assert app._isolated_worker_memory_abort_reason(_budget(230, limit=512)) == ""
    assert app._isolated_worker_memory_abort_reason({"blocked": True}) == "CGROUP_MEMORY_PROBE_UNAVAILABLE"


def test_256mib_parent_terminates_child_before_oom_and_reaps_it(monkeypatch):
    _low_memory_prod(monkeypatch, _budget(224))
    child = BusyChild()
    code, aborted = app._wait_for_isolated_heavy_child(child, "shopping")
    assert (code, aborted) == (-15, True)
    assert child.terminated is True
    assert child.killed is False


def test_noncooperative_child_is_killed_after_grace_and_reaped(monkeypatch):
    _low_memory_prod(monkeypatch, _budget(224))
    child = BusyChild(cooperative=False, completed_code=-9)
    code, aborted = app._wait_for_isolated_heavy_child(child, "budget")
    assert (code, aborted) == (-9, True)
    assert child.terminated is True
    assert child.killed is True


def test_no_preempt_under_safe_cgroup_pressure(monkeypatch):
    _low_memory_prod(monkeypatch, _budget(180))
    child = BusyChild()
    checks = [0]
    orig = child.wait
    def finish_after_a_poll(timeout=None):
        checks[0] += 1
        if checks[0] == 2:
            child.running = False
        return orig(timeout=timeout)
    child.wait = finish_after_a_poll
    code, aborted = app._wait_for_isolated_heavy_child(child, "shopping")
    assert (code, aborted) == (-15, False)
    assert child.terminated is False


def test_memory_abort_is_waiting_memory_without_reusing_stale_error(monkeypatch):
    _low_memory_prod(monkeypatch, _budget(224))
    child = BusyChild()
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("parent abort must not read old API/database errors")
        ),
    )
    app._RECENT_COLLECTION_STATE.update(
        shopping_run_state="RUNNING", shopping_last_error="",
        budget_run_state="IDLE", budget_last_error="",
    )
    app._isolated_heavy_supervisor_worker()
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_LAST["exit_code"] == 75
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_STATE["shopping_last_error"] == "PARENT_CGROUP_MEMORY_HOLD"
    assert app._ISOLATED_HEAVY_SUPERVISOR is None


def test_web_soft_limit_also_preempts_child(monkeypatch):
    _low_memory_prod(monkeypatch, _budget(180))
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 160.0)
    child = BusyChild()
    code, aborted = app._wait_for_isolated_heavy_child(child, "shopping")
    assert (code, aborted) == (-15, True)


def test_already_successful_child_remains_complete_when_preempt_races(monkeypatch):
    _low_memory_prod(monkeypatch, _budget(224))
    child = BusyChild(completed_code=0)
    code, aborted = app._wait_for_isolated_heavy_child(child, "shopping")
    assert (code, aborted) == (0, False)


def test_normal_test_mode_supervisor_wait_contract_unchanged(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", True)
    class Finished:
        def wait(self):
            return 72
    assert app._wait_for_isolated_heavy_child(Finished(), "shopping") == (72, False)


def test_cgroup_preempt_changes_no_checkpoint_or_child_admission_policy():
    import inspect
    import db
    import safe_boot_vnext
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in inspect.getsource(db.init_db)
    assert "CHILD_HEADROOM_RESERVE_MIB = 64.0" in inspect.getsource(safe_boot_vnext)
    helper = inspect.getsource(app._wait_for_isolated_heavy_child)
    assert "save_checkpoint" not in helper
    assert "DELETE FROM" not in helper



def test_three_failed_parent_cgroup_probes_stop_child_without_orphan(monkeypatch, capsys):
    _low_memory_prod(monkeypatch, _budget(180))
    def failing_probe():
        raise OSError("synthetic cgroup file unreadable")
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", failing_probe)
    child = BusyChild()
    code, aborted = app._wait_for_isolated_heavy_child(child, "shopping")
    assert (code, aborted) == (-15, True)
    assert child.terminated is True
    assert child.killed is False
    log = capsys.readouterr().out
    assert "G2B_CHILD_MEMORY_PROBE_DEGRADED OSError" in log
    assert "CGROUP_MEMORY_PROBE_UNAVAILABLE" in log
    assert "synthetic cgroup file unreadable" not in log


def test_transient_probe_failure_recovers_without_killing_healthy_child(monkeypatch):
    _low_memory_prod(monkeypatch, _budget(180))
    snapshot = app.memory_guard.container_budget_snapshot
    seen = [0]
    def intermittent():
        seen[0] += 1
        if seen[0] == 1:
            raise OSError("transient")
        return snapshot()
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", intermittent)
    child = BusyChild()
    first_wait = child.wait
    attempts = [0]
    def finish_after_samples(timeout=None):
        attempts[0] += 1
        if attempts[0] == 4:
            child.running = False
        return first_wait(timeout=timeout)
    child.wait = finish_after_samples
    code, aborted = app._wait_for_isolated_heavy_child(child, "shopping")
    assert (code, aborted) == (-15, False)
    assert child.terminated is False
    assert seen[0] >= 2
