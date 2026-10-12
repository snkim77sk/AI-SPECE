"""G2B 4.1.287: source-free idle recovery and four-hour shopping throttle."""
import asyncio
import datetime as dt
import threading
import time

import pytest

import g2b_idle_memory_recovery as rec
import vnext_clean_app as app
import safe_boot_vnext


def _memory(*,limit=256,effective=104,current=104,rss=100):
    return {
        "guard_ok": True,
        "rss_mib":rss,
        "cgroup_limit_mib":limit,
        "cgroup_current_mib":current,
        "cgroup_effective_mib":effective,
        "cgroup_oom_group":0,
    }


def _idle(monkeypatch, *, seconds=200):
    now=time.monotonic()
    monkeypatch.setattr(rec,"_START_MONOTONIC",now)
    monkeypatch.setattr(rec,"_LAST_ACTIVITY",now-seconds)
    monkeypatch.setattr(rec,"_LAST_ATTEMPT",0.0)
    monkeypatch.setattr(rec,"_ACTIVE_REQUESTS",0)
    monkeypatch.setattr(rec,"_ATTEMPTS",0)
    monkeypatch.setattr(rec,"_SKIPS",0)
    monkeypatch.setattr(rec,"_LAST_RESULT",{"status":"waiting","reason":"test"})


def _callbacks():
    return {"workers_busy":lambda:False,"backend_ready":lambda:True}


def test_idle_collector_headroom_hold_triggers_early_web_trim(monkeypatch):
    _idle(monkeypatch)
    sampled=[_memory(effective=104,current=105,rss=100),
             _memory(effective=82,current=87,rss=80)]
    monkeypatch.setattr(rec.memory_guard,"snapshot",lambda **kw:sampled.pop(0))
    trimmed=[]
    monkeypatch.setattr(rec,"_trim_allocator",lambda:trimmed.append(True) or True)
    monkeypatch.setattr(rec.gc,"collect",lambda:7)
    result=rec.run_recovery_cycle(**_callbacks())
    assert result["mode"]=="pressure"
    assert result["reason"]=="child-headroom-held"
    assert result["effective_reclaimed_mib"]==22
    assert result["web_rss_reclaimed_mib"]==20
    assert result["gc_objects"]==7
    assert result["malloc_trim_called"]
    assert trimmed==[True]
    assert rec.recovery_status()["attempts"]==1
    assert rec.recovery_status()["does_not_restart_web"]


def test_memory_reclaim_never_runs_during_collector(monkeypatch):
    _idle(monkeypatch)
    monkeypatch.setattr(rec.memory_guard,"snapshot",lambda **kw:_memory())
    monkeypatch.setattr(rec.gc,"collect",lambda:(
        (_ for _ in ()).throw(AssertionError("cannot GC with active worker"))
    ))
    result=rec.run_recovery_cycle(workers_busy=lambda:True,backend_ready=lambda:True)
    assert result["reason"]=="worker-or-supervisor-active"
    assert rec.recovery_status()["attempts"]==0


def test_memory_reclaim_skips_active_user_and_recent_requests(monkeypatch):
    _idle(monkeypatch)
    monkeypatch.setattr(rec.memory_guard,"snapshot",lambda **kw:_memory())
    rec.mark_request_start()
    assert rec.run_recovery_cycle(**_callbacks())["reason"]=="requests-active"
    rec.mark_request_end()
    assert rec.run_recovery_cycle(**_callbacks())["reason"]=="recent-visitor-activity"
    assert rec.recovery_status()["attempts"]==0


