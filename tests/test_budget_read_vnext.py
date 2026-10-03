import budget_organization_vnext
import budget_projection_vnext
import budget_read_vnext
import classification_vnext
import db
import vnext_store


def _save_budget(key, day, project_code, name, amount, executed=0):
    vnext_store.preserve_raw(
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


def _prepare():
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    classification_vnext.classify_dataset("budget")


def _counts():
    with db.connect() as conn:
        return {
            "raw": conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0],
            "projection": conn.execute("SELECT COUNT(*) FROM vnext_budget_projection").fetchone()[0],
            "classification": conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
        }


def test_collected_rows_show_detail_projects_before_structural_aidfa():
    _save_budget(
        "detail-first",
        "2026-09-17",
        "P-DETAIL",
        "노후 보안등 LED 교체",
        3000,
    )
    vnext_store.preserve_raw(
        "budget_appropriation",
        "aidfa-later",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "wa_laf_hg_nm": "경기",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_nm": "교통및물류",
            "sect_nm": "도로",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "9000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026-09-17",
    )

    rows = budget_read_vnext.collected_budget_rows(fiscal_year=2026)

    assert [row["source_layer"] for row in rows[:2]] == [
        "DETAIL_EXECUTION",
        "APPROPRIATION",
    ]
    assert rows[0]["project_name"] == "노후 보안등 LED 교체"


def test_screen_budget_rows_are_bounded_and_keep_project_identity():
    _save_budget(
        "screen-led",
        "2026-10-03",
        "P-SCREEN",
        "노후 가로등 LED 교체",
        3000,
    )
    _save_budget(
        "screen-other",
        "2026-10-02",
        "P-OTHER",
        "공원 편의시설 정비",
        5000,
    )

    rows = budget_read_vnext.screen_budget_rows(
        fiscal_year=2026,
        source_layers=("DETAIL_EXECUTION",),
        categories=("LIGHTING",),
        region="경기도",
        limit=1,
    )

    assert len(rows) == 1
    assert rows[0]["raw_source_key"] == "screen-led"
    assert rows[0]["primary_category"] == "LIGHTING"
    assert rows[0]["project_identity"].startswith("DETAIL_EXECUTION|2026|")


