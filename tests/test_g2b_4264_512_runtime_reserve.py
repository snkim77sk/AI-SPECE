"""G2B 4.1.264: preserve the existing 64MiB child reserve while running at 512MiB.

Memory samples are synthetic; the subprocess termination test uses an actual
disposable OS child but does not touch production PostgreSQL/source endpoints.
"""

import subprocess
import sys

import pytest

import memory_guard
import safe_boot_vnext
import vnext_clean_app as app

MIB = 1024 * 1024


def _state(limit, effective, block):
    return {
        "limit_mib": limit,
        "effective_mib": effective,
        "block_threshold_mib": block,
        "blocked": effective >= block,
        "oom_group": 0,
    }


@pytest.mark.parametrize("limit,effective,threshold,expected", [
    (256, 223.99, 224.0, ""),
    (256, 224.0, 224.0, "CGROUP_BLOCK"),
    (512, 447.99, 460.8, ""),
    (512, 448.0, 460.8, "CGROUP_CHILD_RESERVE_HOLD"),
    (512, 452.0, 460.8, "CGROUP_CHILD_RESERVE_HOLD"),
    (512, 460.8, 460.8, "CGROUP_BLOCK"),
    (1024, 921.5, 921.6, ""),
    (1024, 921.6, 921.6, "CGROUP_BLOCK"),
])
def test_parent_retains_512mib_running_reserve_without_256mib_regression(
    limit, effective, threshold, expected
):
    assert (
        app._isolated_worker_memory_abort_reason(_state(limit, effective, threshold))
        == expected
    )


def test_real_memory_guard_512mib_block_threshold_has_less_than_64mib_reserved(monkeypatch):
    limit = 512 * MIB
    effective = 449 * MIB
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: (
            "cgroup-v2", effective, limit, effective,
            {"anon": effective}, {"oom": 0, "oom_kill": 0},
        ),
    )
    monkeypatch.setattr(memory_guard, "_read_text", lambda _path: "0")
    snapshot = memory_guard.container_budget_snapshot()
    assert snapshot["limit_mib"] == 512
    assert snapshot["effective_mib"] == 449
    assert snapshot["block_threshold_mib"] == 460.8
    assert snapshot["blocked"] is False
    assert 512 - snapshot["block_threshold_mib"] < 64
    assert (
        app._isolated_worker_memory_abort_reason(snapshot)
        == "CGROUP_CHILD_RESERVE_HOLD"
    )


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_supervisor_preempts_only_real_os_child_before_512mib_block(monkeypatch, mode):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(
        app.memory_guard, "container_budget_snapshot",
        lambda: _state(512, 449, 460.8),
    )
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 91.0)
    monkeypatch.setattr(app.memory_guard, "soft_limit_mib", lambda: 160)
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", "import time; time.sleep(12)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        code, preempted = app._wait_for_isolated_heavy_child(child, mode)
        assert code == -15
        assert preempted is True
        assert child.poll() == -15
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)


def test_unchanged_worker_forecast_and_64mib_reserve_at_launch():
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64
    snapshot = {
        "guard_ok": True,
        "cgroup_oom_group": 0,
        "cgroup_limit_mib": 512,
        "cgroup_effective_mib": 335.99,
    }
    assert safe_boot_vnext.child_admission(
        snapshot, worker_soft_limit_mib=112
    )[0] is True
    snapshot["cgroup_effective_mib"] = 336
    assert safe_boot_vnext.child_admission(
        snapshot, worker_soft_limit_mib=112
    )[0] is False
