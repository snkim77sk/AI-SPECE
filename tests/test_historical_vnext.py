import datetime as dt

import pytest

import historical_vnext


def test_chunks_are_gapless_non_overlapping_and_within_safe_window():
    chunks = list(historical_vnext.iter_date_chunks("2026-08-25", "2026-09-16", chunk_days=7))
    assert [(c.start_date, c.end_date) for c in chunks] == [
        ("2026-08-25", "2026-08-31"),
        ("2026-09-01", "2026-09-07"),
        ("2026-09-08", "2026-09-14"),
        ("2026-09-15", "2026-09-16"),
    ]
    for left, right in zip(chunks, chunks[1:]):
        left_end = dt.date.fromisoformat(left.end_date)
        right_start = dt.date.fromisoformat(right.start_date)
        assert right_start == left_end + dt.timedelta(days=1)


def test_chunk_days_fail_closed_above_safe_limit():
    with pytest.raises(ValueError):
        list(historical_vnext.iter_date_chunks("2026-01-01", "2026-02-01", chunk_days=29))


def test_build_plan_has_fixed_full_date_range_stage_order():
    plan = historical_vnext.build_plan("2026-09-01", "2026-09-16", chunk_days=7)
    assert plan["stages"] == ["shopping_delivery"]
    assert plan["chunk_count"] == 3
    assert plan["stage_count"] == 1
    assert plan["planned_stage_calls"] == 3


def test_run_backfill_is_locked_without_explicit_live_unlock():
    with pytest.raises(RuntimeError, match="locked until canary verification"):
        historical_vnext.run_backfill("2026-09-01", "2026-09-02")


def test_audit_marks_checkpoint_complete_only_when_fetched_covers_source(monkeypatch):
    def fake_checkpoint(dataset, scope):
        if dataset == "shopping_delivery":
            return {"status": "COMPLETE", "source_total": 10, "fetched_count": 9, "saved_count": 9, "page_no": 1, "last_error": ""}
        return None

    monkeypatch.setattr(historical_vnext, "get_checkpoint", fake_checkpoint)
    audit = historical_vnext.audit_backfill("2026-09-16", "2026-09-16")
    assert audit["expected_units"] == 1
    assert audit["complete_units"] == 0
    assert audit["all_complete"] is False
    rows = {r["dataset"]: r for r in audit["records"]}
    assert rows["shopping_delivery"]["complete"] is False


def test_run_backfill_stops_on_partial_checkpoint(monkeypatch):
    calls = []

    def fake_status(dataset, chunk):
        if calls and dataset == "shopping_delivery":
            return {"dataset": dataset, "scope": chunk.scope, "status": "RUNNING", "complete": False,
                    "receipt_complete": False, "stability_verified": False,
                    "source_total": 2000, "fetched_count": 999, "saved_count": 999, "page_no": 2, "last_error": ""}
        return {"dataset": dataset, "scope": chunk.scope, "status": "NOT_STARTED", "complete": False,
                "receipt_complete": False, "stability_verified": False,
                "source_total": 0, "fetched_count": 0, "saved_count": 0, "page_no": 0, "last_error": ""}

    def first_runner(start, end, **kwargs):
        calls.append((start, end, kwargs))
        return {"complete": False}

    stages = (("shopping_delivery", first_runner),)
    monkeypatch.setattr(historical_vnext, "STAGES", stages)
    monkeypatch.setattr(historical_vnext, "checkpoint_status", fake_status)
    monkeypatch.setattr(historical_vnext, "require_canary_approval",
                        lambda value: {"synthetic_approval": True})
    # This test exercises stop-on-partial semantics with a synthetic runner. Source
    # execution-mode enforcement is covered independently in source-guard tests.
    monkeypatch.setattr(historical_vnext, "require_source_request_mode", lambda expected: expected)

    result = historical_vnext.run_backfill(
        "2026-09-16", "2026-09-16", chunk_days=1,
        allow_live=True, max_pages_per_stage=1, validation_mode=True,
    )
    assert result["complete"] is False
    assert result["stopped_on"]["dataset"] == "shopping_delivery"
    assert result["approval"] == {"synthetic_approval": True}
    assert result["expansion_approval"]["mode"] == "small_validation"
    assert len(calls) == 1
    assert calls[0][2]["max_pages"] == 1


def test_finalize_backfill_fails_closed_before_classification(monkeypatch):
    calls = []
    monkeypatch.setattr(historical_vnext, "audit_backfill", lambda *a, **k: {
        "all_complete": False, "complete_units": 0, "expected_units": 1,
    })
    monkeypatch.setattr(
        historical_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: calls.append("classify"),
    )

    with pytest.raises(RuntimeError, match="0/1 units fresh-stable"):
        historical_vnext.finalize_backfill("2026-09-01", "2026-09-16")
    assert calls == []


def test_finalize_backfill_classifies_only_shopping(monkeypatch):
    calls = []
    audit = {"all_complete": True, "complete_units": 1, "expected_units": 1}
    coverage = {
        "planned_datasets": ["shopping_delivery"],
        "trusted_units": 1,
        "current_raw_rows": 0,
        "all_current_raw_covered_by_plan": True,
    }
    monkeypatch.setattr(historical_vnext, "audit_backfill", lambda *a, **k: audit)
    monkeypatch.setattr(
        historical_vnext, "require_plan_raw_coverage", lambda value: coverage
    )
    monkeypatch.setattr(
        historical_vnext.classification_vnext,
        "classify_dataset",
        lambda dataset, **kw: (
            calls.append((dataset, kw)) or {"dataset": dataset, "classified": 0}
        ),
    )

    result = historical_vnext.finalize_backfill(
        "2026-09-01", "2026-09-16", classify_batch_size=456
    )

    assert calls == [("shopping_delivery", {"batch_size": 456})]
    assert result["audit"] is audit
    assert result["trusted_raw_coverage"] is coverage
    assert result["service_collection_removed"] is True


def test_finalize_backfill_stops_before_classification_if_raw_coverage_gate_rejects(monkeypatch):
    calls = []
    audit = {"all_complete": True, "complete_units": 1, "expected_units": 1}
    monkeypatch.setattr(historical_vnext, "audit_backfill", lambda *a, **k: audit)

    def reject(_audit):
        calls.append("coverage")
        raise RuntimeError("FINALIZE_CURRENT_RAW_OUTSIDE_PLAN")

    monkeypatch.setattr(historical_vnext, "require_plan_raw_coverage", reject)
    monkeypatch.setattr(
        historical_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: calls.append("classify"),
    )

    with pytest.raises(RuntimeError, match="FINALIZE_CURRENT_RAW_OUTSIDE_PLAN"):
        historical_vnext.finalize_backfill("2026-09-01", "2026-09-16")
    assert calls == ["coverage"]
