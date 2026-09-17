import datetime as dt

import pytest

import budget_snapshot_vnext
import historical_vnext
import vnext_live_gate


NOW = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone.utc)


def small_report(*, sha="runtime-sha", generated=None, complete=True):
    return {
        "small_validation_approval_version": 1,
        "source_commit_sha": sha,
        "generated_at_utc": (generated or (NOW - dt.timedelta(minutes=5))).isoformat(),
        "validation_only": True,
        "validation_scope": "one recent completed KST date only",
        "production_db_touched": False,
        "db_artifact_exported": False,
        "date_kst": "2026-09-16",
        "max_pages_per_stage": 2,
        "requested_validation_scope_complete": complete,
        "whole_source_completeness_verified": False,
    }


def _runtime(monkeypatch, sha="runtime-sha"):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv("G2B_VNEXT_SOURCE_COMMIT_SHA", sha)


def test_recent_same_commit_small_validation_approval_passes(monkeypatch):
    _runtime(monkeypatch)
    result = vnext_live_gate.require_small_validation_approval(small_report(), now=NOW)
    assert result["source_commit_sha"] == "runtime-sha"
    assert result["requested_validation_scope_complete"] is True
    assert result["date_kst"] == "2026-09-16"


@pytest.mark.parametrize("mutator,reason", [
    (lambda r: r.update(small_validation_approval_version=0), "SMALL_VALIDATION_APPROVAL_VERSION_MISMATCH"),
    (lambda r: r.update(validation_only=False), "SMALL_VALIDATION_APPROVAL_SCOPE_INVALID"),
    (lambda r: r.update(production_db_touched=True), "SMALL_VALIDATION_APPROVAL_PRODUCTION_DB_UNSAFE"),
    (lambda r: r.update(db_artifact_exported=True), "SMALL_VALIDATION_APPROVAL_DB_EXPORT_UNSAFE"),
    (lambda r: r.update(requested_validation_scope_complete=False), "SMALL_VALIDATION_APPROVAL_SCOPE_INCOMPLETE"),
    (lambda r: r.update(whole_source_completeness_verified=True), "SMALL_VALIDATION_APPROVAL_WHOLE_SOURCE_CLAIM_INVALID"),
    (lambda r: r.update(validation_scope="all history"), "SMALL_VALIDATION_APPROVAL_SCOPE_INVALID"),
    (lambda r: r.update(max_pages_per_stage=3), "SMALL_VALIDATION_APPROVAL_PAGE_BUDGET_INVALID"),
    (lambda r: r.update(date_kst="not-a-date"), "SMALL_VALIDATION_APPROVAL_DATE_INVALID"),
])
def test_invalid_small_validation_approval_is_rejected(monkeypatch, mutator, reason):
    _runtime(monkeypatch)
    report = small_report()
    mutator(report)
    with pytest.raises(vnext_live_gate.LiveApprovalError, match=reason):
        vnext_live_gate.require_small_validation_approval(report, now=NOW)


def test_small_validation_approval_expires_and_must_match_runtime(monkeypatch):
    _runtime(monkeypatch)
    expired = small_report(generated=NOW - dt.timedelta(hours=24, seconds=1))
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="SMALL_VALIDATION_APPROVAL_EXPIRED"):
        vnext_live_gate.require_small_validation_approval(expired, now=NOW)
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="SMALL_VALIDATION_APPROVAL_SOURCE_SHA_MISMATCH"):
        vnext_live_gate.require_small_validation_approval(small_report(sha="other-sha"), now=NOW)


def test_general_historical_requires_small_validation_before_any_runner(monkeypatch):
    calls = []
    monkeypatch.setattr(historical_vnext, "require_canary_approval", lambda value: {"canary": True})
    monkeypatch.setattr(historical_vnext, "STAGES", ((
        "bid_notice_goods", lambda *a, **k: calls.append("NETWORK")
    ),))
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="SMALL_VALIDATION_APPROVAL_REQUIRED"):
        historical_vnext.run_backfill(
            "2026-09-01", "2026-09-02",
            allow_live=True, canary_approval={"synthetic": True},
            small_validation_approval=None,
        )
    assert calls == []


def test_historical_validation_mode_is_hard_bounded(monkeypatch):
    monkeypatch.setattr(historical_vnext, "require_canary_approval", lambda value: {"canary": True})
    with pytest.raises(RuntimeError, match="SMALL_VALIDATION_BOUNDS_REQUIRED"):
        historical_vnext.run_backfill(
            "2026-09-01", "2026-09-02", chunk_days=1,
            allow_live=True, canary_approval={}, validation_mode=True,
            max_pages_per_stage=1,
        )
    with pytest.raises(RuntimeError, match="SMALL_VALIDATION_BOUNDS_REQUIRED"):
        historical_vnext.run_backfill(
            "2026-09-01", "2026-09-01", chunk_days=1,
            allow_live=True, canary_approval={}, validation_mode=True,
            max_pages_per_stage=3,
        )


def test_general_budget_snapshots_require_small_validation_before_collection(monkeypatch):
    called = []
    monkeypatch.setattr(budget_snapshot_vnext, "require_canary_approval", lambda value: {"canary": True})
    monkeypatch.setattr(
        budget_snapshot_vnext.budget_vnext,
        "collect_full_budget",
        lambda *a, **k: called.append("NETWORK"),
    )
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="SMALL_VALIDATION_APPROVAL_REQUIRED"):
        budget_snapshot_vnext.run_snapshots(
            ["2026-09-01"], allow_live=True, canary_approval={},
            small_validation_approval=None,
        )
    assert called == []


def test_budget_validation_mode_is_one_snapshot_and_two_pages_max(monkeypatch):
    monkeypatch.setattr(budget_snapshot_vnext, "require_canary_approval", lambda value: {"canary": True})
    with pytest.raises(RuntimeError, match="SMALL_VALIDATION_BOUNDS_REQUIRED"):
        budget_snapshot_vnext.run_snapshots(
            ["2026-09-01", "2026-09-02"], allow_live=True,
            canary_approval={}, validation_mode=True, max_pages_per_snapshot=1,
        )
    with pytest.raises(RuntimeError, match="SMALL_VALIDATION_BOUNDS_REQUIRED"):
        budget_snapshot_vnext.run_snapshots(
            ["2026-09-01"], allow_live=True,
            canary_approval={}, validation_mode=True, max_pages_per_snapshot=3,
        )
