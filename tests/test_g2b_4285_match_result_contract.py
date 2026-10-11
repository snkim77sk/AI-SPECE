"""V4.1.285: Match child exit must reflect actual work, not a stale durable view.

All tests are source-free and use in-memory monkeypatches only: no PostgreSQL
connection, external shopping/LOFIN API call, schema init, or business writes.
"""
from contextlib import nullcontext
import threading

import pytest

import memory_guard
import g2b_heavy_worker as child
import vnext_clean_app as app


@pytest.fixture()
def fake_inner_match(monkeypatch):
    states = []
    monkeypatch.setattr(app, "_HEAVY_WORK_LOCK", threading.Lock())
    monkeypatch.setattr(app, "_set_match_backfill_state",
                        lambda **v: states.append(dict(v)))
    monkeypatch.setattr(app.memory_guard, "snapshot",
                        lambda **k: {
                            "guard_ok": True, "heavy_work_ok": True,
                            "rss_mib": 52.0, "soft_limit_mib": 112,
                        })
    monkeypatch.setattr(app, "_match_rollover_years", lambda: (2026,))
    import budget_shopping_match_vnext as match
    import budget_shopping_match_store as store
    monkeypatch.setattr(
        match, "historical_match_summary",
        lambda **kwargs: {"match_population_complete": True}
    )
    monkeypatch.setattr(
        store, "save_match_summary",
        lambda summary: {"saved_matches": 1}
    )
    monkeypatch.setattr(store, "organization_patterns", lambda **kwargs: [])
    return states, match, store


def test_successful_match_result_is_explicit_complete(fake_inner_match):
    states, _, _ = fake_inner_match
    assert app._match_backfill_worker_unleased(False) == "COMPLETE"
    assert states[-2]["state"] == "COMPLETE"  # followed by finish timestamp
    assert states[-1].get("last_finished_at")
    assert not app._HEAVY_WORK_LOCK.locked()


def test_partial_match_result_is_never_reported_as_success(fake_inner_match,monkeypatch):
    states, match, _ = fake_inner_match
    monkeypatch.setattr(
        match, "historical_match_summary",
        lambda **kwargs: {"match_population_complete": False}
    )
    assert app._match_backfill_worker_unleased(False) == "PARTIAL"
    assert any(x.get("state") == "PARTIAL" for x in states)
    assert not app._HEAVY_WORK_LOCK.locked()


def test_internal_failure_propagates_failed_without_exposing_details(fake_inner_match,monkeypatch,capsys):
    states, match, _ = fake_inner_match
    def fail(**kwargs):
        raise RuntimeError("postgresql://user:secret@database-host/")
    monkeypatch.setattr(match, "historical_match_summary", fail)
    assert app._match_backfill_worker_unleased(False) == "FAILED"
    assert any(x.get("state") == "FAILED" for x in states)
    assert "secret" not in capsys.readouterr().out
    assert not app._HEAVY_WORK_LOCK.locked()


def test_internal_quota_exception_is_propagated(fake_inner_match,monkeypatch):
    states, match, _ = fake_inner_match
    class SyntheticQuotaBoundary(Exception):
        pass
    def fail(**kwargs):
        raise SyntheticQuotaBoundary("source limit reached")
    monkeypatch.setattr(match,"historical_match_summary",fail)
    assert app._match_backfill_worker_unleased(False) == "WAITING_QUOTA"
    assert any(x.get("state") == "WAITING_QUOTA" for x in states)


def test_inner_memory_guard_hold_returns_waiting_memory(monkeypatch):
    states = []
    monkeypatch.setattr(app, "_HEAVY_WORK_LOCK", threading.Lock())
    monkeypatch.setattr(app, "_set_match_backfill_state",
                        lambda **v: states.append(dict(v)))
    monkeypatch.setattr(app.memory_guard,"snapshot",
                        lambda **k: {
                            "guard_ok": False,"heavy_work_ok": False,
                            "rss_mib": 200.0, "soft_limit_mib": 112,
                        })
    assert app._match_backfill_worker_unleased(False) == "WAITING_MEMORY"
    assert states[-1]["state"] == "WAITING_MEMORY"
    assert not app._HEAVY_WORK_LOCK.locked()


@pytest.mark.parametrize(("match_outcome","expected_exit"),[
    ("COMPLETE",0),("PARTIAL",78),("WAITING_QUOTA",73),
    ("WAITING_STORAGE",74),("WAITING_MEMORY",75),
    ("LEASE_HELD",77),("FAILED",1),(None,1),("UNKNOWN",1),
])
def test_disposable_match_child_maps_real_state_to_exit_code(
    monkeypatch, match_outcome, expected_exit,
):
    # The isolated worker is invoked with a fake executable environment and
    # no source, SQL or network. Match state is the only changing dimension.
    monkeypatch.setattr(child,"_prepare_environment",lambda: None)
    monkeypatch.setattr(child,"_deprioritize",lambda: None)
    monkeypatch.setattr(child,"_start_parent_watchdog",lambda: None)
    monkeypatch.setattr(child,"_worker_slot",lambda **kwargs: nullcontext())
    monkeypatch.setattr(memory_guard,"apply_default_process_tuning",lambda:None)
    monkeypatch.setattr(memory_guard,"wait_for_heavy_work_budget",lambda **kw:True)
    monkeypatch.setattr(memory_guard,"snapshot",
                        lambda **kw: {"cgroup_oom_group": 0})
    monkeypatch.setattr(app,"initialize_backend",lambda **kw:True)
    monkeypatch.setattr(app,"_match_backfill_worker",lambda legacy:match_outcome)
    monkeypatch.setattr(app,"_run_recent_collection_once",lambda **kw:
                        (_ for _ in ()).throw(AssertionError("unexpected source run")))
    assert child.main(["match"]) == expected_exit


@pytest.mark.parametrize(("code","result"),[
    (0,"IDLE"),(70,"WAITING_STORAGE"),(73,"WAITING_QUOTA"),
    (74,"WAITING_STORAGE"),(75,"WAITING_MEMORY"),(77,"LEASE_HELD"),
    (78,"PARTIAL"),(1,"FAILED"),
])
def test_parent_reconciles_match_exit_code(monkeypatch,code,result):
    seen=[]
    monkeypatch.setattr(app,"_set_match_backfill_state",
                        lambda **kw:seen.append(dict(kw)))
    app._isolated_match_worker_exit_state("match",code)
    assert seen[-1]["state"] == result
    assert seen[-1]["last_result_status"] == result