def test_current_rows_include_other_by_default_and_filter_only_on_request():
    _save_budget("led", "2026-09-17", "P1", "노후 가로등 LED 교체", 3000)
    _save_budget("other", "2026-09-17", "P2", "공원 편의시설 정비", 5000)
    _prepare()

    rows = budget_read_vnext.current_budget_rows(fiscal_year=2026)
    assert {row["raw_source_key"] for row in rows} == {"led", "other"}
    assert {row["primary_category"] for row in rows} == {"LIGHTING", "OTHER"}

    lighting = budget_read_vnext.current_budget_rows(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert [row["raw_source_key"] for row in lighting] == ["led"]
    assert budget_read_vnext.current_budget_rows(
        fiscal_year=2026, categories=[]
    ) == []


def test_target_rows_return_only_post_raw_target_candidates():
    _save_budget("led", "2026-09-17", "P1", "LED 보안등 개선", 3000)
    _save_budget("elec", "2026-09-17", "P2", "청사 전기설비 개선", 4000)
    _save_budget("other", "2026-09-17", "P3", "공원 편의시설 정비", 9000)
    _prepare()

    targets = budget_read_vnext.target_budget_rows(fiscal_year=2026)
    assert {row["raw_source_key"] for row in targets} == {"led", "elec"}
    assert {row["primary_category"] for row in targets} == {"LIGHTING", "ELECTRICAL"}

    assert budget_read_vnext.target_budget_rows(
        fiscal_year=2026, categories=[]
    ) == []


def test_qwgjk_history_date_range_filters_region_query_and_category():
    _save_budget(
        "hist-old",
        "2026-03-01",
        "P-HIST",
        "노후 가로등 LED 교체",
        1000,
        executed=100,
    )
    _save_budget(
        "hist-new",
        "2026-06-15",
        "P-HIST",
        "노후 가로등 LED 교체",
        1500,
        executed=400,
    )
    _save_budget(
        "hist-other",
        "2026-07-01",
        "P-OTHER",
        "공원 편의시설 정비",
        9000,
        executed=100,
    )

    rows = budget_read_vnext.qwgjk_history_rows(
        start_date="2026-01-01",
        end_date="2026-12-31",
        region="경기도",
        query="가로등",
        categories=["LIGHTING"],
        limit=20,
    )

    assert [row["source_date"] for row in rows] == [
        "2026-06-15",
        "2026-03-01",
    ]
    assert all(row["project_name"] == "노후 가로등 LED 교체" for row in rows)
    assert all(row["primary_category"] == "LIGHTING" for row in rows)
    assert rows[0]["budget_change"] == 500
    assert rows[0]["executed_change"] == 300
    assert rows[0]["remaining_change"] == 200
    assert rows[1]["budget_change"] is None
    assert rows[1]["executed_change"] is None
    assert rows[1]["remaining_change"] is None


def test_qwgjk_history_excludes_aidfa_structural_rows():
    vnext_store.preserve_raw(
        "budget_appropriation",
        "hist-aidfa",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "wa_laf_hg_nm": "경기",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_nm": "교통및물류",
            "sect_nm": "도로조명",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "9000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026-06-15",
    )
    _save_budget(
        "hist-qwgjk-only",
        "2026-06-15",
        "P-QWGJK",
        "LED 보안등 개선",
        5000,
    )

    rows = budget_read_vnext.qwgjk_history_rows(
        start_date="2026-01-01",
        end_date="2026-12-31",
        region="경기도",
        limit=20,
    )

    assert rows
    assert {row["dataset"] for row in rows} == {"budget"}
    assert all(row["source_layer"] == "DETAIL_EXECUTION" for row in rows)


def test_history_exposes_old_and_current_snapshots_for_same_project():
    _save_budget("old", "2026-08-31", "P1", "가로등 LED 교체", 1000, 100)
    _save_budget("new", "2026-09-17", "P1", "가로등 LED 교체", 1500, 500)
    _prepare()

    current = budget_read_vnext.current_budget_rows(
        fiscal_year=2026, categories=["LIGHTING"]
    )
    assert len(current) == 1
    assert current[0]["raw_source_key"] == "new"
    history = budget_read_vnext.budget_history(current[0]["project_identity"])
    assert [row["raw_source_key"] for row in history] == ["old", "new"]
    assert [row["executed_amount"] for row in history] == [100, 500]


def test_budget_status_is_explicitly_read_only_and_does_not_claim_source_completeness():
    _save_budget("led", "2026-09-17", "P1", "LED 가로등 교체", 3000)
    _prepare()
    before = _counts()

    status = budget_read_vnext.budget_status(fiscal_year=2026)

    after = _counts()
    assert after == before
    assert status["read_only"] is True
    assert status["source_traffic"] is False
    assert status["selection_stage"] == "POST_NORMALIZATION_ANALYSIS_ONLY"
    assert status["source_collection_completeness_verified"] is False
    assert status["analysis"]["by_category"]["LIGHTING"]["projects"] == 1


def test_one_call_read_model_contains_status_all_current_and_target_views():
    _save_budget("led", "2026-09-17", "P1", "LED 가로등 교체", 3000)
    _save_budget("other", "2026-09-17", "P2", "공원 편의시설 정비", 5000)
    _prepare()

    payload = budget_read_vnext.budget_read_model(fiscal_year=2026)
    assert payload["status"]["read_only"] is True
    assert len(payload["current_rows"]) == 2
    assert [row["raw_source_key"] for row in payload["target_rows"]] == ["led"]


def test_one_call_read_model_applies_same_category_filter_to_all_filtered_views():
    _save_budget("led", "2026-09-17", "P1", "LED 가로등 교체", 3000)
    _save_budget("elec", "2026-09-17", "P2", "청사 전기설비 개선", 4000)
    _save_budget("other", "2026-09-17", "P3", "공원 편의시설 정비", 9000)
    _prepare()

    payload = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        categories=["LIGHTING"],
    )

    assert {row["raw_source_key"] for row in payload["current_rows"]} == {"led"}
    assert {row["raw_source_key"] for row in payload["target_rows"]} == {"led"}
    assert {row["raw_source_key"] for row in payload["prebid_rows"]} == {"led"}
    assert "project_pipelines" not in payload
    assert "procurement_candidates" not in payload
    assert "procurement_lifecycle" not in payload
    assert payload["status"]["analysis"]["selected_categories"] == ["LIGHTING"]
    assert set(payload["status"]["analysis"]["by_category"]) == {"LIGHTING"}
    assert payload["status"]["sales_opportunity_scope"] == "BUDGET_ONLY_NO_BID_SERVICE_LINKAGE"
    assert "procurement_pipeline" not in payload["status"]


