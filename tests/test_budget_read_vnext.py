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
    assert status["selection_stage"] == "POST_RAW_ANALYSIS_ONLY"
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
    assert {row["budget_raw_source_key"] for row in payload["project_pipelines"]} == {"led"}
    assert {row["budget_raw_source_key"] for row in payload["prebid_rows"]} == {"led"}
    assert payload["procurement_candidates"] == []
    assert payload["procurement_lifecycle"] == []
    assert payload["status"]["analysis"]["selected_categories"] == ["LIGHTING"]
    assert set(payload["status"]["analysis"]["by_category"]) == {"LIGHTING"}
    assert payload["status"]["procurement_pipeline"]["target_projects"] == 1


def test_one_call_read_model_explicit_empty_category_filter_returns_no_filtered_rows():
    _save_budget("led", "2026-09-17", "P1", "LED 가로등 교체", 3000)
    _prepare()

    payload = budget_read_vnext.budget_read_model(
        fiscal_year=2026,
        categories=[],
    )

    assert payload["current_rows"] == []
    assert payload["target_rows"] == []
    assert payload["procurement_candidates"] == []
    assert payload["procurement_lifecycle"] == []
    assert payload["project_pipelines"] == []
    assert payload["prebid_rows"] == []
    assert payload["status"]["analysis"]["current_projects"] == 0
    assert payload["status"]["analysis"]["selected_categories"] == []
    assert payload["status"]["procurement_pipeline"]["target_projects"] == 0


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
    assert status["procurement_pipeline"]["target_projects"] == 1
    assert status["source_traffic"] is False
