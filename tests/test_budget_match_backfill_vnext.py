from contextlib import contextmanager

import budget_match_backfill_vnext as backfill
import vnext_store


def test_2025_backfill_plan_is_conditional_and_resume_aware():
    summary = {
        "expand_2025_recommended": True,
        "expansion_reasons": ["BUDGET_PROJECT_SAMPLE_SMALL"],
    }
    plan = backfill.build_2025_backfill_plan(summary)

    assert plan["needed"] is True
    assert plan["shopping_start"] == "2025-01-01"
    assert plan["shopping_end"] == "2025-12-31"
    assert plan["shopping_next_date"] == "2025-01-01"
    assert plan["shopping_complete_days"] == 0
    assert plan["budget_snapshot"] == "2025-12-31"
    assert plan["budget_complete"] is False
    assert plan["generic_historical_mode_opened"] is False

    vnext_store.save_checkpoint(
        "shopping_delivery",
        "match-backfill:2025:2025-01-01",
        status="COMPLETE",
    )
    vnext_store.save_checkpoint(
        "budget",
        "history:2025:2025-12-31",
        status="COMPLETE",
    )
    resumed = backfill.build_2025_backfill_plan(summary)

    assert resumed["shopping_complete_days"] == 1
    assert resumed["shopping_next_date"] == "2025-01-02"
    assert resumed["budget_complete"] is True


def test_2025_backfill_skips_source_when_2026_evidence_is_sufficient():
    result = backfill.run_2025_backfill({
        "expand_2025_recommended": False,
        "expansion_reasons": [],
    })

    assert result["status"] == "SKIPPED_EVIDENCE_SUFFICIENT"
    assert result["source_traffic"] is False
    assert result["before"]["needed"] is False


def test_2025_backfill_uses_global_exclusive_lease_and_bounded_parts(monkeypatch):
    lease_calls = []

    @contextmanager
    def fake_lease(name, shared=False):
        lease_calls.append((name, shared))
        yield True

    monkeypatch.setattr(
        backfill.g2b_database,
        "operational_cycle_lease",
        fake_lease,
    )
    monkeypatch.setattr(
        backfill,
        "_run_budget_snapshot",
        lambda max_pages: {
            "status": "COMPLETE",
            "complete": True,
            "max_pages": max_pages,
        },
    )
    monkeypatch.setattr(
        backfill,
        "_run_shopping_days",
        lambda max_days, max_pages: [{
            "status": "COMPLETE",
            "complete": True,
            "source_date": "2025-01-01",
            "max_days": max_days,
            "max_pages": max_pages,
        }],
    )

    plans = [
        {
            "year": 2025,
            "needed": True,
            "shopping_complete": False,
            "budget_complete": False,
        },
        {
            "year": 2025,
            "needed": True,
            "shopping_complete": False,
            "budget_complete": True,
        },
    ]
    monkeypatch.setattr(
        backfill,
        "build_2025_backfill_plan",
        lambda summary=None: plans.pop(0),
    )

    result = backfill.run_2025_backfill(
        {"expand_2025_recommended": True},
        shopping_days=7,
        shopping_max_pages=40,
        budget_max_pages=100,
    )

    assert lease_calls == [("g2b_v41_operational_cycle", False)]
    assert result["status"] == "PARTIAL"
    assert result["source_traffic"] is True
    assert result["budget"]["max_pages"] == 100
    assert result["shopping"][0]["max_days"] == 7
    assert result["shopping"][0]["max_pages"] == 40


def test_2025_backfill_reports_lease_held_without_source_calls(monkeypatch):
    @contextmanager
    def held_lease(name, shared=False):
        yield False

    monkeypatch.setattr(
        backfill.g2b_database,
        "operational_cycle_lease",
        held_lease,
    )
    result = backfill.run_2025_backfill({
        "expand_2025_recommended": True,
        "expansion_reasons": ["HIGH_MATCH_SAMPLE_SMALL"],
    })

    assert result["status"] == "LEASE_HELD"
    assert result["source_traffic"] is False
