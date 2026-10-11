"""4.1.278: cgroup monitoring must continue during unreaped-worker backoff.

Pure in-memory, virtual-time unit tests. No production PG, external API,
credentials, filesystem modification or actual child process termination.
"""
import threading

import pytest

import vnext_clean_app as app


def _snapshot(*, limit=512, effective=110, block=460.8, oom=0):
    return {
        "limit_mib": limit,
        "effective_mib": effective,
        "block_threshold_mib": block,
        "blocked": effective >= block,
        "oom_group": oom,
    }


def _prepare_memory(monkeypatch, snapshot):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    samples = []
    def collect():
        samples.append(True)
        return snapshot() if callable(snapshot) else snapshot
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", collect)
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 91.0)
    monkeypatch.setattr(app.memory_guard, "soft_limit_mib", lambda: 160.0)
    sleeps = []
    monkeypatch.setattr(app.time, "sleep", lambda sec: sleeps.append(sec))
    return samples, sleeps


@pytest.mark.parametrize("sample", [
    _snapshot(limit=256, effective=223.9, block=224),
    _snapshot(limit=512, effective=447.9, block=460.8),
    _snapshot(limit=1024, effective=900, block=921.6),
])
def test_healthy_reap_backoff_samples_every_one_second(monkeypatch, sample):
    checks, sleeps = _prepare_memory(monkeypatch, sample)
    assert app._watch_unreaped_child_backoff_memory(30.0) == ""
    assert sleeps == [1.0] * 30
    assert len(checks) == 31
    assert app.safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64.0


@pytest.mark.parametrize("sample,expected", [
    (_snapshot(limit=256, effective=224.0, block=224), "CGROUP_BLOCK"),
    (_snapshot(limit=512, effective=448.0, block=460.8), "CGROUP_CHILD_RESERVE_HOLD"),
    (_snapshot(limit=512, effective=110, block=460.8, oom=1), "CGROUP_OOM_GROUP"),
    (_snapshot(limit=512, effective=0, block=460.8),
     "CGROUP_MEMORY_PROBE_UNAVAILABLE"),
])
def test_unchanged_parent_memory_threshold_triggers_urgent_reap(monkeypatch, sample, expected):
    checks, sleeps = _prepare_memory(monkeypatch, sample)
    assert app._watch_unreaped_child_backoff_memory(30.0) == expected
    assert len(checks) == 1
    assert sleeps == []


def test_pressure_crossing_threshold_during_backoff_is_seen_within_one_second(monkeypatch):
    snapshots = [
        _snapshot(effective=110),
        _snapshot(effective=447),
        _snapshot(effective=449),
    ]
    def collect():
        return snapshots.pop(0)
    checks, sleeps = _prepare_memory(monkeypatch, collect)
    assert (
        app._watch_unreaped_child_backoff_memory(30)
        == "CGROUP_CHILD_RESERVE_HOLD"
    )
    assert len(checks) == 3
    assert sleeps == [1.0, 1.0]


def test_web_rss_pressure_is_monitored_while_child_unreaped(monkeypatch):
    checks, sleeps = _prepare_memory(monkeypatch, _snapshot())
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 161.0)
    assert app._watch_unreaped_child_backoff_memory(30) == "WEB_RSS_HOLD"
    assert len(checks) == 1
    assert sleeps == []


def test_three_consecutive_cgroup_probe_exceptions_fail_closed(monkeypatch):
    checks, sleeps = _prepare_memory(monkeypatch, None)
    def fail():
        checks.append(True)
        raise OSError("sensitive kernel path")
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", fail)
    assert (
        app._watch_unreaped_child_backoff_memory(30)
        == "CGROUP_MEMORY_PROBE_UNAVAILABLE"
    )
    assert len(checks) == 3
    assert sleeps == [1.0, 1.0]


def test_urgent_reap_already_used_keeps_cgroup_watch_but_waits_full_backoff(monkeypatch):
    checks, sleeps = _prepare_memory(
        monkeypatch, _snapshot(effective=449),
    )
    assert (
        app._watch_unreaped_child_backoff_memory(
            30, allow_emergency=False,
        ) == ""
    )
    assert sleeps == [1.0] * 30
    assert len(checks) == 31


class SourceChild:
    pid = 427800

    def poll(self):
        return None


def _setup_supervisor(monkeypatch):
    child = SourceChild()
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", child)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_LAST", {"kind": "", "exit_code": None})
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 0, "budget": 0,
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_TERMINAL_REVISION", 0)
    monkeypatch.setattr(app, "_RECENT_COLLECTION_WAKE", threading.Event())
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "state": "RUNNING",
        "last_error": "",
        "last_status": "RUNNING",
        "shopping_run_state": "RUNNING",
        "shopping_last_error": "",
        "budget_run_state": "IDLE",
        "budget_last_error": "",
    })
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("emergency watch must not query PostgreSQL")
        ),
    )
    return child


def test_one_urgent_escalation_can_bypass_30s_cooldown_but_cannot_storm(monkeypatch, capsys):
    child = _setup_supervisor(monkeypatch)
    checks, sleeps = _prepare_memory(monkeypatch, _snapshot(effective=449))
    clock = [100.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    def sleep(sec):
        sleeps.append(sec)
        clock[0] += sec
    monkeypatch.setattr(app.time, "sleep", sleep)
    signals = []
    rereads = []

    def full_reap(proc, mode):
        assert proc is child and mode == "shopping"
        signals.append(clock[0])
        if len(signals) <= 2:
            raise RuntimeError("G2B_ISOLATED_CHILD_REAP_UNCONFIRMED")
        raise AssertionError("repeated emergency TERM/KILL beyond single bypass")

    def recheck(proc, mode):
        assert proc is child and mode == "shopping"
        rereads.append(clock[0])
        return 75, True

    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", full_reap)
    monkeypatch.setattr(app, "_recheck_unconfirmed_isolated_child", recheck)
    app._isolated_heavy_supervisor_worker()

    assert len(signals) == 2
    assert signals == [100.0, 100.0]
    assert rereads == [101.0]
    assert checks, "cgroup monitoring was skipped"
    assert sleeps == [1.0]
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_TERMINAL_REVISION == 1
    assert "G2B_ISOLATED_REAP_BACKOFF_MEMORY_HOLD" in capsys.readouterr().out


def test_clean_test_mode_retains_legacy_delay_policy(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(
        app.memory_guard, "container_budget_snapshot",
        lambda: (_ for _ in ()).throw(
            AssertionError("isolated test mode must not inspect real cgroup")
        ),
    )
    sleeps = []
    monkeypatch.setattr(app.time, "sleep", lambda sec: sleeps.append(sec))
    assert app._watch_unreaped_child_backoff_memory(30.0) == ""
    assert sleeps == [30.0]
