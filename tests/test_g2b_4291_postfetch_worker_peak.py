"""G2B V4.1.291: post-fetch memory holds and child memory telemetry.

All tests are source-free: no live G2B/LOFIN, production PostgreSQL or reset.
"""
import inspect
import json
from contextlib import nullcontext

import pytest

import budget_pg_collection
import g2b_heavy_worker as worker
import memory_guard
import safe_boot_vnext
import vnext_clean_app as app
import vnext_collection


def _container(effective, current):
    return {
        "source": "cgroup-v2", "limit_mib": 256,
        "current_mib": current, "peak_mib": current,
        "effective_mib": effective, "wait_threshold_mib": 208,
        "block_threshold_mib": 224,
        "wait_ok": effective < 208, "blocked": effective >= 224,
        "oom_group": 0, "events": {"oom_kill": 0},
    }


def test_source_profile_tracks_only_existing_memory_samples(monkeypatch):
    monkeypatch.setenv("G2B_ISOLATED_HEAVY_WORKER", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 52.0)
    monkeypatch.setattr(memory_guard, "soft_limit_mib", lambda: 112)
    states = iter([_container(135, 147), _container(192, 204)])
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: next(states))
    monkeypatch.setattr(memory_guard, "_ISOLATED_WORKER_MEMORY_OBSERVATIONS", {
        "samples": 0, "sampled_peak_rss_mib": 0.0,
        "sampled_peak_cgroup_current_mib": 0.0,
        "sampled_peak_cgroup_effective_mib": 0.0,
        "sampled_cgroup_limit_mib": 0.0, "last_oom_kill_count": 0,
    })
    assert memory_guard.snapshot(collect=False)["guard_ok"]
    assert memory_guard.snapshot(collect=False)["guard_ok"]
    result = memory_guard.isolated_worker_memory_profile()
    assert result["samples"] == 2
    assert result["sampled_peak_rss_mib"] == 52
    assert result["sampled_peak_cgroup_effective_mib"] == 192
    assert result["sampled_peak_cgroup_current_mib"] == 204
    assert result["sampled_cgroup_limit_mib"] == 256
    assert result["rss_peak_mib"] > 0


def test_web_snapshot_cannot_record_worker_sample(monkeypatch):
    monkeypatch.delenv("G2B_ISOLATED_HEAVY_WORKER", raising=False)
    observations = {
        "samples": 0, "sampled_peak_rss_mib": 0.0,
        "sampled_peak_cgroup_current_mib": 0.0,
        "sampled_peak_cgroup_effective_mib": 0.0,
        "sampled_cgroup_limit_mib": 0.0, "last_oom_kill_count": 0,
    }
    monkeypatch.setattr(memory_guard, "_ISOLATED_WORKER_MEMORY_OBSERVATIONS", observations)
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 80.0)
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: _container(100, 115))
    memory_guard.snapshot(collect=False)
    assert observations["samples"] == 0


@pytest.mark.parametrize(("module", "response_marker", "transaction_marker"), [
    (vnext_collection, "items, reported = source_result", "with connect() as conn:"),
    (budget_pg_collection, "rows, reported = source_result", "with engine.begin() as conn:"),
])
def test_post_response_checkpoint_precedes_hashes_and_transaction(
    module, response_marker, transaction_marker
):
    text = inspect.getsource(module.collect_pages)
    response = text.index(response_marker)
    check = text.index("_memory_checkpoint()", response)
    hashing = text.index("keys = [" if module is budget_pg_collection else "rowkeys = [", response)
    transaction = text.index(transaction_marker, response)
    assert response < check < hashing < transaction


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_worker_profile_is_reported_for_storage_hold_without_source_io(
    monkeypatch, capsys, mode
):
    monkeypatch.setattr(worker, "_prepare_environment", lambda: None)
    monkeypatch.setattr(worker, "_deprioritize", lambda: None)
    monkeypatch.setattr(worker, "_start_parent_watchdog", lambda: None)
    monkeypatch.setattr(worker, "_worker_slot", lambda **kwargs: nullcontext())
    monkeypatch.setattr(worker, "_source_only_candidate", lambda *args: False)
    monkeypatch.setattr(memory_guard, "apply_default_process_tuning", lambda: None)
    monkeypatch.setattr(memory_guard, "snapshot", lambda **kwargs: {"cgroup_oom_group": 0})
    monkeypatch.setattr(memory_guard, "wait_for_heavy_work_budget", lambda **kwargs: None)
    monkeypatch.setattr(app, "initialize_backend", lambda force=False: False)
    monkeypatch.setattr(memory_guard, "isolated_worker_memory_profile", lambda: {
        "rss_peak_mib": 54.3, "samples": 3,
        "sampled_peak_cgroup_effective_mib": 140.2,
    })
    assert worker.main([mode]) == 70
    lines = capsys.readouterr().out.splitlines()
    reports = [line for line in lines
               if line.startswith(f"G2B_SOURCE_WORKER_MEMORY {mode} backend_init ")]
    assert len(reports) == 1
    result = json.loads(reports[0].split(" ", 3)[3])
    assert result["rss_peak_mib"] == 54.3
    assert result["samples"] == 3


