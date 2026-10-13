"""4.1.289 — stagger 256MiB shopping/LOFIN automatic collection without overlap.

Each source has four-hour slots, offset two hours from the other:
    00–02 shopping; 02–04 budget; 04–06 shopping; etc.
Existing source singleton, PG advisory locks, web/worker memory gates, quotas,
manual buttons and durable checkpoints must all remain unchanged.
The tests never call external APIs or a real PostgreSQL database.
"""
import datetime as dt

import pytest

import safe_boot_vnext
import vnext_clean_app as app

KST = dt.timezone(dt.timedelta(hours=9))


def _now(day=13,hour=0,minute=0,second=0):
    return dt.datetime(2026,10,day,hour,minute,second,tzinfo=KST)


@pytest.mark.parametrize(("hour","source","switch"),[
    (0,"shopping","02:00:00"),
    (1,"shopping","02:00:00"),
    (2,"budget","04:00:00"),
    (3,"budget","04:00:00"),
    (4,"shopping","06:00:00"),
    (6,"budget","08:00:00"),
    (8,"shopping","10:00:00"),
    (10,"budget","12:00:00"),
    (20,"shopping","22:00:00"),
    (22,"budget","00:00:00"),
])
def test_kst_phase_each_source_every_four_hours(hour,source,switch):
    phase=app._small_cgroup_source_stagger_slot(_now(hour=hour))
    assert phase["source"] == source
    assert phase["next_change_at_kst"].endswith(switch+"+09:00")
    assert phase["seconds_until_change"] == 7200
    assert not phase["slot_id"].startswith("UTC")


def test_near_boundary_wake_is_not_delayed_two_hours():
    phase=app._small_cgroup_source_stagger_slot(_now(hour=1,minute=59,second=45))
    assert phase["source"]=="shopping"
    assert phase["seconds_until_change"] == 15
    assert phase["next_change_at_kst"].startswith("2026-10-13T02:00:00")
    next_day=app._small_cgroup_source_stagger_slot(_now(hour=23,minute=59,second=59))
    assert next_day["seconds_until_change"] == 1
    assert next_day["next_change_at_kst"].startswith("2026-10-14T00:00:00")


def _production_small(monkeypatch,*,limit=256,slot_source="shopping",slot_id="2026-10-13-4"):
    monkeypatch.setattr(app,"TEST_MODE",False)
    monkeypatch.setattr(app,"is_unified",lambda:True)
    monkeypatch.setattr(app,"_auto_sync_enabled",lambda:True)
    monkeypatch.setattr(app.memory_guard,"container_budget_snapshot",
                        lambda:{"limit_mib":limit})
    monkeypatch.setattr(app,"_small_cgroup_source_stagger_slot",lambda now=None:{
        "slot_id":slot_id,
        "source":slot_source,
        "seconds_until_change":1200,
        "next_change_at_kst":"2026-10-13T10:00:00+09:00",
    })
    monkeypatch.setattr(app,"_RECENT_COLLECTION_EXPLICIT_WAKE_REVISION",0)
    monkeypatch.setattr(app,"_RECENT_COLLECTION_STATE",{
        "shopping_run_state":"IDLE",
        "budget_run_state":"IDLE",
    })
    monkeypatch.setattr(app,"_isolated_heavy_worker_status",lambda:{
        "running":False,"pending":[],"supervisor_running":False,
        "reap_pending":False,
    })


@pytest.mark.parametrize(("source","other"),[
    ("shopping","budget"),
    ("budget","shopping"),
])
def test_256_auto_cycle_reserves_only_one_source_per_phase(monkeypatch,source,other):
    _production_small(monkeypatch,slot_source=source)
    called=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda name:
                        called.append(name) or True)
    outcome=app._run_low_memory_automatic_cycle()
    assert called==[source]
    assert outcome["scheduled"]=={source:True,other:False}
    assert outcome["source_stagger"]["source"]==source
    assert app._RECENT_COLLECTION_STATE["source_stagger_last_source"]==source
    # A genuinely running child belongs to the real existing single-process
    # worker slot; the new scheduler must never queue the competing source.
    assert other not in called


def test_256_finished_phase_is_not_requeued_by_second_hour_tick(monkeypatch):
    _production_small(monkeypatch)
    calls=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda s:
                        calls.append(s) or True)
    assert app._run_low_memory_automatic_cycle()["scheduled"]["shopping"]
    app._RECENT_COLLECTION_STATE["shopping_run_state"]="COMPLETE"
    result=app._run_low_memory_automatic_cycle()
    assert result["scheduled"]=={"shopping":False,"budget":False}
    assert calls==["shopping"]


