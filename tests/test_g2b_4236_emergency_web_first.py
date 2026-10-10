"""4.1.236 web-first emergency: never fork an unsafe worker in 256 MiB."""

import inspect

import safe_boot_vnext
import vnext_clean_app


def _memory(*, limit=256, effective=95, guard_ok=True, oom_group=0):
    return {
        "guard_ok": guard_ok,
        "rss_mib": 94.0,
        "cgroup_limit_mib": limit,
        "cgroup_effective_mib": effective,
        "cgroup_oom_group": oom_group,
    }


def test_small_cgroup_automatic_collection_has_a_five_minute_grace():
    assert safe_boot_vnext.BOOT_GRACE_SECONDS == 300
    delay = safe_boot_vnext.boot_grace_remaining
    assert delay(100, low_memory=True, now=100) == 300
    assert delay(100, low_memory=True, now=299) == 101
    assert delay(100, low_memory=True, now=400) == 0
    assert delay(100, low_memory=False, now=100) == 0
    assert delay(100, low_memory=True, test_mode=True, now=100) == 0


def test_web_256mib_denies_child_forecast_before_fork():
    assert safe_boot_vnext.child_admission(_memory()) == (
        False, "CGRP_CHILD_HEADROOM_UNSAFE"
    )
    assert safe_boot_vnext.child_admission(
        _memory(effective=81)
    )[0] is False
    assert safe_boot_vnext.child_admission(
        _memory(effective=55)
    )[0] is True


def test_unknown_cgroup_limit_must_not_fail_open():
    assert safe_boot_vnext.child_admission(_memory(limit=0)) == (
        False, "CGROUP_LIMIT_UNAVAILABLE"
    )


def test_512mib_tier_retains_collection_when_memory_guard_is_safe():
    assert safe_boot_vnext.child_admission(
        _memory(limit=512, effective=120)
    )[0] is True


def test_guard_or_oom_group_blocks_even_a_large_cgroup():
    assert safe_boot_vnext.child_admission(
        _memory(limit=512, guard_ok=False)
    )[0] is False
    assert safe_boot_vnext.child_admission(
        _memory(limit=512, oom_group=1)
    )[0] is False


def test_production_preflight_blocks_shopping_and_budget_without_fork(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **_: _memory())
    monkeypatch.setattr(
        app, "_launch_isolated_heavy_worker_locked",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("unsafe child process started")
        ),
    )
    assert app._isolated_worker_admission_ok("shopping") is False
    assert app._isolated_worker_admission_ok("budget") is False
    assert app._request_isolated_source_worker("shopping") == "HOLD"
    assert app._request_isolated_source_worker("budget") == "HOLD"


def test_production_preflight_uses_operator_worker_soft_limit(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **_: _memory(effective=55))
    monkeypatch.setenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB", "160")
    # At effective=55 MiB, 55+160+64 is below 256? No: 279 > 256.
    assert app._isolated_worker_admission_ok("budget") is False
    monkeypatch.setenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB", "96")
    assert app._isolated_worker_admission_ok("shopping") is True


def test_unknown_cgroup_isolation_prevents_in_process_source_run(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app.memory_guard, "low_memory_web_hold", lambda: False)
    monkeypatch.setattr(
        app.memory_guard,
        "container_budget_snapshot",
        lambda: {"limit_mib": 0},
    )
    assert app._web_source_isolation_required() is True


def test_automatic_scheduler_defers_work_without_blocking_http(monkeypatch):
    app = vnext_clean_app
    state = {"enabled": True, "grace_calls": 0, "runs": 0}
    waits = []
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: state["enabled"])

    def next_grace():
        state["grace_calls"] += 1
        return 300 if state["grace_calls"] == 1 else 0

    def run_source():
        state["runs"] += 1
        state["enabled"] = False
        return {"operational_cycle_lease": "ISOLATED_AUTOMATIC"}

    monkeypatch.setattr(app, "_web_first_grace_remaining", next_grace)
    monkeypatch.setattr(app, "_web_source_isolation_required", lambda: True)
    monkeypatch.setattr(app, "_run_low_memory_automatic_cycle", run_source)
    monkeypatch.setattr(
        app._RECENT_COLLECTION_WAKE,
        "wait",
        lambda delay: waits.append(delay) or False,
    )
    app._recent_collection_worker()
    assert waits == [15]
    assert state["runs"] == 1


def test_queued_worker_must_obey_same_headroom_gate():
    source = inspect.getsource(vnext_clean_app._isolated_heavy_supervisor_worker)
    assert "_isolated_worker_admission_ok(next_mode)" in source
    assert "_launch_isolated_heavy_worker_locked(next_mode)" in source


def test_web_read_routes_preserved_while_auto_collection_waits():
    routes = {route.path for route in vnext_clean_app.app.routes}
    assert {"/", "/shopping", "/budget", "/vendors", "/dashboard", "/live", "/health"} <= routes
