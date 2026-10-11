"""4.1.261: an already-running 512MiB source child must fail closed if cgroup telemetry disappears."""

import subprocess

import pytest

import vnext_clean_app as app


def _sample(*, limit=512, effective=260, threshold=460.8):
    return {
        "limit_mib": limit,
        "effective_mib": effective,
        "block_threshold_mib": threshold,
        "blocked": False,
        "oom_group": 0,
    }


class _BusySourceChild:
    pid = 4261

    def __init__(self):
        self.running = True
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self.running:
            assert timeout is not None, "supervisor must use bounded child waits"
            raise subprocess.TimeoutExpired("g2b_heavy_worker", timeout)
        return -15

    def poll(self):
        return None if self.running else -15

    def terminate(self):
        self.terminated = True
        self.running = False

    def kill(self):
        self.killed = True
        self.running = False


@pytest.mark.parametrize(
    "sample",
    [
        _sample(limit=0),
        _sample(effective=0),
        _sample(threshold=0),
        _sample(threshold=512),
    ],
)
def test_incomplete_running_child_cgroup_metrics_never_fail_open(sample):
    assert (
        app._isolated_worker_memory_abort_reason(sample)
        == "CGROUP_MEMORY_PROBE_UNAVAILABLE"
    )


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_512mib_child_preempted_when_monitor_loses_readings(monkeypatch, mode):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 92.0)
    monkeypatch.setattr(app.memory_guard, "soft_limit_mib", lambda: 160)

    # Launch-time admission succeeded and the first running sample is healthy.
    # The next sample is incomplete rather than raising an OSError: previous
    # logic silently treated this as safe and left the child unmonitored.
    samples = [_sample(), _sample(effective=0)]
    seen = []

    def cgroup_probe():
        seen.append(True)
        return samples.pop(0) if samples else _sample(effective=0)

    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", cgroup_probe)
    child = _BusySourceChild()
    code, preempted = app._wait_for_isolated_heavy_child(child, mode)

    assert (code, preempted) == (-15, True)
    assert len(seen) >= 2
    assert child.terminated is True
    assert child.killed is False


def test_256mib_normal_block_threshold_and_512mib_threshold_unchanged():
    assert app._isolated_worker_memory_abort_reason(
        _sample(limit=256, effective=223.9, threshold=224)
    ) == ""
    assert app._isolated_worker_memory_abort_reason(
        _sample(limit=256, effective=224, threshold=224)
    ) == "CGROUP_BLOCK"
    assert app._isolated_worker_memory_abort_reason(_sample()) == ""
    assert app._isolated_worker_memory_abort_reason(
        _sample(effective=460.8)
    ) == "CGROUP_BLOCK"
