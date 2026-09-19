import budget_organization_vnext
import budget_projection_vnext
from vnext_store import preserve_raw


def _save_qwgjk(key, day, *, amount, executed, project="P1", field="교통및물류", section="도로"):
    preserve_raw(
        "budget", key,
        {
            "fyr": "2026", "exe_ymd": day.replace("-", ""),
            "wa_laf_cd": "4100000", "wa_laf_hg_nm": "경기",
            "laf_cd": "4111000", "laf_hg_nm": "수원시", "dept_cd": "D1",
            "dbiz_cd": project, "dbiz_nm": "도로시설 유지관리",
            "fld_nm": field, "sect_nm": section, "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": str(amount), "cpl_amt": str(amount), "ep_amt": str(executed),
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date=day,
    )


def test_current_state_collapses_repeated_qwgjk_snapshots_but_timeline_keeps_all():
    _save_qwgjk("old", "2026-08-31", amount=1000, executed=100)
    _save_qwgjk("new", "2026-09-17", amount=1200, executed=300)
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])

    current = budget_organization_vnext.current_budget_state(
        fiscal_year=2026, source_layers=["DETAIL_EXECUTION"]
    )
    assert len(current) == 1
    assert current[0]["snapshot_date"] == "2026-09-17"
    assert current[0]["budget_amount"] == 1200
    assert current[0]["executed_amount"] == 300
    assert current[0]["remaining_amount"] == 900

    timeline = budget_organization_vnext.budget_timeline(current[0]["project_identity"])
    assert [row["snapshot_date"] for row in timeline] == ["2026-08-31", "2026-09-17"]
    assert [row["executed_amount"] for row in timeline] == [100, 300]


def test_projection_coverage_detects_missing_and_stale_current_raw():
    _save_qwgjk("q1", "2026-09-17", amount=1000, executed=100)
    before = {row["dataset"]: row for row in budget_organization_vnext.projection_coverage()}
    assert before["budget"]["raw_rows"] == 1
    assert before["budget"]["projection_rows_current"] == 0
    assert before["budget"]["projection_complete_for_current_raw"] is False
    assert before["budget"]["source_collection_completeness_verified"] is False

    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    after = {row["dataset"]: row for row in budget_organization_vnext.projection_coverage()}
    assert after["budget"]["projection_complete_for_current_raw"] is True

    # Same source identity receives a new revision after projection; coverage must
    # become stale until the projection is refreshed again.
    _save_qwgjk("q1", "2026-09-17", amount=1500, executed=200)
    stale = {row["dataset"]: row for row in budget_organization_vnext.projection_coverage()}
    assert stale["budget"]["stale_or_missing_rows"] == 1
    assert stale["budget"]["projection_complete_for_current_raw"] is False


def test_exact_appropriation_detail_link_requires_exact_org_field_section():
    preserve_raw(
        "budget_appropriation", "a1",
        {
            "fyr": "2026", "wa_laf_cd": "4100000", "laf_cd": "4111000",
            "fld_nm": "교통및물류", "sect_nm": "도로", "acnt_dv_nm": "일반회계",
            "cpl_amt": "5000",
        },
        source_system="지방재정365 AIDFA", source_operation="AIDFA_FULL_V1", source_date="2026",
    )
    _save_qwgjk("d1", "2026-09-17", amount=1000, executed=100)
    _save_qwgjk("d2", "2026-09-17", amount=1000, executed=100, project="P2", section="대중교통")
    budget_projection_vnext.refresh_budget_projection()

    links = budget_organization_vnext.exact_appropriation_detail_links(fiscal_year=2026)
    assert len(links) == 1
    assert links[0]["appropriation_raw_key"] == "a1"
    assert links[0]["detail_raw_key"] == "d1"
    assert links[0]["match_basis"] == "EXACT_ORG_FIELD_SECTION"
    assert links[0]["confidence"] == 1.0


