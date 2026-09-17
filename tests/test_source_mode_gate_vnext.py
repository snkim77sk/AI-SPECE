import datetime as dt

import pytest

import budget_snapshot_vnext
import historical_vnext
import vnext_live_gate
import vnext_source_guard


def _small_context(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "s" * 40)
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 17))
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda value: {"source_commit_sha": "s" * 40},
    )
    return vnext_source_guard.small_validation_source_context(
        "synthetic-canary.json", validation_date="2026-09-16", max_requests=2
    )


def test_general_historical_cannot_reuse_small_validation_context(monkeypatch):
    network = []
    monkeypatch.setattr(historical_vnext, "require_canary_approval", lambda value: {"canary": True})
    monkeypatch.setattr(historical_vnext, "require_small_validation_approval", lambda value: {"small": True})
    monkeypatch.setattr(
        historical_vnext,
        "STAGES",
        (("bid_notice_goods", lambda *a, **k: network.append("NETWORK")),),
    )
    with _small_context(monkeypatch):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="MODE_MISMATCH"):
            historical_vnext.run_backfill(
                "2026-09-01", "2026-09-02", chunk_days=1,
                allow_live=True, canary_approval={}, small_validation_approval={},
                validation_mode=False, max_pages_per_stage=1,
            )
    assert network == []


def test_general_budget_cannot_reuse_small_validation_context(monkeypatch):
    network = []
    monkeypatch.setattr(budget_snapshot_vnext, "require_canary_approval", lambda value: {"canary": True})
    monkeypatch.setattr(budget_snapshot_vnext, "require_small_validation_approval", lambda value: {"small": True})
    monkeypatch.setattr(
        budget_snapshot_vnext.budget_vnext,
        "collect_full_budget",
        lambda *a, **k: network.append("NETWORK"),
    )
    with _small_context(monkeypatch):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="MODE_MISMATCH"):
            budget_snapshot_vnext.run_snapshots(
                ["2026-09-01"], allow_live=True, canary_approval={},
                small_validation_approval={}, validation_mode=False,
                max_pages_per_snapshot=1,
            )
    assert network == []


def test_validation_entrypoints_accept_small_validation_context(monkeypatch):
    monkeypatch.setattr(historical_vnext, "require_canary_approval", lambda value: {"canary": True})
    monkeypatch.setattr(budget_snapshot_vnext, "require_canary_approval", lambda value: {"canary": True})
    with _small_context(monkeypatch):
        canary, expansion = historical_vnext._live_approvals(
            "2026-09-01", "2026-09-01", chunk_days=1,
            max_pages_per_stage=1, canary_approval={},
            small_validation_approval=None, validation_mode=True,
        )
        assert canary == {"canary": True}
        assert expansion["mode"] == "small_validation"
        # The same active context is also the only mode accepted by budget validation.
        monkeypatch.setattr(budget_snapshot_vnext, "audit_snapshots", lambda dates: {
            "records": [{"complete": True}],
            "all_requested_snapshots_complete": True,
        })
        result = budget_snapshot_vnext.run_snapshots(
            ["2026-09-01"], allow_live=True, canary_approval={},
            validation_mode=True, max_pages_per_snapshot=1,
        )
        assert result["expansion_approval"]["mode"] == "small_validation"
