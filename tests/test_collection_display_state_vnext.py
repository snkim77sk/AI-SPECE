"""Execution display must not reinterpret a resumable checkpoint as a dead worker."""
import copy
import datetime as dt

import pytest

import budget_vnext
import collection_monitor_vnext as monitor
import db
import vnext_clean_app as app
from vnext_store import get_checkpoint, save_checkpoint


NOW = dt.datetime(2026, 10, 20, 0, 0, tzinfo=dt.timezone.utc)
SCOPE = "2026:2026-10-19"


def _budget(snapshot):
    return next(row for row in snapshot["stages"] if row["dataset"] == "budget")


def _paused_snapshot():
    # A bounded 16-page batch preserves RUNNING for receipt-verified resume.
    save_checkpoint(
        "budget", SCOPE, page_no=17, page_size=1000,
        source_total=478000, fetched_count=16000, saved_count=16000,
        status="RUNNING",
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE collection_checkpoints SET updated_at=? WHERE dataset=? AND scope_key=?",
            ("2026-10-19T23:50:00+00:00", "budget", SCOPE),
        )
    return monitor.monitor_snapshot(now=NOW)


def test_bounded_batch_exit_displays_partial_and_preserves_checkpoint():
    snapshot = _paused_snapshot()
    before = dict(get_checkpoint("budget", SCOPE))
    assert _budget(snapshot)["state"] == "STALE"
    result = app._apply_runtime_wait_states(
        snapshot,
        {"budget_run_state": app._component_run_state("RUNNING"), "auto_sync_enabled": True},
        {},
    )

    stage = _budget(result)
    assert stage["state"] == "PARTIAL"
    assert stage["checkpoint_state"] == "STALE"
    assert stage["checkpoint_status"] == "RUNNING"
    assert stage["pages_processed"] == 16
    assert "자동 재개 대기" in stage["message"]
    recent = next(row for row in result["recent_activity"] if row["scope"] == SCOPE)
    assert recent["status"] == "RUNNING"
    assert recent["display_state"] == "PARTIAL"
    assert recent["display_state_label"] == stage["state_label"]
    assert result["summary"]["running"] == 0
    assert result["summary"]["errors"] == 0
    assert result["source_io_performed"] is False
    assert dict(get_checkpoint("budget", SCOPE)) == before
    assert _budget(snapshot)["state"] == "STALE"


@pytest.mark.parametrize("running", [False, True])
def test_execution_requires_live_worker_evidence(running):
    snapshot = _paused_snapshot()
    result = app._apply_runtime_wait_states(
        snapshot,
        {"budget_run_state": "RUNNING", "manual_budget_running": running},
        {},
    )
    expected = "RUNNING" if running else "STALE"
    assert _budget(result)["state"] == expected
    assert result["summary"]["running"] == int(running)
    recent = next(row for row in result["recent_activity"] if row["scope"] == SCOPE)
    assert recent["status"] == "RUNNING"
    assert recent["display_state"] == expected


@pytest.mark.parametrize("runtime_state", [
    "PARTIAL", "WAITING_MEMORY", "WAITING_QUOTA", "REAP_PENDING",
    "REAPER_RECOVERY", "WORKER_STATUS_UNKNOWN", "QUEUED",
])
def test_runtime_display_keeps_completed_and_historical_evidence(runtime_state):
    snapshot = _paused_snapshot()
    complete = next(row for row in snapshot["stages"] if row["dataset"] == "budget_appropriation")
    complete.update(state="COMPLETE", state_label="완료", scope="2027:ALL")
    historical = {
        "dataset": "budget", "scope": "history:2026:2026-01-02",
        "status": "RUNNING", "display_state": "STALE",
        "display_state_label": "갱신중단",
    }
    snapshot["recent_activity"].append(historical)
    before = copy.deepcopy(snapshot)
    result = app._apply_runtime_wait_states(
        snapshot, {"budget_run_state": runtime_state, "auto_sync_enabled": False},
        {"budget": {"used": 500, "limit": 500}},
    )

    assert _budget(result)["state"] == runtime_state
    assert result["summary"]["errors"] == 0
    complete = next(row for row in result["stages"] if row["dataset"] == "budget_appropriation")
    assert complete["state"] == "COMPLETE"
    assert result["recent_activity"][-1] == historical
    assert snapshot == before
    if runtime_state == "PARTIAL":
        assert "자동 재개" not in _budget(result)["message"]


