"""V4.1.297: opt-in incremental cgroup admission never weakens legacy guards.

Unit tests do NOT use real source keys, production PG, subprocesses or a live
Cafe24 service. The real cgroup canary verifies HTTP /health separately.
"""
import os

import pytest

import memory_guard
import safe_boot_vnext as boot
import vnext_clean_app as app


def _source_state(*, current=113.7, effective=113.3, limit=256.0,
                  source="cgroup-v2", process_ok=True, guard_ok=True):
    return {
        "guard_ok": guard_ok,
        "process_guard_ok": process_ok,
        "rss_mib": 93.0,
        "soft_limit_mib": 160.0,
        "cgroup_source": source,
        "cgroup_limit_mib": limit,
        "cgroup_current_mib": current,
        "cgroup_effective_mib": effective,
        "cgroup_wait_threshold_mib": 208,
        "cgroup_block_threshold_mib": 224,
        "cgroup_oom_group": 0,
    }


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_4297_exact_256_optin_has_separate_increment_and_original_64_reserve(mode):
    state = _source_state()
    # Default caller remains the original 112+64 forecast / refusal.
    assert boot.child_admission(state, worker_soft_limit_mib=112) == (
        False, "CGRP_CHILD_HEADROOM_UNSAFE"
    )
    assert boot.source_incremental_forecast(
        state, mode=mode, enabled=True
    ) == 76.0
    assert boot.CHILD_MIN_SOFT_MIB == 96
    assert boot.CHILD_HEADROOM_RESERVE_MIB == 64
    assert boot.child_admission(
        state, worker_soft_limit_mib=112,
        source_mode=mode, incremental_source_enabled=True,
    ) == (True, "CGRP_SOURCE_INCREMENTAL_HEADROOM_OK")
    assert max(state["cgroup_current_mib"], state["cgroup_effective_mib"]) + 76 + 64 < 256


@pytest.mark.parametrize(("current", "effective", "reason"), [
    (116.0, 113.3, "CGRP_SOURCE_INCREMENTAL_HEADROOM_UNSAFE"),
    (116.1, 113.3, "CGRP_SOURCE_INCREMENTAL_HEADROOM_UNSAFE"),
    (113.7, 116.0, "CGRP_SOURCE_INCREMENTAL_HEADROOM_UNSAFE"),
    (240.0, 114.0, "CGRP_SOURCE_INCREMENTAL_HEADROOM_UNSAFE"),
    (0, 113.3, "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE"),
    (float("nan"), 113.3, "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE"),
])
def test_4297_lowtier_rejects_unsound_raw_or_effective_pressure(current, effective, reason):
    allowed, actual = boot.child_admission(
        _source_state(current=current, effective=effective),
        worker_soft_limit_mib=112, source_mode="budget",
        incremental_source_enabled=True,
    )
    assert (allowed, actual) == (False, reason)


@pytest.mark.parametrize("source,process_ok", [
    ("", True), ("cgroup-v1", True), ("cgroup-v2", False),
])
def test_4297_lowtier_refuses_missing_real_cgroup_proof(source, process_ok):
    assert boot.child_admission(
        _source_state(source=source, process_ok=process_ok),
        source_mode="shopping", incremental_source_enabled=True,
    ) == (False, "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE")


def test_4297_non_source_modes_and_512mib_keep_exact_legacy_math():
    low = _source_state()
    assert boot.source_incremental_forecast(low, mode="match", enabled=True) is None
    assert boot.child_admission(
        low, source_mode="match", incremental_source_enabled=True,
    ) == (False, "CGRP_CHILD_HEADROOM_UNSAFE")

    large = _source_state(current=220, effective=219, limit=512)
    assert boot.source_incremental_forecast(
        large, mode="budget", enabled=True,
    ) is None
    assert boot.child_admission(
        large, worker_soft_limit_mib=112,
        source_mode="budget", incremental_source_enabled=True,
    ) == (True, "LARGE_CGROUP_CHILD_HEADROOM_OK")