def test_parent_guard_still_rejects_existing_cafe24_256mib_headroom():
    result = safe_boot_vnext.child_admission({
        "guard_ok": True, "cgroup_oom_group": 0,
        "cgroup_effective_mib": 113.3, "cgroup_limit_mib": 256,
    }, worker_soft_limit_mib=112)
    assert result == (False, "CGRP_CHILD_HEADROOM_UNSAFE")
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64


def test_shopping_post_fetch_pressure_does_not_commit_unfinished_page(monkeypatch):
    """The SECOND guard runs after API response; retry fetches that page only."""
    import db
    import shopping_vnext
    import vnext_store

    calls = []
    checks = []

    def fetch(start, end, page=1, rows=999):
        calls.append(page)
        assert (start, end, page, rows) == (
            "2026-10-08", "2026-10-08", 1, 1
        )
        return [{
            "dlvrReqNo": "POSTFETCH-4291", "dlvrReqChgOrd": "0",
            "prdctSno": "1", "dlvrReqRcptDate": "20261008",
            "dtilPrdctClsfcNo": "3911160302", "prdctNm": "LED 가로등기구",
        }], 1

    def guard():
        checks.append("memory")
        if len(checks) == 2:
            raise memory_guard.MemoryPressureError("SOURCE_RESPONSE_GUARD_HOLD")

    monkeypatch.setattr(shopping_vnext, "fetch_page", fetch)
    monkeypatch.setattr(vnext_collection, "_memory_checkpoint", guard)
    with pytest.raises(memory_guard.MemoryPressureError):
        shopping_vnext.collect_all(
            "2026-10-08", "2026-10-08", page_size=1, max_pages=1
        )
    cp = vnext_store.get_checkpoint(
        "shopping_delivery", "2026-10-08:2026-10-08"
    )
    assert cp["status"] == "INCOMPLETE"
    assert cp["fetched_count"] == cp["saved_count"] == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM shopping_records").fetchone()[0] == 0

    monkeypatch.setattr(vnext_collection, "_memory_checkpoint", lambda: None)
    resumed = shopping_vnext.collect_all(
        "2026-10-08", "2026-10-08", page_size=1, max_pages=1, resume=True
    )
    assert resumed["complete"] is True
    assert resumed["fetched"] == resumed["saved"] == 1
    assert calls == [1, 1]


def test_budget_post_fetch_pressure_does_not_commit_unfinished_page(monkeypatch, tmp_path):
    """LOFIN identical post-response gate preserves original PostgreSQL ABI."""
    import budget_pg_store

    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'postfetch-budget.sqlite3'}"
    )
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(name, raising=False)
    budget_pg_store.reset_engine_cache()
    page_calls = []
    checks = []

    def fetch(page, size):
        page_calls.append(page)
        return ([{"fyr": "2026", "dbiz_cd": "POSTFETCH-4291", "amount": 100}], 1)

    def collect(*, resume=True):
        return budget_pg_collection.collect_pages(
            dataset="budget", scope="2026:2026-10-08",
            range_start="2026", range_end="2026-10-08",
            page_size=1, max_pages=1, resume=resume, fetch=fetch,
            identity=lambda row: row["dbiz_cd"],
            source_system="LOFIN", source_operation="QWGJK",
            source_date=lambda row: "2026-10-08",
            checkpoint_contract="V4291_POSTFETCH",
        )

    def guard():
        checks.append("memory")
        if len(checks) == 2:
            raise memory_guard.MemoryPressureError("SOURCE_RESPONSE_GUARD_HOLD")

    monkeypatch.setattr(budget_pg_collection, "_memory_checkpoint", guard)
    with pytest.raises(memory_guard.MemoryPressureError):
        collect(resume=False)
    cp = budget_pg_store.get_checkpoint("budget", "2026:2026-10-08")
    assert cp["status"] == "INCOMPLETE"
    assert cp["fetched_count"] == cp["saved_count"] == 0
    counts = budget_pg_store.dataset_counts("budget")
    assert counts["current_records"] == counts["observations"] == 0

    monkeypatch.setattr(budget_pg_collection, "_memory_checkpoint", lambda: None)
    completed = collect(resume=True)
    assert completed["complete"] is True
    assert completed["fetched"] == completed["saved"] == 1
    assert page_calls == [1, 1]
