import datetime as dt
import json

import pytest

import budget_snapshot_vnext
import historical_vnext
import vnext_live_gate
from vnext_provenance import seal_report


NOW = dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.timezone.utc)


def _seal(report):
    return seal_report(report, purpose=vnext_live_gate.CANARY_PROVENANCE_PURPOSE)


def approval(*, generated=None, sha="synthetic-sha"):
    return _seal({
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
    })


def _local_runtime(monkeypatch, sha="synthetic-sha"):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setenv("G2B_VNEXT_SOURCE_COMMIT_SHA", sha)


def test_valid_recent_sanitized_approval_passes_without_exposing_extra_values(monkeypatch):
    _local_runtime(monkeypatch)
    report = approval()
    report["synthetic_secret_that_must_not_escape"] = "DO_NOT_RETURN"
    report = _seal(report)
    result = vnext_live_gate.require_canary_approval(report, now=NOW)
    assert result["g2b_status"] == "CONCLUSIVE"
    assert result["budget_status"] == "SCHEMA_PASS"
    assert result["source_commit_sha"] == "synthetic-sha"
    assert "DO_NOT_RETURN" not in repr(result)


def test_approval_can_be_loaded_from_sanitized_report_file(monkeypatch, tmp_path):
    _local_runtime(monkeypatch)
    path = tmp_path / "canary.json"
    path.write_text(json.dumps(approval()), encoding="utf-8")
    assert vnext_live_gate.require_canary_approval(path, now=NOW)["approval_version"] == 1


def test_unsigned_or_tampered_canary_report_is_rejected(monkeypatch):
    _local_runtime(monkeypatch)
    unsigned = approval()
    unsigned.pop("provenance")
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="PROVENANCE_REQUIRED"):
        vnext_live_gate.require_canary_approval(unsigned, now=NOW)

    tampered = approval()
    tampered["all_sample_schemas_verified"] = False
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="PROVENANCE_SIGNATURE_MISMATCH"):
        vnext_live_gate.require_canary_approval(tampered, now=NOW)


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
    _local_runtime(monkeypatch)
    report = approval()
    mutator(report)
    report = _seal(report)
    with pytest.raises(vnext_live_gate.LiveApprovalError, match=reason):
        vnext_live_gate.require_canary_approval(report, now=NOW)


def test_approval_expires_after_24_hours(monkeypatch):
    _local_runtime(monkeypatch)
    report = approval(generated=NOW - dt.timedelta(hours=24, seconds=1))
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_EXPIRED"):
        vnext_live_gate.require_canary_approval(report, now=NOW)


def test_github_execution_requires_exact_canary_commit(monkeypatch):
    monkeypatch.delenv("G2B_VNEXT_SOURCE_COMMIT_SHA", raising=False)
    monkeypatch.setenv("GITHUB_SHA", "expected-sha")
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_SOURCE_SHA_MISMATCH"):
        vnext_live_gate.require_canary_approval(approval(sha="other-sha"), now=NOW)
    assert vnext_live_gate.require_canary_approval(
        approval(sha="expected-sha"), now=NOW
    )["source_commit_sha"] == "expected-sha"


def test_non_github_live_approval_requires_explicit_runtime_commit(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("G2B_VNEXT_SOURCE_COMMIT_SHA", raising=False)
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_RUNTIME_SHA_MISSING"):
        vnext_live_gate.require_canary_approval(approval(), now=NOW)


def test_explicit_local_runtime_commit_must_match_approval(monkeypatch):
    _local_runtime(monkeypatch, "runtime-sha")
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_SOURCE_SHA_MISMATCH"):
        vnext_live_gate.require_canary_approval(approval(sha="other-sha"), now=NOW)


def test_conflicting_github_and_explicit_runtime_commit_is_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "github-sha")
    monkeypatch.setenv("G2B_VNEXT_SOURCE_COMMIT_SHA", "explicit-other-sha")
    with pytest.raises(vnext_live_gate.LiveApprovalError, match="CANARY_APPROVAL_RUNTIME_SHA_CONFLICT"):
        vnext_live_gate.require_canary_approval(approval(sha="github-sha"), now=NOW)


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