def test_worker_pressure_cooldown_and_normal_two_day_interval(monkeypatch):
    _idle(monkeypatch)
    assert rec.CHECK_INTERVAL_SECONDS==900
    monkeypatch.setattr(rec.memory_guard,"snapshot",lambda **kw:_memory())
    monkeypatch.setattr(rec,"_trim_allocator",lambda:True)
    monkeypatch.setattr(rec.gc,"collect",lambda:0)
    assert rec.run_recovery_cycle(**_callbacks())["status"]=="completed"
    assert rec.run_recovery_cycle(**_callbacks())["reason"]=="cooldown"

    _idle(monkeypatch)
    monkeypatch.setattr(rec.memory_guard,"snapshot",lambda **kw:
                        _memory(limit=512,effective=125,current=140,rss=70))
    assert rec.run_recovery_cycle(**_callbacks())["reason"]=="periodic-not-due"
    monkeypatch.setattr(rec,"_START_MONOTONIC",time.monotonic()-72*3600-1)
    assert rec.run_recovery_cycle(**_callbacks())["mode"]=="periodic-idle"


def test_memory_recovery_env_switch_and_periodic_clamp(monkeypatch):
    monkeypatch.setenv("G2B_IDLE_MEMORY_RECOVERY","off")
    assert not rec.enabled()
    assert rec.run_recovery_cycle(**_callbacks())["reason"]=="disabled"
    monkeypatch.setenv("G2B_IDLE_MEMORY_RECOVERY","on")
    assert rec.enabled()
    monkeypatch.setenv("G2B_IDLE_MEMORY_RECLAIM_HOURS","5")
    assert rec.periodic_seconds()==48*3600
    monkeypatch.setenv("G2B_IDLE_MEMORY_RECLAIM_HOURS","999")
    assert rec.periodic_seconds()==72*3600


def test_memory_probe_failure_and_unknown_worker_state_fail_closed(monkeypatch):
    _idle(monkeypatch)
    monkeypatch.setattr(rec.memory_guard,"snapshot",lambda **kw:
                        (_ for _ in ()).throw(
                            RuntimeError("postgresql://user:secret@private/")
                        ))
    report=rec.run_recovery_cycle(**_callbacks())
    assert report["reason"]=="memory-probe-RuntimeError"
    assert "secret" not in repr(report)
    assert rec.run_recovery_cycle()["reason"]=="worker-or-backend-guard-unavailable"


def test_activity_middleware_tracks_long_requests_and_ignores_health():
    async def scenario():
        async def handler(scope,receive,send):
            assert rec.recovery_status()["active_requests"] == (
                0 if scope["path"]=="/health" else 1
            )
        app_=rec.ActivityMiddleware(handler)
        await app_({"type":"http","path":"/shopping"},None,None)
        assert rec.recovery_status()["active_requests"]==0
        await app_({"type":"http","path":"/health"},None,None)
        assert rec.recovery_status()["active_requests"]==0
        await app_({"type":"http","path":"/collection-monitor"},None,None)
        assert rec.recovery_status()["active_requests"]==0
    asyncio.run(scenario())


def test_no_service_restart_storage_api_or_cgroup_threshold_relaxation():
    from pathlib import Path
    source=Path(rec.__file__).read_text(encoding="utf-8")
    for forbidden in ("os.kill(", "os.exec", "shutil.rmtree", "init_db(", "DROP TABLE",
                      "DROP SCHEMA", "urllib.request", "psycopg.connect"):
        assert forbidden not in source
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB==64
    assert safe_boot_vnext.child_admission(_memory()) == (
        False,"CGRP_CHILD_HEADROOM_UNSAFE"
    )


def _shopping_state(monkeypatch, *, run_state="COMPLETE", age_seconds=0,
                    explicit_completed=1, explicit_now=1):
    # CI runner uptime can be below 3–4 hours; use a positive virtual
    # monotonic clock for age-based auto refresh assertions.
    current=100000.0
    monkeypatch.setattr(app.time, "monotonic", lambda: current)
    monkeypatch.setattr(app,"_RECENT_COLLECTION_STATE",{
        "shopping_run_state":run_state,
        "shopping_complete_monotonic":current-age_seconds,
        "shopping_complete_explicit_revision":explicit_completed,
        "budget_run_state":"COMPLETE",
    })
    monkeypatch.setattr(app,"_RECENT_COLLECTION_EXPLICIT_WAKE_REVISION",explicit_now)