def test_education_rows_have_stable_current_identity_by_project_code():
    preserve_raw(
        "education_budget", "e1",
        {"YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
         "projectCode": "E1", "사업명": "학교시설 환경개선", "예산액": "3000", "집행액": "500"},
        source_system="지방교육재정알리미(opclTotal)",
        source_operation="EDUINFO_FULL_RAW_V1:opclTotal", source_date="2026-06-30",
    )
    preserve_raw(
        "education_budget", "e2",
        {"YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
         "projectCode": "E1", "사업명": "학교시설 환경개선", "예산액": "3500", "집행액": "1000"},
        source_system="지방교육재정알리미(opclTotal)",
        source_operation="EDUINFO_FULL_RAW_V1:opclTotal", source_date="2026-09-17",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["education_budget"])
    current = budget_organization_vnext.current_budget_state(
        fiscal_year=2026, source_layers=["EDUCATION"]
    )
    assert len(current) == 1
    assert current[0]["budget_amount"] == 3500
    assert current[0]["remaining_amount"] == 2500
    timeline = budget_organization_vnext.budget_timeline(current[0]["project_identity"])
    assert len(timeline) == 2


def test_organization_summary_reports_current_project_counts_not_raw_snapshot_count():
    _save_qwgjk("old", "2026-08-31", amount=1000, executed=100)
    _save_qwgjk("new", "2026-09-17", amount=1200, executed=300)
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    summary = budget_organization_vnext.organization_summary(fiscal_year=2026)
    assert summary["current_projects"] == 1
    assert summary["by_layer"]["DETAIL_EXECUTION"]["projects"] == 1
    assert summary["source_collection_completeness_verified"] is False


def test_education_same_project_in_different_request_types_does_not_collapse():
    preserve_raw(
        "education_budget", "type-a",
        {
            "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
            "projectCode": "E1", "사업명": "학교시설 환경개선",
            "예산액": "3000", "집행액": "500",
        },
        source_system="지방교육재정알리미(typeA)",
        source_operation="EDUINFO_FULL_RAW_V1:typeA",
        source_date="2026-09-17",
    )
    preserve_raw(
        "education_budget", "type-b",
        {
            "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
            "projectCode": "E1", "사업명": "학교시설 환경개선",
            "예산액": "7000", "집행액": "2000",
        },
        source_system="지방교육재정알리미(typeB)",
        source_operation="EDUINFO_FULL_RAW_V1:typeB",
        source_date="2026-09-17",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["education_budget"])

    current = budget_organization_vnext.current_budget_state(
        fiscal_year=2026, source_layers=["EDUCATION"]
    )

    assert len(current) == 2
    assert {row["source_operation"] for row in current} == {
        "EDUINFO_FULL_RAW_V1:typeA",
        "EDUINFO_FULL_RAW_V1:typeB",
    }
    assert {row["budget_amount"] for row in current} == {3000, 7000}
    assert len({row["project_identity"] for row in current}) == 2
    assert all(
        "EDUINFO_FULL_RAW_V1:type" in row["project_identity"]
        for row in current
    )


def test_education_timeline_stays_within_one_request_type():
    for key, day, amount in (
        ("a-old", "2026-06-30", "3000"),
        ("a-new", "2026-09-17", "3500"),
    ):
        preserve_raw(
            "education_budget", key,
            {
                "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
                "projectCode": "E1", "사업명": "학교시설 환경개선",
                "예산액": amount, "집행액": "500",
            },
            source_system="지방교육재정알리미(typeA)",
            source_operation="EDUINFO_FULL_RAW_V1:typeA",
            source_date=day,
        )
    preserve_raw(
        "education_budget", "b-one",
        {
            "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
            "projectCode": "E1", "사업명": "학교시설 환경개선",
            "예산액": "9000", "집행액": "1000",
        },
        source_system="지방교육재정알리미(typeB)",
        source_operation="EDUINFO_FULL_RAW_V1:typeB",
        source_date="2026-09-17",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["education_budget"])

    current = budget_organization_vnext.current_budget_state(
        fiscal_year=2026, source_layers=["EDUCATION"]
    )
    type_a = next(
        row for row in current
        if row["source_operation"] == "EDUINFO_FULL_RAW_V1:typeA"
    )
    timeline = budget_organization_vnext.budget_timeline(type_a["project_identity"])

    assert [row["raw_source_key"] for row in timeline] == ["a-old", "a-new"]
    assert {row["source_operation"] for row in timeline} == {
        "EDUINFO_FULL_RAW_V1:typeA"
    }