@pytest.mark.parametrize("runtime_state", [None, "PARTIAL", "RUNNING"])
def test_partition_freshness_and_runtime_display_keep_raw_status(monkeypatch, runtime_state):
    monkeypatch.setattr(budget_vnext, "operational_region_partition_plan", lambda year: {
        "ready": True, "region_codes": ["1100000", "2800000"],
        "region_names": {"1100000": "서울특별시", "2800000": "인천광역시"},
        "region_count": 2, "minimum_regions": 2,
    })
    scopes = [
        {"scope_key": SCOPE + ":1100000", "status": "COMPLETE",
         "updated_at": "2026-10-19T23:40:00+00:00"},
        {"scope_key": SCOPE + ":2800000", "status": "RUNNING",
         "updated_at": "2026-10-19T23:50:00+00:00", "page_no": 17,
         "page_size": 1000, "source_total": 478000, "saved_count": 16000},
    ]
    before = copy.deepcopy(scopes)
    stage, _ = monitor._budget_stage(monitor.STAGES[1], {"scopes": scopes}, NOW)
    recent = monitor._checkpoint_display(scopes[-1], NOW)

    assert stage["state"] == recent["display_state"] == "STALE"
    assert stage["partition_active_state"] == "RUNNING"
    assert stage["partition_active_display_state"] == "STALE"
    assert stage["checkpoint_scope"] == scopes[-1]["scope_key"]
    assert "체크포인트 5분 이상 미갱신" in stage["message"]
    assert scopes == before
    if runtime_state is not None:
        result = app._apply_runtime_wait_states(
            {"stages": [stage], "recent_activity": [{
                "dataset": "budget", "scope": scopes[-1]["scope_key"],
                "status": "RUNNING", **recent,
            }]},
            {"budget_run_state": runtime_state, "manual_budget_running": runtime_state == "RUNNING"},
            {},
        )
        updated = _budget(result)
        assert updated["state"] == updated["partition_state"] == runtime_state
        assert updated["partition_active_display_state"] == runtime_state
        assert updated["partition_message"] == updated["message"]
        assert updated["partition_active_state"] == "RUNNING"
        assert result["recent_activity"][0]["display_state"] == runtime_state
        assert result["recent_activity"][0]["status"] == "RUNNING"
        assert stage["partition_state"] == "STALE"


def test_desktop_and_mobile_render_display_state_without_raw_status_loss(monkeypatch):
    snapshot = app._apply_runtime_wait_states(
        _paused_snapshot(), {"budget_run_state": "PARTIAL", "auto_sync_enabled": True}, {},
    )
    monkeypatch.setattr(app, "require_user", lambda request: {"username": "fixture"})
    monkeypatch.setattr(app, "is_result_server", lambda: True)
    monkeypatch.setattr(app, "_runtime_collection_snapshot", lambda: snapshot)
    monkeypatch.setattr(app, "match_backfill_status", lambda: {})
    monkeypatch.setattr(app.g2b_idle_memory_recovery, "recovery_status", lambda: {})
    monkeypatch.setattr(app, "layout", lambda title, body, *args, **kwargs: body)
    html = app.collection_monitor_page(None)

    assert "stage-state running'>부분완료</span>" in html
    assert "collection-activity-status running'>부분완료</span>" in html
    assert "stage-state running'>실행중</span>" not in html
    assert "자동수집 전체가 멈췄다는 판정이 아닙니다" in html
    assert get_checkpoint("budget", SCOPE)["status"] == "RUNNING"