@pytest.mark.parametrize("state",["PARTIAL","WAITING_MEMORY","FAILED","WAITING_STORAGE"])
def test_256_incomplete_or_denied_source_can_retry_its_own_phase(monkeypatch,state):
    _production_small(monkeypatch)
    calls=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda s:
                        calls.append(s) or True)
    app._run_low_memory_automatic_cycle()
    app._RECENT_COLLECTION_STATE["shopping_run_state"]=state
    result=app._run_low_memory_automatic_cycle()
    assert result["scheduled"]["shopping"] is True
    assert calls==["shopping","shopping"]


def test_256_denied_admission_does_not_consume_rotation_slot(monkeypatch):
    _production_small(monkeypatch)
    calls=[]
    def held(s):
        calls.append(s)
        app._RECENT_COLLECTION_STATE["shopping_run_state"]="WAITING_MEMORY"
        return False
    monkeypatch.setattr(app,"schedule_manual_collection",held)
    assert app._run_low_memory_automatic_cycle()["scheduled"]["shopping"] is False
    assert "source_stagger_last_slot" not in app._RECENT_COLLECTION_STATE
    assert app._run_low_memory_automatic_cycle()["scheduled"]["shopping"] is False
    assert calls==["shopping","shopping"]


def test_256_same_slot_explicit_key_wake_is_honored_once(monkeypatch):
    _production_small(monkeypatch)
    calls=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda s:
                        calls.append(s) or True)
    app._run_low_memory_automatic_cycle()
    app._RECENT_COLLECTION_STATE["shopping_run_state"]="COMPLETE"
    assert not app._run_low_memory_automatic_cycle()["scheduled"]["shopping"]
    app._RECENT_COLLECTION_EXPLICIT_WAKE_REVISION += 1
    # Simulate a key update with no lingering last-success shopping throttle.
    monkeypatch.setattr(app,"_shopping_auto_due",lambda:True)
    assert app._run_low_memory_automatic_cycle()["scheduled"]["shopping"]
    assert calls==["shopping","shopping"]


def test_512mib_retains_existing_sequential_scheduler(monkeypatch):
    _production_small(monkeypatch,limit=512)
    called=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda s:
                        called.append(s) or True)
    result=app._run_low_memory_automatic_cycle()
    assert called==["shopping","budget"]
    assert result["source_stagger"] is None
    assert result["scheduled"]=={"shopping":True,"budget":True}


def test_small_cgroup_unknown_fails_closed_to_single_source_schedule(monkeypatch):
    _production_small(monkeypatch,limit=0)
    assert app._small_cgroup_source_stagger_enabled()
    called=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda s:
                        called.append(s) or True)
    app._run_low_memory_automatic_cycle()
    assert called==["shopping"]


def test_wait_boundary_is_capped_only_on_small_production(monkeypatch):
    _production_small(monkeypatch)
    assert app._bound_automatic_wait_to_stagger(7200)==1200
    assert app._bound_automatic_wait_to_stagger(60)==60
    assert app._bound_automatic_wait_to_stagger(600)==600
    monkeypatch.setattr(app.memory_guard,"container_budget_snapshot",
                        lambda:{"limit_mib":512})
    assert app._bound_automatic_wait_to_stagger(7200)==7200


def test_quota_wait_on_one_source_cannot_hide_other_phase(monkeypatch):
    _production_small(monkeypatch)
    app._RECENT_COLLECTION_STATE.update(
        shopping_run_state="WAITING_QUOTA",
        budget_run_state="COMPLETE",
    )
    monkeypatch.setattr(app,"_seconds_until_next_kst_date",lambda now=None:86400)
    assert app._automatic_cycle_wait_seconds({})==1200


def test_256_zero_extra_worker_admission_and_no_safety_relaxation(monkeypatch):
    _production_small(monkeypatch)
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB==64
    assert safe_boot_vnext.child_admission({
        "guard_ok":True,
        "cgroup_limit_mib":256,
        "cgroup_effective_mib":113.3,
        "cgroup_oom_group":0,
    },worker_soft_limit_mib=112)==(False,"CGRP_CHILD_HEADROOM_UNSAFE")
    # Rotation only reduces source scheduling, not memory admission, API quotas
    # or an existing validated PostgreSQL normalized data/checkpoint contract.