def test_4297_experimental_web_parent_only_and_boot_grace(monkeypatch, capsys):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    state = _source_state()
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kwargs: dict(state))
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 0)
    assert app._isolated_worker_admission_ok("shopping") is True
    assert app._isolated_worker_admission_ok("budget") is True
    assert app._isolated_worker_admission_ok("match") is False
    details = app._worker_capacity_details(state)
    assert details["memory_worker_admission_allowed"] is True
    assert details["memory_worker_expected_mib"] == 76.0
    assert details["memory_worker_process_soft_mib"] == 112.0
    assert details["memory_worker_forecast_basis"] == "CGROUP_INCREMENTAL_CANARY"
    assert details["memory_worker_reserve_mib"] == 64.0
    assert details["memory_worker_max_prelaunch_mib_exclusive"] == 116.0
    assert details["memory_worker_prelaunch_accounted_mib"] == 113.7

    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 60)
    assert app._isolated_worker_admission_ok("budget") is False
    assert "G2B_SOURCE_INCREMENTAL_BOOT_GRACE_HOLD" in capsys.readouterr().out

    monkeypatch.delenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY")
    # Explicitly opt out of the V4.1.298 25MiB source-only start for this
    # legacy compatibility assertion, preserving its original 112+64 path.
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    assert app._isolated_worker_admission_ok("budget") is False
    assert app._worker_capacity_details(state)["memory_worker_expected_mib"] == 112.0


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_4297_parent_preempts_source_at_real_64mib_reserve(monkeypatch, mode):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    baseline = {
        "limit_mib": 256.0, "current_mib": 191.99,
        "effective_mib": 155, "block_threshold_mib": 224,
        "blocked": False, "oom_group": 0,
    }
    assert app._isolated_worker_memory_abort_reason(
        baseline, mode=mode
    ) == ""
    baseline["current_mib"] = 192.0
    assert app._isolated_worker_memory_abort_reason(
        baseline, mode=mode
    ) == "CGROUP_SOURCE_WEB_RESERVE_HOLD"
    baseline["current_mib"] = 191.0
    baseline["effective_mib"] = 192.0
    assert app._isolated_worker_memory_abort_reason(
        baseline, mode=mode
    ) == "CGROUP_SOURCE_WEB_RESERVE_HOLD"
    baseline["current_mib"] = 0
    assert app._isolated_worker_memory_abort_reason(
        baseline, mode=mode
    ) == "CGROUP_MEMORY_PROBE_UNAVAILABLE"
    # Match and the unmodified legacy direct call still use existing 224MiB.
    baseline["current_mib"] = 193.0
    baseline["effective_mib"] = 193.0
    assert app._isolated_worker_memory_abort_reason(baseline) == ""
    assert app._isolated_worker_memory_abort_reason(
        baseline, mode="match"
    ) == ""


