import pytest

import vnext_finalize_guard
import vnext_stability
from vnext_collection import collect_pages
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint


def _fresh_stable_unit(dataset="bid_notice_service", scope="2026-09-16:2026-09-16"):
    pages = {
        1: [{"id": "A", "bidNtceNm": "테스트 용역"}],
        2: [],
    }

    result = collect_pages(
        dataset=dataset,
        scope=scope,
        range_start="2026-09-16",
        range_end="2026-09-16",
        page_size=2,
        max_pages=3,
        resume=True,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
        source_system="TEST",
        source_operation="TEST_LIST",
        source_date=lambda row: "2026-09-16",
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
    )
    assert result["complete"] is True

    stable = vnext_stability.verify_checkpoint_source(
        dataset=dataset,
        scope=scope,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
    )
    assert stable["stable"] is True
    return {
        "all_complete": True,
        "records": [{"dataset": dataset, "scope": scope, "complete": True}],
    }


def test_plan_raw_coverage_accepts_only_current_raw_in_fresh_stable_generation():
    audit = _fresh_stable_unit()
    coverage = vnext_finalize_guard.require_plan_raw_coverage(audit)
    assert coverage["planned_datasets"] == ["bid_notice_service"]
    assert coverage["trusted_units"] == 1
    assert coverage["current_raw_rows"] == 1
    assert coverage["all_current_raw_covered_by_plan"] is True


def test_same_dataset_partial_raw_outside_audited_receipt_blocks_finalize():
    audit = _fresh_stable_unit()
    preserve_raw(
        "bid_notice_service",
        "MANUAL-OUTSIDE-PLAN",
        {"id": "MANUAL-OUTSIDE-PLAN", "bidNtceNm": "partial manual row"},
        source_system="TEST",
        source_operation="MANUAL",
        source_date="2026-09-16",
    )

    with pytest.raises(
        vnext_finalize_guard.FinalizeCoverageError,
        match="FINALIZE_CURRENT_RAW_OUTSIDE_PLAN",
    ):
        vnext_finalize_guard.require_plan_raw_coverage(audit)


def test_raw_dataset_outside_audited_plan_blocks_db_wide_finalize():
    audit = _fresh_stable_unit()
    preserve_raw(
        "budget",
        "OUTSIDE-BUDGET",
        {"id": "OUTSIDE-BUDGET"},
        source_system="TEST",
        source_operation="MANUAL",
        source_date="2026-09-16",
    )

    with pytest.raises(
        vnext_finalize_guard.FinalizeCoverageError,
        match="FINALIZE_RAW_DATASET_OUTSIDE_PLAN:budget",
    ):
        vnext_finalize_guard.require_plan_raw_coverage(audit)


def test_claimed_complete_record_is_revalidated_against_checkpoint():
    audit = {
        "all_complete": True,
        "records": [{
            "dataset": "bid_notice_service",
            "scope": "missing-scope",
            "complete": True,
        }],
    }
    with pytest.raises(
        vnext_finalize_guard.FinalizeCoverageError,
        match="FINALIZE_AUDIT_UNIT_NOT_FRESH_STABLE",
    ):
        vnext_finalize_guard.require_plan_raw_coverage(audit)