def test_normal_and_lowtier_status_include_honest_schedule_mode(monkeypatch):
    _production_small(monkeypatch)
    # Status requires a small collection of read-only in-memory and source
    # values; verify code-level status field wiring without touching PG.
    import inspect
    source=inspect.getsource(app.recent_collection_status)
    for term in ("STAGGERED_4H_PER_SOURCE","auto_source_active_source",
                 "auto_source_next_switch_kst","budget_interval_seconds"):
        assert term in source


def test_long_running_shopping_hands_off_to_budget_on_new_phase(monkeypatch):
    _production_small(monkeypatch,slot_source="budget",slot_id="2026-10-13-5")
    old_outcome={
        "operational_cycle_lease":"ISOLATED_AUTOMATIC",
        "source_stagger":{
            "slot_id":"2026-10-13-4",
            "source":"shopping",
        },
    }
    # The shopping child started in the previous two-hour phase and only
    # now finished; don't sleep for another 2h before inspecting budget.
    assert app._automatic_cycle_wait_seconds(old_outcome)==1


def test_collection_page_calls_scheduler_a_scheduler_not_source_child():
    import inspect
    src=inspect.getsource(app.collection_monitor_page)
    assert "자동수집 스케줄러" in src
    assert "stagger_notice" in src
    assert "메모리 입장 승인을 뜻하지 않습니다" in src


def test_new_lofin_key_can_wake_budget_inside_shopping_phase(monkeypatch):
    _production_small(monkeypatch,slot_source="shopping")
    monkeypatch.setattr(app,"set_source_credential",lambda *a,**kw:None)
    assert app._save_source_key_updates(lofin_key="TEST_ONLY_NO_NETWORK")
    assert app._RECENT_COLLECTION_STATE["source_stagger_key_wake_pending"]==["budget"]
    calls=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda source:
                        calls.append(source) or True)
    result=app._run_low_memory_automatic_cycle()
    assert result["scheduled"]=={"shopping":False,"budget":True}
    assert calls==["budget"]
    assert app._RECENT_COLLECTION_STATE["source_stagger_key_wake_pending"]==[]


def test_both_keys_use_one_child_per_cycle_and_prompt_handoff(monkeypatch):
    _production_small(monkeypatch,slot_source="shopping")
    monkeypatch.setattr(app,"set_source_credential",lambda *a,**kw:None)
    assert app._save_source_key_updates(
        g2b_key="TEST_G2B_NO_NETWORK",lofin_key="TEST_LOFIN_NO_NETWORK"
    )
    scheduled=[]
    monkeypatch.setattr(app,"schedule_manual_collection",lambda source:
                        scheduled.append(source) or True)
    result=app._run_low_memory_automatic_cycle()
    assert result["scheduled"]=={"shopping":True,"budget":False}
    assert app._RECENT_COLLECTION_STATE["source_stagger_key_wake_pending"]==["budget"]
    assert app._automatic_cycle_wait_seconds(result)==1
    # First child has exited and been reaped; the next cycle may wake LOFIN.
    app._RECENT_COLLECTION_STATE["shopping_run_state"]="COMPLETE"
    result=app._run_low_memory_automatic_cycle()
    assert result["scheduled"]=={"shopping":False,"budget":True}
    assert scheduled==["shopping","budget"]
    assert app._RECENT_COLLECTION_STATE["source_stagger_key_wake_pending"]==[]


def test_key_wake_survives_source_memory_hold(monkeypatch):
    _production_small(monkeypatch,slot_source="shopping")
    monkeypatch.setattr(app,"_RECENT_COLLECTION_STATE",{
        "shopping_run_state":"WAITING_MEMORY",
        "budget_run_state":"IDLE",
        "source_stagger_key_wake_pending":["budget"],
    })
    monkeypatch.setattr(app,"schedule_manual_collection",lambda source:False)
    outcome=app._run_low_memory_automatic_cycle()
    assert not outcome["scheduled"]["budget"]
    assert app._RECENT_COLLECTION_STATE["source_stagger_key_wake_pending"]==["budget"]
    # Failed admission of a newly added LOFIN key must use the existing
    # bounded memory retry, not a one-second tight source-launch loop.
    assert app._automatic_cycle_wait_seconds({}) >= 60


def test_manual_collection_function_is_not_time_slot_limited():
    import inspect
    source=inspect.getsource(app.schedule_manual_collection)
    assert "_small_cgroup_source_stagger_slot" not in source
    assert "_request_isolated_source_worker(source)" in source