def test_one_call_read_model_explicit_empty_category_filter_returns_no_filtered_rows():
    _save_budget("led", "2026-09-17", "P1", "LED 가로등 교체", 3000)
    _prepare()

    payload = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        categories=[],
    )

    assert payload["current_rows"] == []
    assert payload["target_rows"] == []
    assert payload["prebid_rows"] == []
    assert "procurement_candidates" not in payload
    assert "procurement_lifecycle" not in payload
    assert "project_pipelines" not in payload
    assert payload["status"]["analysis"]["current_projects"] == 0
    assert payload["status"]["analysis"]["selected_categories"] == []
    assert "procurement_pipeline" not in payload["status"]


def test_budget_status_filter_does_not_mutate_stored_data():
    _save_budget("led", "2026-09-17", "P1", "LED 가로등 교체", 3000)
    _save_budget("elec", "2026-09-17", "P2", "청사 전기설비 개선", 4000)
    _prepare()
    before = _counts()

    status = budget_read_vnext.budget_status(
        fiscal_year=2026,
        categories=["ELECTRICAL"],
    )

    assert _counts() == before
    assert status["analysis"]["selected_categories"] == ["ELECTRICAL"]
    assert set(status["analysis"]["by_category"]) == {"ELECTRICAL"}
    assert status["sales_opportunity_scope"] == "BUDGET_ONLY_NO_BID_SERVICE_LINKAGE"
    assert "procurement_pipeline" not in status
    assert status["source_traffic"] is False


