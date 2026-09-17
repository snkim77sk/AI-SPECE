import datetime as dt
import json

import pytest

import budget_snapshot_vnext
import historical_vnext
import vnext_live_gate


NOW = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone.utc)


def approval(*, generated=None, sha="synthetic-sha"):
    return {
        "approval_version": 1,
        "source_commit_sha": sha,
        "production_db_touched": False,
        "bulk_collection_attempted": False,
        "live_allowed_for_this_invocation": True,
        "generated_at_utc": (generated or (NOW - dt.timedelta(minutes=5))).isoformat(),
        "g2b": {"status": "CONCLUSIVE", "live_request_attempted": True},
        "budget": {
            "status": "SCHEMA_PASS",
            "schema_verified": True,
            "live_request_attempted": True,
        },
        "all_sample_schemas_verified": True,
        "whole_source_completeness_verified": False,
    }


def test_valid_recent_sanitized_approval_passes_without_exposing_extra_values(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    report = approval()
    report["synthetic_secret_that_must_not_escape"] = "DO_NOT_RETURN"
    result = vnext_live_gate.require_canary_approval(report, now=NOW)
    assert result["g2b_status"] == "CONCLUSIVE"
    assert result["budget_status"] == "SCHEMA_PASS"
    assert result["source_commit_sha"] == "synthetic-sha"
    assert "DO_NOT_RETURN" not in repr(result)


def test_approval_can_be_loaded_from_sanitized_report_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    path = tmp_path / "canary.json"
    path.write_text(json.dumps(approval()), encoding="utf-8")
    assert vnext_live_gate.require_canary_approval(path, now=NOW)["approval_version"] == 1


@pytest.mark.parametrize("mutator,reason", [
    (lambda r: r.update(approval_version=0), "CANARY_APPROVAL_VERSION_MISMATCH"),
    (lambda r: r.update(production_db_touched=True), "CANARY_APPROVAL_PRODUCTION_DB_UNSAFE"),
    (lambda r: r.update(bulk_collection_attempted=True), "CANARY_APPROVAL_BULK_UNSAFE"),
    (lambda r: r.update(live_allowed_for_this_invocation=False), "CANARY_APPROVAL_NOT_LIVE"),
    (lambda r: r["g2b"].update(status="BLOCKED", live_request_attempted=False), "CANARY_APPROVAL_G2B_NOT_CONCLUSIVE"),
    (lambda r: r["budget"].update(status="BLOCKED", schema_verified=False, live_request_attempted=False), "CANARY_APPROVAL_BUDGET_NOT_VERIFIED"),
    (lambda r: r.update(all_sample_schemas_verified=False), "CANARY_APPROVAL_SAMPLE_GATE_FAILED"),
    (lambda r: r.update(source_commit_sha=""), "CANARY_APPROVAL_SOURCE_SHA_MISSING"),
])
def test_unsafe_or_partial_approval_is_rejected(monkeypatch, mutator, reason):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    report = approval()
    mutator(report)
    with pytest.raises(vnext_live_gate.LiveApprovalError, match=reason):
        vnext_live_gate.require_canary_approval(report, now=NOW)


def test_approval_expires_after_24_hours(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    report = approval(generated=NOW - dt.timedelta(hours=24, seconds=1))
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_EXPIRED"):
        vnext_live_gate.require_canary_approval(report, now=NOW)


def test_github_execution_requires_exact_canary_commit(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "expected-sha")
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_SOURCE_SHA_MISMATCH"):
        vnext_live_gate.require_canary_approval(approval(sha="other-sha"), now=NOW)
    assert vnext_live_gate.require_canary_approval(
        approval(sha="expected-sha"), now=NOW
    )["source_commit_sha"] == "expected-sha"


def test_historical_live_true_without_approval_stops_before_any_runner(monkeypatch):
    called = []
    monkeypatch.setattr(historical_vnext, "STAGES", ((
        "bid_notice_goods", lambda *a, **k: called.append("NETWORK")
    ),))
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_REQUIRED"):
        historical_vnext.run_backfill(
            "2026-09-01", "2026-09-01", allow_live=True, canary_approval=None,
        )
    assert called == []


def test_budget_live_true_without_approval_stops_before_collection(monkeypatch):
    called = []
    monkeypatch.setattr(
        budget_snapshot_vnext.budget_vnext,
        "collect_full_budget",
        lambda *a, **k: called.append("NETWORK"),
    )
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_REQUIRED"):
        budget_snapshot_vnext.run_snapshots(
            ["2026-09-01"], allow_live=True, canary_approval=None,
        )
    assert called == []
