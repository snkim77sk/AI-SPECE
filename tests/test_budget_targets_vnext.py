import budget_targets_vnext
from db import connect
from vnext_store import preserve_raw


def _save_budget(key, day, project_code, name, amount, executed=0):
    preserve_raw(
        "budget", key,
        {
            "fyr": "2026", "exe_ymd": day.replace("-", ""),
            "wa_laf_cd": "4100000", "wa_laf_hg_nm": "경기",
            "laf_cd": "4111000", "laf_hg_nm": "수원시", "dept_cd": "D1",
            "dbiz_cd": project_code, "dbiz_nm": name, "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": str(amount), "cpl_amt": str(amount), "ep_amt": str(executed),
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date=day,
    )


def _save_education(key, code, name, amount):
    preserve_raw(
        "education_budget", key,
        {
            "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
            "projectCode": code, "사업명": name, "예산액": str(amount), "집행액": "0",
        },
        source_system="지방교육재정알리미(opclTotal)",
        source_operation="EDUINFO_FULL_RAW_V1:opclTotal",
        source_date="2026-09-17",
    )


def test_budget_and_education_are_classified_only_after_raw_preservation():
    _save_budget("q-led", "2026-09-17", "Q1", "노후 가로등 LED 교체", 1000)
    _save_education("e-led", "E1", "학교 LED 조명 개선", 2000)
    _save_education("e-other", "E2", "교실 냉난방 개선", 3000)

    result = budget_targets_vnext.prepare_budget_analysis()
    assert result["source_traffic"] is False
    assert result["projection"]["projected"] == 3
    assert result["source_collection_completeness_verified"] is False

    rows = budget_targets_vnext.current_budget_analysis(fiscal_year=2026)
    by_key = {(row["raw_dataset"], row["raw_source_key"]): row for row in rows}
    assert by_key[("budget", "q-led")]["primary_category"] == "LIGHTING"
    assert by_key[("budget", "q-led")]["subcategory"] == "STREET_LIGHT"
    assert by_key[("education_budget", "e-led")]["primary_category"] == "LIGHTING"
    assert by_key[("education_budget", "e-other")]["primary_category"] == "OTHER"

    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM raw_records").fetchone()["n"] == 3


def test_target_candidates_filters_only_at_analysis_view_and_keeps_other_raw():
    _save_education("e-led", "E1", "학교 LED 조명 개선", 2000)
    _save_education("e-other", "E2", "학교 급식실 환경개선", 3000)
    budget_targets_vnext.prepare_budget_analysis()

    candidates = budget_targets_vnext.target_candidates(fiscal_year=2026)
    assert [(row["raw_source_key"], row["primary_category"]) for row in candidates] == [
        ("e-led", "LIGHTING")
    ]

    all_rows = budget_targets_vnext.current_budget_analysis(fiscal_year=2026)
    assert {row["raw_source_key"] for row in all_rows} == {"e-led", "e-other"}
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM raw_records WHERE dataset='education_budget'"
        ).fetchone()["n"] == 2


def test_target_candidates_use_newest_qwgjk_snapshot_only():
    _save_budget("q-old", "2026-08-31", "Q1", "가로등 LED 교체사업", 1000, executed=100)
    _save_budget("q-new", "2026-09-17", "Q1", "가로등 LED 교체사업", 1500, executed=500)
    budget_targets_vnext.prepare_budget_analysis()

    candidates = budget_targets_vnext.target_candidates(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert len(candidates) == 1
    assert candidates[0]["raw_source_key"] == "q-new"
    assert candidates[0]["snapshot_date"] == "2026-09-17"
    assert candidates[0]["budget_amount"] == 1500
    assert candidates[0]["executed_amount"] == 500
    assert candidates[0]["remaining_amount"] == 1000


def test_budget_appropriation_text_can_be_post_classified_without_prefiltering():
    preserve_raw(
        "budget_appropriation", "a1",
        {
            "fyr": "2026", "wa_laf_cd": "4100000", "laf_cd": "4111000",
            "fld_nm": "교통및물류", "sect_nm": "도로조명", "cpl_amt": "9000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1", source_date="2026",
    )
    budget_targets_vnext.prepare_budget_analysis()
    rows = budget_targets_vnext.current_budget_analysis(fiscal_year=2026)
    assert len(rows) == 1
    assert rows[0]["source_layer"] == "APPROPRIATION"
    assert rows[0]["primary_category"] == "LIGHTING"
    assert rows[0]["subcategory"] == "STREET_LIGHT"


def test_summary_separates_targets_from_other_without_completeness_claim():
    _save_budget("q-elec", "2026-09-17", "Q2", "청사 전기설비 개선", 4000)
    _save_education("e-other", "E2", "교실 냉난방 개선", 3000)
    budget_targets_vnext.prepare_budget_analysis()
    summary = budget_targets_vnext.target_summary(fiscal_year=2026)
    assert summary["current_projects"] == 2
    assert summary["by_category"]["ELECTRICAL"]["projects"] == 1
    assert summary["by_category"]["OTHER"]["projects"] == 1
    assert summary["selection_stage"] == "POST_RAW_ANALYSIS_ONLY"
    assert summary["source_collection_completeness_verified"] is False

def test_target_candidates_sort_zero_remaining_below_positive_remaining():
    _save_budget(
        "q-zero", "2026-09-22", "Q0",
        "LED 가로등 전액집행 사업", 10000, executed=10000
    )
    _save_budget(
        "q-positive", "2026-09-22", "Q1",
        "LED 가로등 잔액 사업", 100, executed=50
    )
    budget_targets_vnext.prepare_budget_analysis()

    candidates = budget_targets_vnext.target_candidates(
        fiscal_year=2026, categories=["LIGHTING"]
    )

    assert [row["raw_source_key"] for row in candidates] == [
        "q-positive", "q-zero"
    ]
    assert [row["remaining_amount"] for row in candidates] == [50, 0]

