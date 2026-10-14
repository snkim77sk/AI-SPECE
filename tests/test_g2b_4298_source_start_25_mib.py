"""G2B 4.1.298 — user-requested 25MiB startup reserve, 64MiB running guard.

Production cgroup-v2 256MiB only; never lower the 64MiB *in-flight*
reserve. This test has no real source API, Cafe24 DB, or data resets.
"""
import pytest

import memory_guard
import safe_boot_vnext as safe
import vnext_clean_app as app


def _state(current=114.6, effective=114.0, *, limit=256, source="cgroup-v2"):
    return {
        "guard_ok": True,
        "process_guard_ok": True,
        "rss_mib": 93.0,
        "soft_limit_mib": 160,
        "cgroup_source": source,
        "cgroup_limit_mib": limit,
        "cgroup_current_mib": current,
        "cgroup_effective_mib": effective,
        "cgroup_oom_group": 0,
    }


@pytest.mark.parametrize("source", ["shopping", "budget"])
def test_source_start_25_allows_screenshot_114mib_without_lowering_runtime_reserve(source):
    state = _state()
    assert safe.CHILD_HEADROOM_RESERVE_MIB == 64
    assert safe.SOURCE_START_RESERVE_256_MIB == 25
    assert safe.child_admission(state, worker_soft_limit_mib=112) == (
        False, "CGRP_CHILD_HEADROOM_UNSAFE"
    )
    assert safe.child_admission(
        state, worker_soft_limit_mib=112, source_mode=source,
        source_start_25_enabled=True,
    ) == (True, "CGRP_SOURCE_START_25MIB_OK")
    assert max(state["cgroup_current_mib"], state["cgroup_effective_mib"]) + 112 + 25 < 256


@pytest.mark.parametrize(("current", "effective", "expected"), [
    (119.0, 114.0, "CGRP_SOURCE_START_HEADROOM_UNSAFE"),
    (119.2, 114.0, "CGRP_SOURCE_START_HEADROOM_UNSAFE"),
    (114.0, 119.0, "CGRP_SOURCE_START_HEADROOM_UNSAFE"),
    (0, 114.0, "CGRP_SOURCE_START_PROOF_UNAVAILABLE"),
    (float("nan"), 114.0, "CGRP_SOURCE_START_PROOF_UNAVAILABLE"),
])
def test_source_start_fail_closed_if_raw_current_unsafe(current, effective, expected):
    assert safe.child_admission(
        _state(current, effective), worker_soft_limit_mib=112,
        source_mode="budget", source_start_25_enabled=True,
    ) == (False, expected)


@pytest.mark.parametrize("source,soft,reason", [
    ("match", 112, "CGRP_CHILD_HEADROOM_UNSAFE"),
    ("budget", 160, "CGRP_SOURCE_START_SOFT_LIMIT_UNSAFE"),
    ("shopping", 113, "CGRP_SOURCE_START_SOFT_LIMIT_UNSAFE"),
])
def test_source_start_never_relaxes_match_or_oversized_worker(source, soft, reason):
    assert safe.child_admission(
        _state(), worker_soft_limit_mib=soft, source_mode=source,
        source_start_25_enabled=True,
    ) == (False, reason)


def test_source_start_25_uses_only_real_cgroup_v2_and_web_rss():
    bad = _state(source="cgroup-v1")
    assert safe.child_admission(
        bad, source_mode="shopping", source_start_25_enabled=True,
    ) == (False, "CGRP_SOURCE_START_PROOF_UNAVAILABLE")
    bad = _state()
    bad["rss_mib"] = 0
    assert safe.child_admission(
        bad, source_mode="shopping", source_start_25_enabled=True,
    ) == (False, "CGRP_SOURCE_START_PROOF_UNAVAILABLE")
    large = _state(current=115, effective=114, limit=512)
    assert safe.child_admission(
        large, source_mode="shopping", source_start_25_enabled=True,
    ) == (True, "LARGE_CGROUP_CHILD_HEADROOM_OK")


def test_parent_defaults_25_for_source_but_rollback_64_available(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 0)
    monkeypatch.delenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", raising=False)
    monkeypatch.delenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", raising=False)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kw: _state())
    assert app._isolated_worker_admission_ok("shopping") is True
    result = app._worker_capacity_details(_state())
    assert result["memory_worker_admission_allowed"] is True
    assert result["memory_worker_source_start_25_enabled"] is True
    assert result["memory_worker_expected_mib"] == 112
    assert result["memory_worker_reserve_mib"] == 25
    assert result["memory_worker_runtime_reserve_mib"] == 64
    assert result["memory_worker_max_prelaunch_mib_exclusive"] == 119
    assert result["memory_worker_prelaunch_accounted_mib"] == 114.6
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    assert app._isolated_worker_admission_ok("shopping") is False
    assert app._worker_capacity_details(_state())["memory_worker_reserve_mib"] == 64


def test_early_parent_abort_at_192_keeps_64web_even_start_25(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "0")
    monkeypatch.delenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", raising=False)
    def _budget(current):
        return {
            "limit_mib": 256, "current_mib": current,
            "effective_mib": current-0.5, "block_threshold_mib": 224,
            "blocked": False, "oom_group": 0,
        }
    assert app._isolated_worker_memory_abort_reason(
        _budget(191.9), mode="shopping"
    ) == ""
    assert app._isolated_worker_memory_abort_reason(
        _budget(192), mode="shopping"
    ) == "CGROUP_SOURCE_WEB_RESERVE_HOLD"
    assert app._isolated_worker_memory_abort_reason(_budget(193)) == ""
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    assert app._isolated_worker_memory_abort_reason(
        _budget(193), mode="shopping"
    ) == ""


def test_child_source_only_checkpoint_enforces_same_64_web_reserve(monkeypatch):
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "0")
    monkeypatch.delenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", raising=False)
    state = _state(current=192, effective=190)
    state["cgroup_source"] = "cgroup-v2"
    with pytest.raises(memory_guard.MemoryPressureError, match="SOURCE_CGROUP_WEB_RESERVE_HOLD"):
        memory_guard._enforce_source_incremental_web_reserve(state)
    state["cgroup_current_mib"] = 191.9
    assert memory_guard._enforce_source_incremental_web_reserve(state) is None


def test_boot_grace_still_applies_25_entry(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.delenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", raising=False)
    monkeypatch.delenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", raising=False)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kwargs: _state())
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 120)
    assert app._isolated_worker_admission_ok("budget") is False
    state = app._worker_capacity_details(_state())
    assert state["memory_worker_boot_grace_seconds"] == 120
    assert state["memory_worker_admission_allowed"] is False


def test_existing_76_plus_64_canary_precedes_25_entry(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "1")
    monkeypatch.delenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", raising=False)
    result=app._worker_capacity_details(_state(current=114.6))
    assert result["memory_worker_source_start_25_enabled"] is False
    assert result["memory_worker_reserve_mib"]==64
    assert result["memory_worker_expected_mib"]==76
