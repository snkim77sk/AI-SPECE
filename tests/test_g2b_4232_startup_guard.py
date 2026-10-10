"""Source-free tests for the 4.1.232 256 MiB Cafe24 recovery guard."""
import safe_boot_vnext
import vnext_clean_app

def _state(limit=256, effective=95, **kwargs):
    state = {
        "guard_ok": True,
        "cgroup_oom_group": 0,
        "cgroup_limit_mib": limit,
        "cgroup_effective_mib": effective,
    }
    state.update(kwargs)
    return state

def test_boot_grace_is_nonblocking_and_bounded():
    fn = safe_boot_vnext.boot_grace_remaining
    assert fn(100, low_memory=True, now=100) == 120
    assert fn(100, low_memory=True, now=110) == 110
    assert fn(100, low_memory=True, now=220) == 0
    assert fn(100, low_memory=False, now=100) == 0
    assert fn(100, low_memory=True, test_mode=True, now=100) == 0

def test_256mib_realistic_web_budget_denies_extra_python_child():
    assert safe_boot_vnext.child_admission(_state()) == (False, "CGRP_CHILD_HEADROOM_UNSAFE")

def test_child_start_requires_future_headroom():
    assert safe_boot_vnext.child_admission(_state(effective=55))[0]
    assert not safe_boot_vnext.child_admission(_state(effective=80))[0]
    assert safe_boot_vnext.child_admission(_state(limit=320, effective=95))[0]

def test_unsafe_or_unknown_pressure_never_launches_new_child():
    assert not safe_boot_vnext.child_admission(_state(effective=0))[0]
    assert not safe_boot_vnext.child_admission(_state(guard_ok=False))[0]
    assert not safe_boot_vnext.child_admission(_state(cgroup_oom_group=1))[0]

def test_large_cgroup_keeps_existing_policy():
    assert safe_boot_vnext.child_admission(_state(limit=512, effective=175))[0]

def test_parent_worker_admission_is_fail_closed_on_256mib(monkeypatch):
    clean = vnext_clean_app
    monkeypatch.setattr(clean.memory_guard, "snapshot", lambda **_: _state())
    monkeypatch.setattr(clean.memory_guard, "low_memory_web_hold", lambda: True)
    assert clean._isolated_worker_admission_ok("shopping") is False
    assert clean._isolated_worker_admission_ok("budget") is False

def test_status_exposes_boot_grace(monkeypatch):
    clean = vnext_clean_app
    monkeypatch.setattr(clean, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(clean, "_automatic_boot_grace_remaining", lambda: 91)
    status = clean.recent_collection_status()
    assert status["auto_sync_recovery_grace_seconds"] == 91
    assert status["auto_sync_recovery_mode"] == "BOOT_GRACE"
