"""G2B V4.1.301: explain raw-vs-effective source admission and attempt
bounded idle-only cgroup-v2 file-cache reclaim without relaxing source guards.

Synthetic only: never touches the real cgroup control file or live PG/API.
"""
import time

import g2b_idle_memory_recovery as rec
import safe_boot_vnext as safe
import vnext_clean_app as app


MIB = 1 << 20


def _state(*, raw=169.7, effective=115.7):
    return {
        "guard_ok": True,
        "process_guard_ok": True,
        "guard_state": "SAFE",
        "rss_mib": 93.0,
        "soft_limit_mib": 160.0,
        "cgroup_source": "cgroup-v2",
        "cgroup_limit_mib": 256.0,
        "cgroup_current_mib": raw,
        "cgroup_effective_mib": effective,
        "cgroup_oom_group": 0,
    }


def _idle(monkeypatch):
    now = time.monotonic()
    monkeypatch.setattr(rec, "_START_MONOTONIC", now - 100)
    monkeypatch.setattr(rec, "_LAST_ACTIVITY", now - 200)
    monkeypatch.setattr(rec, "_LAST_ATTEMPT", 0.0)
    monkeypatch.setattr(rec, "_ATTEMPTS", 0)
    monkeypatch.setattr(rec, "_ACTIVE_REQUESTS", 0)
    monkeypatch.setattr(rec, "_LAST_RESULT", {})
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "25")
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "0")
    monkeypatch.setenv("G2B_IDLE_CGROUP_FILE_RECLAIM", "1")


def _profile():
    return {
        "source": "cgroup-v2",
        "limit_mib": 256.0,
        "current_mib": 169.7,
        "effective_mib": 115.7,
        "file_bytes": 110 * MIB,
        "inactive_file_bytes": 78 * MIB,
    }


def _callbacks():
    return {"workers_busy": lambda: False, "backend_ready": lambda: True}


def test_real_raw_baseline_explains_50_7_mib_shortage(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 0)
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "25")
    monkeypatch.setenv("G2B_256MIB_SOURCE_INCREMENTAL_CANARY", "0")
    state = _state()
    assert safe.child_admission(
        state, source_mode="shopping", source_start_25_enabled=True
    ) == (False, "CGRP_SOURCE_START_HEADROOM_UNSAFE")
    detail = app._worker_capacity_details(state)
    assert detail["memory_worker_max_prelaunch_mib_exclusive"] == 119.0
    assert detail["memory_worker_prelaunch_accounted_mib"] == 169.7
    assert detail["memory_worker_headroom_shortfall_mib"] == 50.7
    html = app._collection_worker_capacity_notice({
        **detail,
        "shopping_run_state": "WAITING_MEMORY",
        "memory_cgroup_current_mib": 169.7,
        "memory_cgroup_effective_mib": 115.7,
        "memory_cgroup_limit_mib": 256.0,
    })
    assert "memory.current) 169.7MiB" in html
    assert "실효 사용량 115.7MiB" in html
    assert "실제 시작 판정 사용량 169.7MiB" in html
    assert "부족량은 약 50.7MiB" in html


def test_idle_file_cache_reclaim_then_source_gate_must_resample(monkeypatch):
    _idle(monkeypatch)
    samples = [_state(), _state(), _state(raw=116.5, effective=110.0)]
    monkeypatch.setattr(rec.memory_guard, "snapshot", lambda **kw: samples.pop(0))
    monkeypatch.setattr(rec.memory_guard, "container_budget_snapshot", _profile)
    written = []
    monkeypatch.setattr(rec, "_write_cgroup_file_reclaim", written.append)
    monkeypatch.setattr(rec.gc, "collect", lambda: 0)
    monkeypatch.setattr(rec, "_trim_allocator", lambda: True)
    result = rec.run_recovery_cycle(**_callbacks())
    assert result["status"] == "completed"
    assert result["reason"] == "source-start-raw-headroom-held"
    assert 50 * MIB <= written[0] <= 64 * MIB
    assert len(written) == 1
    assert result["cgroup_file_reclaim"]["status"] == "completed"
    assert result["raw_cgroup_reclaimed_mib"] > 50
    assert safe.child_admission(
        samples[0] if samples else _state(raw=116.5, effective=110.0),
        source_mode="shopping", source_start_25_enabled=True
    )[0] is True
    assert safe.CHILD_HEADROOM_RESERVE_MIB == 64


def test_active_request_or_source_never_triggers_idle_kernel_reclaim(monkeypatch):
    _idle(monkeypatch)
    monkeypatch.setattr(rec.memory_guard, "snapshot", lambda **kw: _state())
    monkeypatch.setattr(rec, "_write_cgroup_file_reclaim", lambda *_:
                        (_ for _ in ()).throw(AssertionError("kernel reclaim during visitor")))
    rec.mark_request_start()
    assert rec.run_recovery_cycle(**_callbacks())["reason"] == "requests-active"
    rec.mark_request_end()
    _idle(monkeypatch)
    assert rec.run_recovery_cycle(
        workers_busy=lambda: True, backend_ready=lambda: True
    )["reason"] == "worker-or-supervisor-active"
    assert rec.recovery_status()["attempts"] == 0


def test_readonly_cgroup_reclaim_is_safe_hold_without_fallback(monkeypatch):
    _idle(monkeypatch)
    samples = [_state(), _state(), _state()]
    monkeypatch.setattr(rec.memory_guard, "snapshot", lambda **kw: samples.pop(0))
    monkeypatch.setattr(rec.memory_guard, "container_budget_snapshot", _profile)
    monkeypatch.setattr(rec, "_write_cgroup_file_reclaim", lambda *_:
                        (_ for _ in ()).throw(PermissionError("readonly")))
    monkeypatch.setattr(rec.gc, "collect", lambda: 0)
    monkeypatch.setattr(rec, "_trim_allocator", lambda: False)
    result = rec.run_recovery_cycle(**_callbacks())
    assert result["cgroup_file_reclaim"]["status"] == "unavailable"
    assert result["cgroup_file_reclaim"]["error_code"] == "PermissionError"
    assert result["raw_cgroup_reclaimed_mib"] == 0
    assert result["status"] == "completed"


def test_no_cache_reclaim_when_disabled_or_unsafe_tier(monkeypatch):
    _idle(monkeypatch)
    monkeypatch.setattr(rec, "_write_cgroup_file_reclaim", lambda *_:
                        (_ for _ in ()).throw(AssertionError("must be disabled")))
    monkeypatch.setenv("G2B_IDLE_CGROUP_FILE_RECLAIM", "0")
    detail, _ = rec._idle_cgroup_cache_reclaim(_state())
    assert detail["status"] == "disabled"
    monkeypatch.setenv("G2B_IDLE_CGROUP_FILE_RECLAIM", "1")
    detail, _ = rec._idle_cgroup_cache_reclaim({
        **_state(), "cgroup_limit_mib": 512.0,
    })
    assert detail["status"] == "not-eligible"
