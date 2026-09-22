import budget_projection_vnext
import budget_read_vnext
import classification_vnext
import vnext_store


def test_identity_less_budget_rows_are_analysis_only_not_procurement_projects():
    vnext_store.preserve_raw(
        "budget", "partial-qwgjk",
        {
            "fyr": "2026",
            "exe_ymd": "20260919",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "사업명": "LED 조명 개선",
            "bdg_cash_amt": "1000",
            "ep_amt": "100",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )
    vnext_store.preserve_raw(
        "education_budget", "partial-education",
        {
            "YMQ": "2026",
            "officeCode": "J10",
            "교육청명": "경기도교육청",
            "itemName": "LED 조명 시설비",
            "예산액": "2000",
            "집행액": "200",
        },
        source_system="지방교육재정알리미(typeA)",
        source_operation="EDUINFO_FULL_RAW_V1:typeA",
        source_date="2026",
    )

    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget", "education_budget"]
    )
    classification_vnext.classify_dataset("budget")
    classification_vnext.classify_dataset("education_budget")

    current = budget_read_vnext.current_budget_rows(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert {row["raw_source_key"] for row in current} == {
        "partial-qwgjk", "partial-education"
    }
    assert all(
        not row["project_code"] and not row["project_name"]
        for row in current
    )

    # Preserve incomplete rows for diagnostics/analysis but do not promote them
    # into procurement/sales projects before project identity exists.
    assert budget_read_vnext.target_budget_rows(
        fiscal_year=2026, categories=["LIGHTING"]
    ) == []
    payload = budget_read_vnext.budget_read_model(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert payload["target_rows"] == []
    assert payload["procurement_candidates"] == []
    assert payload["project_pipelines"] == []
    assert payload["prebid_rows"] == []
    assert payload["status"]["analysis"]["current_projects"] == 0
    assert payload["status"]["analysis"]["by_category"] == {}