def test_four_hour_shopping_complete_only_gate(monkeypatch):
    monkeypatch.delenv("G2B_SHOPPING_COMPLETE_REFRESH_SECONDS",raising=False)
    assert app.SHOPPING_COMPLETE_REFRESH_SECONDS==4*3600
    _shopping_state(monkeypatch,age_seconds=3*3600)
    assert app._shopping_auto_due() is False
    _shopping_state(monkeypatch,age_seconds=4*3600+10)
    assert app._shopping_auto_due() is True


def test_partial_shopping_and_explicit_wake_skip_four_hour_throttle(monkeypatch):
    _shopping_state(monkeypatch,run_state="PARTIAL",age_seconds=100)
    assert app._shopping_auto_due() is True
    _shopping_state(monkeypatch,age_seconds=100,explicit_completed=1,explicit_now=2)
    assert app._shopping_auto_due() is True


def test_source_cycle_skips_recent_complete_shopping_without_delaying_budget(monkeypatch):
    _shopping_state(monkeypatch,age_seconds=80)
    called=[]
    monkeypatch.setattr(app,"_auto_sync_enabled",lambda:True)
    monkeypatch.setattr(app,"schedule_manual_collection",lambda src:
                        called.append(src) or True)
    monkeypatch.setattr(app,"_isolated_heavy_worker_status",
                        lambda:{
                            "running":False,
                            "pending":[],
                            "supervisor_running":False,
                            "reap_pending":False,
                        })
    outcome=app._run_low_memory_automatic_cycle()
    assert outcome["scheduled"]=={"shopping":False,"budget":True}
    assert called==["budget"]


def test_manual_shopping_completion_sets_clock_without_pg(monkeypatch):
    _shopping_state(monkeypatch,run_state="RUNNING",age_seconds=100000)
    monkeypatch.setattr(app,"get_setting",lambda *a,**k:
                        (_ for _ in ()).throw(
                            AssertionError("no DB on successful state"))
                        )
    app._isolated_worker_exit_state("shopping",0)
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"]=="COMPLETE"
    assert app._RECENT_COLLECTION_STATE["shopping_complete_monotonic"]>0
    assert app._shopping_auto_due() is False


def test_budget_stays_two_hour_without_shopping_extra_api_calls(monkeypatch):
    _shopping_state(monkeypatch,age_seconds=2*3600)
    monkeypatch.setattr(app,"_PARENT_MEMORY_ABORT_STREAK",{"shopping":0,"budget":0})
    assert app.SHOPPING_SYNC_INTERVAL_SECONDS==7200
    assert app._automatic_cycle_wait_seconds({})==7200
    assert app._shopping_auto_due() is False


def test_quota_on_one_source_does_not_hold_other_until_midnight(monkeypatch):
    _shopping_state(monkeypatch,age_seconds=5*3600)
    monkeypatch.setattr(app,"_seconds_until_next_kst_date",lambda *_:9000)
    app._RECENT_COLLECTION_STATE["budget_run_state"]="WAITING_QUOTA"
    assert app._automatic_cycle_wait_seconds({})==7200
    app._RECENT_COLLECTION_STATE["shopping_run_state"]="WAITING_QUOTA"
    app._RECENT_COLLECTION_STATE["budget_run_state"]="WAITING_QUOTA"
    assert app._automatic_cycle_wait_seconds({})==9000


def test_lifespan_installs_light_monitor_only_when_production_unified():
    import inspect
    src=inspect.getsource(app.lifespan)
    assert "monitor_idle_memory_recovery" in src
    assert "not TEST_MODE and is_unified()" in src
    assert "memory_task.cancel()" in src



