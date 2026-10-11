"""4.1.260: a larger Cafe24 cgroup must NEVER move collection into the web."""

import subprocess
import threading

import pytest

import safe_boot_vnext
import vnext_clean_app as app
from scripts.g2b_postboot_collection_admission import assess_postboot_admission


def _forecast(effective, *, limit=512, rss=94, ok=True):
    return {
        "guard_ok": bool(ok),
        "heavy_work_ok": bool(ok),
        "cgroup_oom_group": 0,
        "cgroup_limit_mib": limit,
        "cgroup_effective_mib": effective,
        "rss_mib": rss,
    }


@pytest.mark.parametrize("limit", [256, 512, 1024])
def test_all_production_unified_memory_tiers_isolate_sources(monkeypatch,limit):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot",
                        lambda: {"limit_mib":limit})
    monkeypatch.setattr(app.memory_guard,"low_memory_web_hold", lambda: False)
    assert app._web_source_isolation_required() is True
    # The source work belongs to a child even when a 512MiB web is healthy.
    assert "_run_recent_collection_once()" not in app._web_source_isolation_required.__code__.co_names


def test_non_unified_and_test_modes_keep_compatibility(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    assert app._web_source_isolation_required() is False
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: False)
    assert app._web_source_isolation_required() is False


@pytest.mark.parametrize("limit, effective, allowed", [
    (256, 79.9, True),
    (256, 80, False),
    (512, 335.9, True),
    (512, 336, False),
    (512, 380, False),
    (1024, 847.9, True),
    (1024, 848, False),
])
def test_reserved_headroom_strictly_applies_even_at_512mib(limit,effective,allowed):
    state = _forecast(effective,limit=limit)
    success, reason = safe_boot_vnext.child_admission(state,worker_soft_limit_mib=112)
    assert success is allowed, (limit,effective,reason)
    if not allowed:
        assert reason == "CGRP_CHILD_HEADROOM_UNSAFE"


def test_512mib_unknown_cgroup_and_oom_group_fail_closed():
    assert safe_boot_vnext.child_admission(
        _forecast(0,limit=0)
    ) == (False,"CGROUP_LIMIT_UNAVAILABLE")
    high = _forecast(0,limit=512)
    assert safe_boot_vnext.child_admission(high)==(False,"CGRP_PRESSURE_UNAVAILABLE")
    high["cgroup_effective_mib"]=120
    high["cgroup_oom_group"]=1
    assert safe_boot_vnext.child_admission(high)==(False,"CGROUP_OOM_GROUP")


def test_512mib_postboot_report_is_isolated_and_needs_headroom():
    blocked=assess_postboot_admission(
        _forecast(350),role="UNIFIED",auto_sync_enabled=True,
        test_mode=False,web_grace_operator_confirmed=True,
    )
    assert blocked["collection_execution_mode"]=="ISOLATED"
    assert blocked["max_prelaunch_effective_mib_exclusive"]==336
    assert blocked["worker_headroom_reserve_mib"]==64
    assert not blocked["automatic_collection_deployment_ready"]
    allowed=assess_postboot_admission(
        _forecast(130),role="UNIFIED",auto_sync_enabled=True,
        test_mode=False,web_grace_operator_confirmed=True,
    )
    assert allowed["automatic_collection_deployment_ready"]
    assert allowed["current_admission_reason"]=="LARGE_CGROUP_CHILD_HEADROOM_OK"


def test_512mib_admission_fails_closed_before_source_spawn(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app,"is_unified",lambda:True)
    monkeypatch.setattr(app.memory_guard,"snapshot",lambda **kw:_forecast(350))
    assert app._isolated_worker_admission_ok("shopping") is False
    monkeypatch.setattr(app.memory_guard,"snapshot",lambda **kw:_forecast(130))
    assert app._isolated_worker_admission_ok("shopping") is True


