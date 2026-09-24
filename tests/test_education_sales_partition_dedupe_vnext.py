import budget_organization_vnext
import budget_read_vnext
import budget_targets_vnext
from vnext_store import preserve_raw


def _save(key, request_type, *, school_code="S1", school_name="가온초등학교",
          budget=2000, executed=200):
    preserve_raw(
        "education_budget",
        key,
        {
            "YMQ": "2026",
            "officeCode": "J10",
            "교육청명": "경기도교육청",
            "schoolCode": school_code,
            "schoolName": school_name,
            "departmentCode": "D1",
            "departmentName": "교육시설과",
            "projectCode": "E1",
            "사업명": "학교 LED 조명 개선",
            "itemCode": "I1",
            "itemName": "시설비",
            "예산액": str(budget),
            "집행액": str(executed),
        },
        source_system=f"지방교육재정알리미({request_type})",
        source_operation=f"EDUINFO_FULL_RAW_V1:{request_type}",
        source_date="2026-09-24",
    )


def test_identical_request_type_partitions_stay_separate_in_raw_but_one_sales_candidate():
    _save("type-a", "typeA")
    _save("type-b", "typeB")
    result = budget_targets_vnext.prepare_budget_analysis()
    assert result["source_traffic"] is False

    current = budget_organization_vnext.current_budget_state(
        fiscal_year=2026, source_layers=["EDUCATION"]
    )
    assert len(current) == 2
    assert len({row["project_identity"] for row in current}) == 2

    candidates = budget_targets_vnext.target_candidates(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert len(candidates) == 1
    row = candidates[0]
    assert row["education_request_types"] == ["typeA", "typeB"]
    assert row["source_operation"] == "EDUINFO_FULL_RAW_V1:typeA"
    assert row["education_partition_count"] == 2
    assert row["education_partition_deduplicated"] is True
    assert row["education_partition_variant_count"] == 1
    assert row["education_partition_conflict"] is False
    assert row["institution_code"] == "S1"
    assert row["institution_name"] == "가온초등학교"
    assert row["budget_amount"] == 2000
    assert row["remaining_amount"] == 1800

    read_rows = budget_read_vnext.target_budget_rows(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert len(read_rows) == 1

    summary = budget_targets_vnext.target_summary(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert summary["current_projects"] == 1
    assert summary["by_category"]["LIGHTING"]["projects"] == 1
    assert summary["by_category"]["LIGHTING"]["budget_amount"] == 2000


def test_different_financial_facts_across_request_types_are_not_blindly_collapsed():
    _save("type-a", "typeA", budget=2000, executed=200)
    _save("type-b", "typeB", budget=2500, executed=200)
    budget_targets_vnext.prepare_budget_analysis()

    candidates = budget_targets_vnext.target_candidates(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert len(candidates) == 2
    assert len({row["sales_opportunity_identity"] for row in candidates}) == 1
    assert {row["budget_amount"] for row in candidates} == {2000, 2500}
    assert all(row["education_partition_conflict"] is True for row in candidates)
    assert all(row["education_partition_variant_count"] == 2 for row in candidates)
    assert all(row["education_partition_deduplicated"] is False for row in candidates)


def test_school_identity_prevents_cross_school_sales_dedupe():
    _save("school-a", "typeA", school_code="S1", school_name="가온초등학교")
    _save("school-b", "typeB", school_code="S2", school_name="나래초등학교")
    budget_targets_vnext.prepare_budget_analysis()

    candidates = budget_targets_vnext.target_candidates(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert len(candidates) == 2
    assert {row["institution_code"] for row in candidates} == {"S1", "S2"}
    assert len({row["sales_opportunity_identity"] for row in candidates}) == 2
    assert all(row["education_partition_conflict"] is False for row in candidates)