def test_passive_status_html_tracks_active_read_without_resetting_idle(monkeypatch):
    _idle(monkeypatch)
    monkeypatch.setattr(rec.memory_guard, "snapshot", lambda **kw: _memory())
    before = rec._LAST_ACTIVITY

    async def check():
        async def status(scope, receive, send):
            assert scope["path"] == "/collection-monitor"
            assert rec.recovery_status()["active_requests"] == 1
            assert rec.run_recovery_cycle(**_callbacks())["reason"] == "requests-active"
        await rec.ActivityMiddleware(status)(
            {"type": "http", "path": "/collection-monitor"},
            None, None,
        )
    asyncio.run(check())
    assert rec.recovery_status()["active_requests"] == 0
    assert rec._LAST_ACTIVITY == before, "30s passive polls must not starve reclaim"


def test_rss_guard_pressure_gets_early_idle_trim_without_loosening_guard(monkeypatch):
    _idle(monkeypatch)
    bad = {
        **_memory(limit=256, effective=154, current=183, rss=164),
        "guard_ok": False,
        "process_guard_ok": False,
        "guard_state": "PROCESS_RSS_HOLD",
    }
    assert rec._pressure_reason(bad) == "web-rss-held"
    good = {
        **_memory(limit=256, effective=137, current=166, rss=146),
        "process_guard_ok": True,
        "guard_state": "SAFE",
    }
    samples = [bad, good]
    monkeypatch.setattr(rec.memory_guard, "snapshot", lambda **kw: samples.pop(0))
    monkeypatch.setattr(rec.gc, "collect", lambda: 3)
    monkeypatch.setattr(rec, "_trim_allocator", lambda: True)
    result = rec.run_recovery_cycle(**_callbacks())
    assert result["reason"] == "web-rss-held"
    assert result["effective_reclaimed_mib"] == 17
    assert result["web_rss_reclaimed_mib"] == 18
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64


def test_cgroup_wait_guard_is_eligible_for_early_reclaim():
    state = {
        **_memory(limit=256, effective=198, current=198, rss=133),
        "guard_ok": False,
        "process_guard_ok": True,
        "guard_state": "CGROUP_WAIT",
    }
    assert rec._pressure_reason(state) == "web-memory-guard-held"


def test_recovery_holds_worker_launch_mutex_across_actual_gc(monkeypatch):
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", None)
    monkeypatch.setattr(app, "_MATCH_BACKFILL_THREAD", None)
    monkeypatch.setattr(app, "_MANUAL_COLLECTION_THREADS",
                        {"shopping": None, "budget": None})
    invoked = []
    def run(**kw):
        assert not app._ISOLATED_HEAVY_LOCK.acquire(blocking=False), (
            "new Worker could start during malloc_trim"
        )
        assert kw["workers_busy"]() is False
        invoked.append(True)
        return {"status": "completed"}
    monkeypatch.setattr(app.g2b_idle_memory_recovery, "run_recovery_cycle", run)
    assert app._idle_memory_recovery_cycle()["status"] == "completed"
    assert invoked == [True]
    assert app._ISOLATED_HEAVY_LOCK.acquire(blocking=False)
    app._ISOLATED_HEAVY_LOCK.release()


def test_recovery_skips_when_source_launch_mutex_is_already_owned(monkeypatch):
    monkeypatch.setattr(app.g2b_idle_memory_recovery, "run_recovery_cycle",
                        lambda **kw: (_ for _ in ()).throw(
                            AssertionError("new source launch must take priority")
                        ))
    with app._ISOLATED_HEAVY_LOCK:
        assert app._idle_memory_recovery_cycle() == {
            "status": "skipped", "reason": "worker-launch-in-progress"
        }


def test_recovery_monitor_only_runs_synchronized_callback(monkeypatch):
    counters = []
    async def scenario():
        monkeypatch.setattr(rec, "CHECK_INTERVAL_SECONDS", 0.01)
        task = asyncio.create_task(rec.monitor_idle_memory_recovery(
            run_cycle=lambda: counters.append("guarded"),
        ))
        await asyncio.sleep(0.06)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("monitor task must cancel on FastAPI shutdown")
    asyncio.run(scenario())
    assert counters