def test_512mib_manual_source_schedules_disposable_child_not_web_work(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app,"is_unified",lambda:True)
    monkeypatch.setattr(app,"can_collect_sources",lambda:True)
    monkeypatch.setattr(app.memory_guard,"low_memory_web_hold",lambda:False)
    monkeypatch.setattr(app,"_request_isolated_source_worker",lambda mode:
                        "STARTED" if mode=="shopping" else "HOLD")
    monkeypatch.setattr(app, "_manual_collection_worker",lambda *a,**kw:
                        (_ for _ in ()).throw(AssertionError("ran web collector")))
    monkeypatch.setattr(app,"_set_source_collection_state",lambda *a,**kw:None)
    monkeypatch.setattr(app,"_set_recent_collection_state",lambda **kw:None)
    assert app.schedule_manual_collection("shopping") is True


def test_512mib_parent_cgroup_block_terminates_disposable_child(monkeypatch):
    class Busy:
        pid=5120
        def __init__(self):
            self.alive=True
            self.terminated=False
        def wait(self,timeout=None):
            if self.alive:
                assert timeout is not None, "unbounded child wait hides 512MiB OOM"
                raise subprocess.TimeoutExpired("g2b_heavy_worker",timeout)
            return -15
        def poll(self):
            return None if self.alive else -15
        def terminate(self):
            self.terminated=True
            self.alive=False
        def kill(self):
            self.terminated=True
            self.alive=False
    monkeypatch.setattr(app,"TEST_MODE",False)
    monkeypatch.setattr(app,"is_unified",lambda:True)
    monkeypatch.setattr(app.memory_guard,"low_memory_web_hold",lambda:False)
    monkeypatch.setattr(app.memory_guard,"container_budget_snapshot",lambda: {
        "limit_mib":512,"effective_mib":462,
        "block_threshold_mib":460.8,"blocked":True,"oom_group":0
    })
    monkeypatch.setattr(app.memory_guard,"current_rss_mib",lambda:94)
    monkeypatch.setattr(app.memory_guard,"soft_limit_mib",lambda:160)
    child=Busy()
    code,preempted=app._wait_for_isolated_heavy_child(child,"budget")
    assert (code,preempted)==(-15,True)
    assert child.terminated


def test_512mib_running_child_is_not_preempted_under_healthy_pressure(monkeypatch):
    snapshot={
        "limit_mib":512,"effective_mib":280,
        "block_threshold_mib":460.8,"blocked":False,"oom_group":0,
    }
    assert app._isolated_worker_memory_abort_reason(snapshot) == ""
    snapshot["effective_mib"]=461
    assert app._isolated_worker_memory_abort_reason(snapshot)=="CGROUP_BLOCK"


def test_512mib_web_first_grace_still_protects_startup(monkeypatch):
    monkeypatch.setattr(app,"TEST_MODE",False)
    monkeypatch.setattr(app,"is_unified",lambda:True)
    monkeypatch.setattr(app.memory_guard,"low_memory_web_hold",lambda:False)
    observed=[]
    monkeypatch.setattr(app.safe_boot_vnext,"boot_grace_remaining",lambda started_monotonic,**kw:
                        observed.append(kw) or 300)
    assert app._web_first_grace_remaining()==300
    assert observed[0]["low_memory"] is True


def test_512mib_automatic_run_never_invokes_inline_source(monkeypatch):
    monkeypatch.setattr(app,"TEST_MODE",False)
    monkeypatch.setattr(app,"is_unified",lambda:True)
    monkeypatch.setattr(app,"_auto_sync_enabled",lambda:True)
    monkeypatch.setattr(app,"_web_first_grace_remaining",lambda:0)
    monkeypatch.setattr(app,"_run_recent_collection_once",lambda *_a,**_k:
                        (_ for _ in ()).throw(AssertionError("inline source called")))
    calls=[]
    monkeypatch.setattr(app,"_run_low_memory_automatic_cycle",lambda:
                        calls.append("isolated") or {"operational_cycle_lease":"ISOLATED_AUTOMATIC"})
    monkeypatch.setattr(app,"_automatic_cycle_wait_seconds",lambda *_a:1)
    class Done(BaseException):
        pass
    class Wake:
        def clear(self):pass
        def wait(self,seconds):raise Done()
    monkeypatch.setattr(app,"_RECENT_COLLECTION_WAKE",Wake())
    monkeypatch.setattr(app,"_RECENT_COLLECTION_THREAD",threading.current_thread())
    with pytest.raises(Done):
        app._recent_collection_worker()
    assert calls==["isolated"]
    assert app._RECENT_COLLECTION_THREAD is None