def test_4297_disposable_source_worker_guard_keeps_64mb_in_flight(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    proof = _source_state(current=191.99, effective=187)
    assert memory_guard._enforce_source_incremental_web_reserve(proof) is None
    proof["cgroup_current_mib"] = 192.0
    with pytest.raises(memory_guard.MemoryPressureError,
                       match="SOURCE_CGROUP_WEB_RESERVE_HOLD"):
        memory_guard._enforce_source_incremental_web_reserve(proof)
    proof["cgroup_current_mib"] = 180.0
    proof["cgroup_source"] = ""
    with pytest.raises(memory_guard.MemoryPressureError,
                       match="SOURCE_CGROUP_RESERVE_PROBE_UNAVAILABLE"):
        memory_guard._enforce_source_incremental_web_reserve(proof)

    # Guard is still enabled inside the *existing* per-page checkpoint loop,
    # not just in a standalone function that never runs.
    proof = _source_state(current=192.0, effective=181)
    monkeypatch.setattr(memory_guard, "snapshot", lambda **kwargs: proof)
    with pytest.raises(memory_guard.MemoryPressureError,
                       match="SOURCE_CGROUP_WEB_RESERVE_HOLD"):
        memory_guard.wait_for_heavy_work_budget(timeout=0)


def test_4297_test_and_source_optout_never_add_extra_guard(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    memory_guard._enforce_source_incremental_web_reserve(
        _source_state(current=230, effective=224)
    )
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "0")
    memory_guard._enforce_source_incremental_web_reserve(
        _source_state(current=230, effective=224)
    )


def test_4297_diagnostic_shows_legacy_and_experimental_forecast_separately(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    bad = _source_state(current=117, effective=113.3)
    state = app._worker_capacity_details(bad)
    assert not state["memory_worker_admission_allowed"]
    assert state["memory_worker_admission_reason"] == "CGRP_SOURCE_INCREMENTAL_HEADROOM_UNSAFE"
    assert state["memory_worker_process_soft_mib"] == 112.0
    assert state["memory_worker_expected_mib"] == 76.0
    assert state["memory_worker_headroom_shortfall_mib"] == 1.0


def test_4297_real_parent_wait_terminates_source_at_192_before_legacy_block(monkeypatch):
    import subprocess

    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    sample = {
        "limit_mib": 256.0, "current_mib": 192.0,
        "effective_mib": 160.0, "block_threshold_mib": 224.0,
        "blocked": False, "oom_group": 0,
    }
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", lambda: dict(sample))
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 91.0)
    monkeypatch.setattr(app.memory_guard, "soft_limit_mib", lambda: 160.0)

    class Child:
        terminated = False
        def wait(self, timeout=None):
            if not self.terminated:
                raise subprocess.TimeoutExpired("source_worker", timeout)
            return -15
        def poll(self):
            return -15 if self.terminated else None
        def terminate(self):
            self.terminated = True

    child = Child()
    code, aborted = app._wait_for_isolated_heavy_child(child, "budget")
    assert code == -15
    assert aborted is True
    assert child.terminated is True


def test_4297_unreaped_source_reserve_guard_is_urgent(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot",
                        lambda: {
                            "limit_mib": 256, "current_mib": 192.5,
                            "effective_mib": 158, "block_threshold_mib": 224,
                            "blocked": False, "oom_group": 0,
                        })
    monkeypatch.setattr(app.memory_guard, "current_rss_mib", lambda: 91)
    monkeypatch.setattr(app.memory_guard, "soft_limit_mib", lambda: 160)
    monkeypatch.setattr(app.time, "sleep",
                        lambda sec: (_ for _ in ()).throw(
                            AssertionError("urgent low-tier source must not delay")
                        ))
    assert app._watch_unreaped_child_backoff_memory(
        30.0, mode="shopping"
    ) == "CGROUP_SOURCE_WEB_RESERVE_HOLD"


def test_4297_disposable_cgroup_tests_do_not_modify_operational_api_or_pg():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    source = (root / "scripts/g2b_256mib_cgroup_canary.sh").read_text()
    stress = (root / "scripts/g2b_256mib_source_reserve_stress_ci.py").read_text()
    workflow = (root / ".github/workflows/g2b-vnext.yml").read_text()
    assert "G2B_256MIB_SOURCE_INCREMENTAL_CANARY=0" in source
    assert "G2B_256MIB_SOURCE_START_RESERVE_MIB=25" in source
    assert "G2B_4298_REAL_WEB_PARENT_SOURCE_START_25MIB_OK" in source
    assert "G2B_4297_REAL_256MIB_INFLIGHT_64MIB_RESERVE_GUARD_OK" in stress
    assert "G2B_4297_CGROUP_OOM_KILL_INCREASED" in source
    assert "G2B_4297_EXTERNAL_SOURCE_API_CALLS" not in stress
    assert "G2B_4297_INCREMENTAL_SOURCE_CANARY_WEB_LIVE_AND_OOM_ZERO_OK" in source
    assert "startsWith(github.head_ref, 'fix/g2b-4.1.298-')" in workflow
    assert "https://apis.data.go.kr" not in stress
    assert "DROP TABLE" not in stress


def test_4297_worker_understands_boolean_operator_flag_variants(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "TRUE")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    with pytest.raises(memory_guard.MemoryPressureError,
                       match="SOURCE_CGROUP_WEB_RESERVE_HOLD"):
        memory_guard._enforce_source_incremental_web_reserve(
            _source_state(current=192, effective=155)
        )


def test_4297_collection_monitor_notice_is_honest_about_cgroup_increment():
    base = {
        "shopping_run_state": "WAITING_MEMORY",
        "memory_worker_admission_reason": "CGRP_SOURCE_INCREMENTAL_HEADROOM_UNSAFE",
        "memory_worker_expected_mib": 76,
        "memory_worker_reserve_mib": 64,
        "memory_cgroup_effective_mib": 113.3,
        "memory_cgroup_limit_mib": 256,
        "memory_worker_max_prelaunch_mib_exclusive": 116,
        "memory_worker_headroom_shortfall_mib": 1,
    }
    notice = app._collection_worker_capacity_notice(base)
    assert "cgroup 추가 점유" in notice
    assert "512MiB 이상으로 상향" not in notice
    base["memory_worker_admission_reason"] = "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE"
    assert "memory.current" in app._collection_worker_capacity_notice(base)
    base["memory_worker_admission_reason"] = "CGRP_SOURCE_INCREMENTAL_BOOT_GRACE_HOLD"
    assert "300초" in app._collection_worker_capacity_notice(base)


@pytest.mark.parametrize("field", ["rss_mib", "soft_limit_mib"])
def test_4297_web_missing_real_process_rss_is_never_source_admission_proof(field):
    state = _source_state()
    state[field] = 0
    assert boot.child_admission(
        state, source_mode="shopping", incremental_source_enabled=True,
    ) == (False, "CGRP_SOURCE_INCREMENTAL_PROOF_UNAVAILABLE")


def test_4297_raised_worker_soft_limit_is_not_covered_by_76mib_canary():
    assert boot.child_admission(
        _source_state(), worker_soft_limit_mib=160,
        source_mode="budget", incremental_source_enabled=True,
    ) == (False, "CGRP_SOURCE_INCREMENTAL_SOFT_LIMIT_UNSAFE")


def test_4297_source_worker_without_rss_rejects_every_page(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    state = _source_state(current=150, effective=149)
    state["rss_mib"] = 0
    with pytest.raises(memory_guard.MemoryPressureError,
                       match="SOURCE_CGROUP_RESERVE_PROBE_UNAVAILABLE"):
        memory_guard._enforce_source_incremental_web_reserve(state)


def test_4297_flag_does_not_change_320mib_source_only_worker_guard(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    # 320MiB source-only worker is legacy-admitted and must retain its old
    # active memory policy, not get a false 256MiB canary-probe failure.
    state = _source_state(current=193, effective=190, limit=320)
    assert memory_guard._enforce_source_incremental_web_reserve(state) is None
