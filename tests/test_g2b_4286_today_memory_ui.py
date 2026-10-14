"""4.1.286: KST-today default views and truthful 256MiB worker admission UI.

No source APIs, production PostgreSQL connections, schema mutations, or
database resets. Existing manually entered search ranges remain supported.
"""
import datetime as dt

import pytest
from starlette.requests import Request

import procurement_read_vnext
import safe_boot_vnext
import vnext_clean_app as app


def _req(path="/shopping", query=""):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "query_string": query.encode("utf-8"), "headers": [],
    })


@pytest.mark.parametrize("today", [
    dt.date(2026, 1, 1),
    dt.date(2026, 10, 13),
    dt.date(2026, 12, 31),
    dt.date(2027, 3, 15),
])
def test_shopping_unfiltered_defaults_to_current_kst_year_through_today(today):
    start,end=app._shopping_date_range(_req(),today=today)
    assert start==f"{today.year}-01-01"
    assert end==today.isoformat()


def test_shopping_user_range_is_not_overwritten_by_today_or_future_year_end():
    q="start_date=2026-02-03&end_date=2026-12-31"
    assert app._shopping_date_range(_req(query=q),today=dt.date(2026,10,13)) == (
        "2026-02-03","2026-12-31"
    )


def test_shopping_invalid_or_backwards_range_uses_today_not_future_december():
    today=dt.date(2026,10,13)
    for query in ("start_date=garbage&end_date=nope",
                  "start_date=2026-11-11&end_date=2026-10-13"):
        assert app._shopping_date_range(_req(query=query),today=today) == (
            "2026-01-01","2026-10-13"
        )


def test_shopping_filter_still_respects_2026_data_start():
    assert app._shopping_date_range(
        _req(query="start_date=2024-01-01&end_date=2026-04-01"),
        today=dt.date(2026,10,13),
    ) == ("2026-01-01","2026-04-01")


@pytest.mark.parametrize(("fiscal","today","expected"),[
    (2026,dt.date(2026,10,13),("2026-01-01","2026-10-13")),
    (2027,dt.date(2027,2,20),("2027-01-01","2027-02-20")),
    (2026,dt.date(2027,2,20),("2026-01-01","2026-12-31")),
])
def test_budget_history_current_year_today_but_old_year_full_period(
    fiscal,today,expected,
):
    assert app._budget_history_date_range(
        _req("/budget"),fiscal,today=today,
    ) == expected


def test_budget_history_manual_dates_are_preserved():
    assert app._budget_history_date_range(
        _req("/budget","history_start_date=2026-03-01&history_end_date=2026-12-31"),
        2026,today=dt.date(2026,10,13),
    ) == ("2026-03-01","2026-12-31")


def _cgroup(*,limit,effective,ok=True):
    return {
        "guard_ok": ok, "cgroup_limit_mib": limit,
        "cgroup_effective_mib": effective,
        "cgroup_oom_group": 0,
    }


def test_same_existing_112_plus_64_guard_blocks_256mib_at_95mib(monkeypatch):
    monkeypatch.delenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB",raising=False)
    # Legacy v4.1.286 assertions explicitly test reserve=64 opt-out.
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    sample=_cgroup(limit=256,effective=95)
    details=app._worker_capacity_details(sample)
    assert details["memory_worker_admission_allowed"] is False
    assert details["memory_worker_admission_reason"]=="CGRP_CHILD_HEADROOM_UNSAFE"
    assert details["memory_worker_expected_mib"]==112
    assert details["memory_worker_reserve_mib"]==64
    assert details["memory_worker_max_prelaunch_mib_exclusive"]==80
    assert details["memory_worker_headroom_shortfall_mib"]==15
    assert safe_boot_vnext.child_admission(sample,worker_soft_limit_mib=112) == (
        False,"CGRP_CHILD_HEADROOM_UNSAFE"
    )


def test_512mib_remains_admitted_without_relaxing_worker_guard(monkeypatch):
    monkeypatch.delenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB",raising=False)
    # Legacy v4.1.286 assertions explicitly test reserve=64 opt-out.
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    sample=_cgroup(limit=512,effective=95)
    details=app._worker_capacity_details(sample)
    assert details["memory_worker_admission_allowed"]
    assert details["memory_worker_max_prelaunch_mib_exclusive"]==336
    assert details["memory_worker_headroom_shortfall_mib"]==0
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB==64


def test_collection_memory_explanation_is_read_only_and_actionable(monkeypatch):
    monkeypatch.delenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB",raising=False)
    # Legacy v4.1.286 assertions explicitly test reserve=64 opt-out.
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    sample=_cgroup(limit=256,effective=95)
    state={
        **app._worker_capacity_details(sample),
        "memory_cgroup_limit_mib":256,
        "memory_cgroup_effective_mib":95,
        "shopping_run_state":"WAITING_MEMORY",
        "budget_run_state":"WAITING_MEMORY",
    }
    result=app._collection_worker_capacity_notice(state)
    for term in ("95.0MiB","256MiB","112MiB","64MiB","80.0MiB","15.0MiB","512MiB"):
        assert term in result
    assert "체크포인트는 유지" in result
    assert "LOW_MEMORY_WORKER_UNSAFE" not in result
    # Healthy source status must not be mislabeled as waiting memory.
    state["shopping_run_state"]="COMPLETE"
    state["budget_run_state"]="COMPLETE"
    assert app._collection_worker_capacity_notice(state)==""


def test_actual_health_snapshot_fields_use_existing_memory_probe(monkeypatch):
    monkeypatch.delenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB",raising=False)
    # Legacy v4.1.286 assertions explicitly test reserve=64 opt-out.
    monkeypatch.setenv("G2B_256MIB_SOURCE_START_RESERVE_MIB", "64")
    monkeypatch.setattr(app.memory_guard, "snapshot",lambda **kwargs:{
        **_cgroup(limit=256,effective=95),
        "cgroup_current_mib":100.5, "rss_mib":92.0,
        "soft_limit_mib":160, "cgroup_events":{},
    })
    fields=app._memory_status_fields(collect=False)
    assert fields["memory_cgroup_limit_mib"]==256
    assert fields["memory_worker_admission_reason"]=="CGRP_CHILD_HEADROOM_UNSAFE"
    assert fields["memory_worker_expected_mib"]==112
    assert fields["memory_worker_headroom_shortfall_mib"]==15


def test_shopping_page_explains_default_today_without_live_source(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(app, "require_user", lambda request:{"username":"ci"})
    monkeypatch.setattr(app, "is_result_server", lambda:False)
    monkeypatch.setattr(app, "_shopping_date_range",lambda request:(
        "2026-01-01","2026-10-13"
    ))
    seen=[]
    monkeypatch.setattr(procurement_read_vnext, "shopping_rows",
                        lambda **kwargs: seen.append(kwargs) or [])
    response=app.shopping_page(_req("/shopping"))
    assert response.status_code==200
    body=response.body.decode("utf-8")
    assert "접속한 오늘(한국시간)까지" in body
    assert 'name="end_date" type="date"' in body
    assert 'value="2026-10-13"' in body
    assert seen[0]["start_date"]=="2026-01-01"
    assert seen[0]["end_date"]=="2026-10-13"