def test_appropriation_context_rows_expose_current_aidfa_and_detail_amounts_read_only():
    vnext_store.preserve_raw(
        "budget_appropriation", "a1",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_cd": "F1",
            "fld_nm": "교통및물류",
            "sect_cd": "S1",
            "sect_nm": "도로",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "5000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    vnext_store.preserve_raw(
        "budget", "d1",
        {
            "fyr": "2026",
            "exe_ymd": "20260919",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "dept_nm": "도로관리과",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "fld_cd": "F1",
            "fld_nm": "교통및물류",
            "sect_cd": "S1",
            "sect_nm": "도로",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "1200",
            "ep_amt": "300",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation", "budget"]
    )
    before = _counts()

    rows = budget_read_vnext.appropriation_context_rows(fiscal_year=2026)

    assert _counts() == before
    assert len(rows) == 1
    row = rows[0]
    assert row["appropriation_raw_key"] == "a1"
    assert row["appropriation_budget_amount"] == 5000
    assert row["appropriation_amount"] == 5000
    assert row["detail_raw_key"] == "d1"
    assert row["detail_project_code"] == "P1"
    assert row["detail_project_name"] == "LED 가로등 교체"
    assert row["detail_dept_code"] == "D1"
    assert row["detail_dept_name"] == "도로관리과"
    assert row["detail_snapshot_date"] == "2026-09-19"
    assert row["detail_budget_amount"] == 1200
    assert row["detail_executed_amount"] == 300
    assert row["detail_remaining_amount"] == 900
    assert row["confidence"] == 1.0


def test_budget_read_model_exposes_appropriation_context_without_promoting_it_to_procurement():
    vnext_store.preserve_raw(
        "budget_appropriation", "a1",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_cd": "F1",
            "fld_nm": "교통및물류",
            "sect_cd": "S1",
            "sect_nm": "도로",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "5000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    vnext_store.preserve_raw(
        "budget", "d1",
        {
            "fyr": "2026",
            "exe_ymd": "20260919",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "일반 도로 유지관리",
            "fld_cd": "F1",
            "fld_nm": "교통및물류",
            "sect_cd": "S1",
            "sect_nm": "도로",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "1200",
            "ep_amt": "300",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation", "budget"]
    )
    classification_vnext.classify_dataset("budget_appropriation")
    classification_vnext.classify_dataset("budget")

    payload = budget_read_vnext.budget_read_model(fiscal_year=2026)

    assert len(payload["appropriation_context"]) == 1
    assert payload["appropriation_context"][0]["appropriation_raw_key"] == "a1"
    assert "procurement_candidates" not in payload
    assert "procurement_lifecycle" not in payload

def test_target_rows_keep_aidfa_appropriation_context_only():
    vnext_store.preserve_raw(
        "budget_appropriation", "aidfa-lighting-context",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_nm": "교통및물류",
            "sect_nm": "도로조명",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "9000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation"]
    )
    classification_vnext.classify_dataset("budget_appropriation")

    current = budget_read_vnext.current_budget_rows(fiscal_year=2026)
    assert len(current) == 1
    assert current[0]["source_layer"] == "APPROPRIATION"
    assert current[0]["primary_category"] == "LIGHTING"

    # AIDFA can be classified for structural context, but must not become a direct
    # procurement/sales target row before a QWGJK or education project exists.
    assert budget_read_vnext.target_budget_rows(fiscal_year=2026) == []

    payload = budget_read_vnext.budget_read_model(fiscal_year=2026)
    assert payload["target_rows"] == []
    assert payload["prebid_rows"] == []
    assert "project_pipelines" not in payload
    assert payload["status"]["analysis"]["current_projects"] == 0
    assert payload["status"]["analysis"]["by_category"] == {}




def test_budget_read_model_reuses_one_current_analysis_scan(monkeypatch):
    _save_budget("led", "2026-09-17", "P1", "LED 가로등 교체", 3000)
    _save_budget("other", "2026-09-17", "P2", "공원 편의시설 정비", 5000)
    _prepare()

    original = budget_read_vnext.current_budget_analysis
    calls = []

    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(budget_read_vnext, "current_budget_analysis", counted)

    payload = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        categories=["LIGHTING"],
    )

    assert len(calls) == 1
    assert {row["raw_source_key"] for row in payload["target_rows"]} == {"led"}
    assert {row["raw_source_key"] for row in payload["prebid_rows"]} == {"led"}
    assert payload["status"]["analysis"]["selected_categories"] == ["LIGHTING"]



def test_indexed_appropriation_matching_preserves_code_first_fallback_rules():
    appropriation = {
        "source_layer": "APPROPRIATION",
        "project_identity": "APPROPRIATION|2026|4111000|F1|S1|A1",
        "fiscal_year": 2026,
        "org_code": "4111000",
        "org_name": "수원시",
        "field_code": "F1",
        "field_name": "교통및물류",
        "section_code": "S1",
        "section_name": "도로",
        "account_code": "A1",
        "account_name": "일반회계",
        "budget_amount": 5000,
        "appropriation_amount": 5000,
        "raw_source_key": "a1",
    }
    wrong_code_same_name = {
        "source_layer": "DETAIL_EXECUTION",
        "project_identity": "DETAIL_EXECUTION|2026|4111000|D1|P-WRONG|A1",
        "fiscal_year": 2026,
        "org_code": "4111000",
        "org_name": "수원시",
        "field_code": "F2",
        "field_name": "교통및물류",
        "section_code": "S1",
        "section_name": "도로",
        "account_code": "A1",
        "account_name": "일반회계",
        "dept_code": "D1",
        "project_code": "P-WRONG",
        "project_name": "잘못된 코드 사업",
        "snapshot_date": "2026-10-01",
        "budget_amount": 1000,
        "executed_amount": 100,
        "remaining_amount": 900,
        "raw_source_key": "wrong",
    }
    codeless_fallback = {
        **wrong_code_same_name,
        "project_identity": "DETAIL_EXECUTION|2026|4111000|D1|P-FALLBACK|A1",
        "field_code": "",
        "project_code": "P-FALLBACK",
        "project_name": "코드 누락 이름 일치 사업",
        "raw_source_key": "fallback",
    }

    links = budget_organization_vnext.exact_appropriation_detail_links_from_rows(
        [appropriation, wrong_code_same_name, codeless_fallback],
        fiscal_year=2026,
    )

    assert [row["detail_raw_key"] for row in links] == ["fallback"]
    assert links[0]["field_match_basis"] == "NAME"
    assert links[0]["section_match_basis"] == "CODE"


def test_future_appropriation_rows_are_separate_budget_signals():
    vnext_store.preserve_raw(
        "budget_appropriation",
        "future-lighting",
        {
            "fyr": "2027",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_nm": "교통및물류",
            "sect_nm": "도로조명",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "900000000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026-10-02",
    )
    vnext_store.preserve_raw(
        "budget_appropriation",
        "future-other",
        {
            "fyr": "2027",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_nm": "사회복지",
            "sect_nm": "복지행정",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "1200000000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026-10-02",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation"]
    )
    classification_vnext.classify_dataset("budget_appropriation")

    future = budget_read_vnext.future_appropriation_rows(fiscal_year=2027)

    assert len(future) == 1
    assert future[0]["raw_source_key"] == "future-lighting"
    assert future[0]["source_layer"] == "APPROPRIATION"
    assert future[0]["primary_category"] == "LIGHTING"
    assert future[0]["budget_amount"] == 900000000

    # Structural future budget signals remain separate from direct sales targets.
    assert budget_read_vnext.target_budget_rows(fiscal_year=2027) == []
    assert budget_read_vnext.prebid_budget_rows(fiscal_year=2027) == []

def test_region_filter_supports_nationwide_lofin_and_education_office_names():
    rows = [
        {
            "raw_source_key": "gyeonggi",
            "region_name": "경기",
            "org_name": "수원시",
            "source_layer": "DETAIL_EXECUTION",
        },
        {
            "raw_source_key": "incheon",
            "region_name": "인천광역시",
            "org_name": "인천광역시",
            "source_layer": "DETAIL_EXECUTION",
        },
        {
            "raw_source_key": "seoul-edu",
            "region_name": "서울특별시교육청",
            "org_name": "서울특별시교육청",
            "source_layer": "EDUCATION",
        },
    ]

    assert budget_read_vnext.canonical_region("경기") == "경기도"
    assert budget_read_vnext.canonical_region("인천광역시") == "인천광역시"
    assert (
        budget_read_vnext.canonical_region("서울특별시교육청")
        == "서울특별시"
    )
    assert {
        row["raw_source_key"]
        for row in budget_read_vnext._filter_region(rows, "")
    } == {"gyeonggi", "incheon", "seoul-edu"}
    assert [
        row["raw_source_key"]
        for row in budget_read_vnext._filter_region(rows, "경기도")
    ] == ["gyeonggi"]
    assert [
        row["raw_source_key"]
        for row in budget_read_vnext._filter_region(rows, "서울특별시")
    ] == ["seoul-edu"]


def test_budget_read_model_filters_existing_lofin_rows_by_region_without_source_io():
    vnext_store.preserve_raw(
        "budget",
        "region-gyeonggi",
        {
            "fyr": "2026",
            "exe_ymd": "20260920",
            "wa_laf_cd": "4100000",
            "wa_laf_hg_nm": "경기",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "dbiz_cd": "P-GG",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "3000",
            "ep_amt": "500",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-20",
    )
    vnext_store.preserve_raw(
        "budget",
        "region-incheon",
        {
            "fyr": "2026",
            "exe_ymd": "20260920",
            "wa_laf_cd": "2800000",
            "wa_laf_hg_nm": "인천광역시",
            "laf_cd": "2817700",
            "laf_hg_nm": "미추홀구",
            "dept_cd": "D2",
            "dbiz_cd": "P-IC",
            "dbiz_nm": "LED 보안등 교체",
            "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "5000",
            "ep_amt": "1000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-20",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    classification_vnext.classify_dataset("budget")

    nationwide = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        categories=["LIGHTING"],
        region="",
    )
    incheon = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        categories=["LIGHTING"],
        region="인천광역시",
    )
    gyeonggi = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        categories=["LIGHTING"],
        region="경기도",
    )

    assert {
        row["raw_source_key"] for row in nationwide["target_rows"]
    } == {"region-gyeonggi", "region-incheon"}
    assert [
        row["raw_source_key"] for row in incheon["target_rows"]
    ] == ["region-incheon"]
    assert [
        row["raw_source_key"] for row in gyeonggi["target_rows"]
    ] == ["region-gyeonggi"]
    assert incheon["status"]["selected_region"] == "인천광역시"
    assert gyeonggi["status"]["selected_region"] == "경기도"


def test_future_appropriation_region_filter_reuses_saved_projection():
    for key, region_code, region_name, org_code, org_name in (
        ("future-ic", "2800000", "인천광역시", "2817700", "미추홀구"),
        ("future-gg", "4100000", "경기", "4111000", "수원시"),
    ):
        vnext_store.preserve_raw(
            "budget_appropriation",
            key,
            {
                "fyr": "2027",
                "wa_laf_cd": region_code,
                "wa_laf_hg_nm": region_name,
                "laf_cd": org_code,
                "laf_hg_nm": org_name,
                "fld_nm": "교통및물류",
                "sect_nm": "도로조명",
                "acnt_dv_nm": "일반회계",
                "biz_bdg_tott_amt": "900000000",
            },
            source_system="지방재정365 AIDFA",
            source_operation="AIDFA_FULL_V1",
            source_date="2026-10-03",
        )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation"]
    )
    classification_vnext.classify_dataset("budget_appropriation")

    incheon = budget_read_vnext.future_appropriation_rows(
        fiscal_year=2027,
        region="인천광역시",
    )
    nationwide = budget_read_vnext.future_appropriation_rows(
        fiscal_year=2027,
        region="",
    )

    assert [row["raw_source_key"] for row in incheon] == ["future-ic"]
    assert {
        row["raw_source_key"] for row in nationwide
    } == {"future-ic", "future-gg"}

def test_collected_budget_rows_do_not_depend_on_projection_or_classification():
    vnext_store.preserve_raw(
        "budget_appropriation",
        "stored-aidfa-only",
        {
            "fyr": "2026",
            "wa_laf_cd": "2800000",
            "wa_laf_hg_nm": "인천광역시",
            "laf_cd": "2817700",
            "laf_hg_nm": "미추홀구",
            "fld_nm": "교통및물류",
            "sect_nm": "도로조명",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "123456789",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026-10-03",
    )

    # No projection refresh and no stored classification are intentionally run.
    with db.connect() as conn:
        assert conn.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='table' AND name='vnext_budget_projection'"""
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM classifications"
        ).fetchone()[0] == 0

    rows = budget_read_vnext.collected_budget_rows(
        fiscal_year=2026,
        region="인천광역시",
    )

    assert len(rows) == 1
    assert rows[0]["raw_source_key"] == "stored-aidfa-only"
    assert rows[0]["source_layer"] == "APPROPRIATION"
    assert rows[0]["budget_amount"] == 123456789
    assert rows[0]["primary_category"] == "LIGHTING"
    assert budget_read_vnext.row_region(rows[0]) == "인천광역시"


def test_collected_budget_rows_apply_region_and_category_locally():
    for key, region_name, org_name, project_name in (
        ("ic-led", "인천광역시", "미추홀구", "LED 도로조명"),
        ("gg-other", "경기", "수원시", "공원 편의시설"),
    ):
        vnext_store.preserve_raw(
            "budget",
            key,
            {
                "fyr": "2026",
                "exe_ymd": "20261003",
                "wa_laf_hg_nm": region_name,
                "laf_hg_nm": org_name,
                "dbiz_cd": key,
                "dbiz_nm": project_name,
                "bdg_cash_amt": "1000",
                "ep_amt": "100",
            },
            source_system="지방재정365 QWGJK",
            source_operation="QWGJK_FULL_V2_SNAPSHOT",
            source_date="2026-10-03",
        )

    incheon_lighting = budget_read_vnext.collected_budget_rows(
        fiscal_year=2026,
        region="인천광역시",
        categories=["LIGHTING"],
    )
    nationwide = budget_read_vnext.collected_budget_rows(
        fiscal_year=2026,
        region="",
    )

    assert [row["raw_source_key"] for row in incheon_lighting] == ["ic-led"]
    assert {
        row["raw_source_key"] for row in nationwide
    } == {"ic-led", "gg-other"}

