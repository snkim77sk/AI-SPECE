import analysis_vnext
import award_projection
import budget_procurement_lifecycle_vnext
import budget_projection_vnext
import budget_read_vnext
import classification_vnext
import contract_projection
import db
import vnext_store


def _budget(name="LED 가로등 교체", *, key="P1", amount=100000000, executed=10000000):
    vnext_store.preserve_raw(
        "budget", key,
        {
            "fyr": "2026", "exe_ymd": "20260918",
            "laf_cd": "4111000", "laf_hg_nm": "수원시",
            "dbiz_cd": key, "dbiz_nm": name, "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": str(amount), "ep_amt": str(executed),
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-18",
    )


def _notice(dataset, key="A|000", name="LED 가로등 교체 설계"):
    no, order = key.split("|", 1)
    vnext_store.preserve_raw(
        dataset, key,
        {
            "bidNtceNo": no, "bidNtceOrd": order, "bidNtceNm": name,
            "bidNtceDt": "2026-09-18", "dminsttCd": "4111000", "dminsttNm": "수원시",
        },
        source_system="G2B", source_operation="TEST", source_date="2026-09-18",
    )


def _prepare(*datasets):
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    classification_vnext.classify_dataset("budget")
    for dataset in datasets:
        classification_vnext.classify_dataset(dataset)


def _service_execution(rebid="0", vendor="가격1순위", final_vendor="최종업체", contract_no="C1"):
    execution = f"A|000|1|{rebid}"
    opening = {
        "bidNtceNo": "A", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": rebid,
        "progrsDivCdNm": "개찰완료",
        "opengCorpInfo": f"{vendor}^1234567890^REP^100^88.1",
    }
    final = dict(
        opening, bidwinnrNm=final_vendor, bidwinnrBizno="1234567890",
        sucsfbidAmt="110",
    )
    contract = {
        "bidNtceNo": "A", "bidNtceOrd": "000", "dcsnCntrctNo": contract_no,
        "thtmCntrctAmt": "110",
        "corpList": f"[1^단독^^{final_vendor}^REP^KR^100^^CONTACT^1234567890]",
    }
    vnext_store.preserve_raw("opening_result_service", execution, opening)
    award_projection.project_opening_row(opening, raw_source_key=execution)
    vnext_store.preserve_raw("award_result_service", execution, final)
    award_projection.project_final_award_row(final, raw_source_key=execution)
    vnext_store.preserve_raw("contract_service", contract_no, contract)
    contract_projection.project_contract_row(contract, raw_source_key=contract_no)
    vnext_store.save_lifecycle_link(
        "bid_notice", "A|000", "award_summary", execution, "HAS_AWARD_EXECUTION",
        confidence=1.0, reason="test execution",
    )
    return execution


def _counts():
    with db.connect() as conn:
        return {
            "raw": conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0],
            "classifications": conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
            "links": conn.execute("SELECT COUNT(*) FROM lifecycle_links").fetchone()[0],
        }


def test_budget_service_candidate_exposes_first_final_and_contract():
    _budget()
    _notice("bid_notice_service")
    _prepare("bid_notice_service")
    execution = _service_execution()

    rows = budget_procurement_lifecycle_vnext.budget_procurement_lifecycle_rows(
        fiscal_year=2026
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["award_summary_key"] == execution
    assert row["latest_known_stage"] == "CONTRACTED"
    assert row["first_rank_vendor"] == "가격1순위"
    assert row["final_vendor"] == "최종업체"
    assert row["contract_no"] == "C1"
    assert row["budget_notice_relation"] == "CANDIDATE_ONLY"


def test_goods_candidate_stays_notice_only_without_invented_award_lifecycle():
    _budget()
    _notice("bid_notice_goods", name="LED 가로등 등기구 교체 구매")
    _prepare("bid_notice_goods")

    rows = budget_procurement_lifecycle_vnext.budget_procurement_lifecycle_rows(
        fiscal_year=2026
    )

    assert len(rows) == 1
    assert rows[0]["notice_dataset"] == "bid_notice_goods"
    assert rows[0]["latest_known_stage"] == "NOTICE_ONLY"
    assert rows[0]["lifecycle_supported"] is False
    assert rows[0]["final_vendor"] == ""
    assert rows[0]["contract_no"] == ""


def test_multiple_service_executions_remain_separate_budget_lifecycle_rows():
    _budget()
    _notice("bid_notice_service")
    _prepare("bid_notice_service")
    first = _service_execution(rebid="0", vendor="첫개찰", final_vendor="최종A", contract_no="C1")
    second = _service_execution(rebid="1", vendor="재개찰", final_vendor="최종B", contract_no="C2")

    rows = budget_procurement_lifecycle_vnext.budget_procurement_lifecycle_rows(
        fiscal_year=2026
    )

    assert {row["award_summary_key"] for row in rows} == {first, second}
    assert {row["first_rank_vendor"] for row in rows} == {"첫개찰", "재개찰"}


def test_targeted_service_lifecycle_filter_returns_only_requested_notice():
    for key in ("A|000", "B|000"):
        no, order = key.split("|", 1)
        vnext_store.preserve_raw(
            "bid_notice_service", key,
            {"bidNtceNo": no, "bidNtceOrd": order, "bidNtceNm": "LED 가로등 설계"},
            source_system="G2B", source_date="2026-09-18",
        )
    classification_vnext.classify_dataset("bid_notice_service")

    rows = analysis_vnext.service_lifecycle_rows(source_keys=["B|000"])

    assert [row["source_key"] for row in rows] == ["B|000"]


def test_budget_procurement_lifecycle_query_is_read_only():
    _budget()
    _notice("bid_notice_service")
    _prepare("bid_notice_service")
    _service_execution()
    before = _counts()

    rows = budget_procurement_lifecycle_vnext.budget_procurement_lifecycle_rows(
        fiscal_year=2026
    )
    summary = budget_procurement_lifecycle_vnext.budget_procurement_lifecycle_summary(
        fiscal_year=2026
    )

    after = _counts()
    assert rows
    assert summary["persisted_budget_notice_links"] == 0
    assert summary["source_traffic"] is False
    assert after == before


def test_budget_read_model_exposes_procurement_lifecycle():
    _budget()
    _notice("bid_notice_service")
    _prepare("bid_notice_service")
    _service_execution()

    payload = budget_read_vnext.budget_read_model(fiscal_year=2026)

    assert len(payload["procurement_lifecycle"]) == 1
    assert payload["procurement_lifecycle"][0]["latest_known_stage"] == "CONTRACTED"
    assert payload["procurement_lifecycle"][0]["final_vendor"] == "최종업체"


def test_project_view_keeps_budget_only_target_visible():
    _budget()
    _prepare()

    rows = budget_procurement_lifecycle_vnext.budget_project_procurement_rows(
        fiscal_year=2026
    )

    assert len(rows) == 1
    assert rows[0]["project_name"] == "LED 가로등 교체"
    assert rows[0]["latest_known_stage"] == "BUDGET_ONLY"
    assert rows[0]["procurement_candidate_count"] == 0
    assert rows[0]["notices"] == []


def test_project_view_groups_goods_notice_as_notice_published():
    _budget()
    _notice("bid_notice_goods", name="LED 가로등 등기구 교체 구매")
    _prepare("bid_notice_goods")

    rows = budget_procurement_lifecycle_vnext.budget_project_procurement_rows(
        fiscal_year=2026
    )

    assert len(rows) == 1
    assert rows[0]["latest_known_stage"] == "NOTICE_PUBLISHED"
    assert rows[0]["procurement_candidate_count"] == 1
    notice = rows[0]["notices"][0]
    assert notice["notice_dataset"] == "bid_notice_goods"
    assert notice["latest_known_stage"] == "NOTICE_PUBLISHED"
    assert notice["executions"] == []


def test_project_view_nests_service_contract_under_budget():
    _budget()
    _notice("bid_notice_service")
    _prepare("bid_notice_service")
    execution = _service_execution()

    rows = budget_procurement_lifecycle_vnext.budget_project_procurement_rows(
        fiscal_year=2026
    )

    assert len(rows) == 1
    assert rows[0]["latest_known_stage"] == "CONTRACTED"
    assert rows[0]["procurement_candidate_count"] == 1
    notice = rows[0]["notices"][0]
    assert notice["latest_known_stage"] == "CONTRACTED"
    assert len(notice["executions"]) == 1
    assert notice["executions"][0]["award_summary_key"] == execution
    assert notice["executions"][0]["final_vendor"] == "최종업체"
    assert notice["executions"][0]["contract_no"] == "C1"


def test_budget_read_model_exposes_project_pipeline_even_before_notice():
    _budget()
    _prepare()

    payload = budget_read_vnext.budget_read_model(fiscal_year=2026)

    assert len(payload["project_pipelines"]) == 1
    assert payload["project_pipelines"][0]["latest_known_stage"] == "BUDGET_ONLY"
    assert payload["project_pipelines"][0]["source_traffic"] is False


def test_prebid_view_keeps_only_budget_only_projects_and_sorts_remaining_amount():
    _budget("LED 가로등 교체", key="P1", amount=100000000, executed=10000000)
    _budget("LED 보안등 개선", key="P2", amount=300000000, executed=50000000)
    _notice("bid_notice_goods", name="LED 가로등 등기구 교체 구매")
    _prepare("bid_notice_goods")

    rows = budget_procurement_lifecycle_vnext.prebid_budget_projects(fiscal_year=2026)

    assert [row["budget_raw_source_key"] for row in rows] == ["P2"]
    assert rows[0]["latest_known_stage"] == "BUDGET_ONLY"
    assert rows[0]["remaining_amount"] == 250000000


def test_prebid_view_minimum_remaining_amount_is_plain_filter_not_score():
    _budget("LED 보안등 개선", key="P1", amount=100000000, executed=90000000)
    _prepare()

    assert budget_procurement_lifecycle_vnext.prebid_budget_projects(
        fiscal_year=2026, minimum_remaining_amount=20000000
    ) == []
    rows = budget_procurement_lifecycle_vnext.prebid_budget_projects(
        fiscal_year=2026, minimum_remaining_amount=10000000
    )
    assert len(rows) == 1
    assert rows[0]["remaining_amount"] == 10000000


def test_pipeline_summary_counts_budget_only_and_notice_published_amounts():
    _budget("LED 가로등 교체", key="P1", amount=100000000, executed=10000000)
    _budget("LED 보안등 개선", key="P2", amount=300000000, executed=50000000)
    _notice("bid_notice_goods", name="LED 가로등 등기구 교체 구매")
    _prepare("bid_notice_goods")

    summary = budget_procurement_lifecycle_vnext.budget_pipeline_summary(fiscal_year=2026)

    assert summary["target_projects"] == 2
    assert summary["by_stage"]["BUDGET_ONLY"]["projects"] == 1
    assert summary["by_stage"]["BUDGET_ONLY"]["remaining_amount"] == 250000000
    assert summary["by_stage"]["NOTICE_PUBLISHED"]["projects"] == 1
    assert summary["read_only"] is True
    assert summary["source_collection_completeness_verified"] is False


def test_budget_read_model_exposes_prebid_rows_and_pipeline_summary():
    _budget("LED 보안등 개선", amount=300000000, executed=50000000)
    _prepare()

    payload = budget_read_vnext.budget_read_model(fiscal_year=2026)

    assert len(payload["prebid_rows"]) == 1
    assert payload["prebid_rows"][0]["latest_known_stage"] == "BUDGET_ONLY"
    assert payload["status"]["procurement_pipeline"]["by_stage"]["BUDGET_ONLY"]["projects"] == 1


def test_aidfa_appropriation_is_not_promoted_to_budget_only_sales_project():
    vnext_store.preserve_raw(
        "budget_appropriation", "AIDFA-1",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_cd": "F1",
            "fld_nm": "도로조명",
            "sect_cd": "S1",
            "sect_nm": "LED 가로등",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "500000000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation"]
    )
    classification_vnext.classify_dataset("budget_appropriation")

    assert budget_procurement_lifecycle_vnext.budget_project_procurement_rows(
        fiscal_year=2026
    ) == []
    assert budget_procurement_lifecycle_vnext.prebid_budget_projects(
        fiscal_year=2026
    ) == []
    summary = budget_procurement_lifecycle_vnext.budget_pipeline_summary(
        fiscal_year=2026
    )
    assert summary["target_projects"] == 0
    assert summary["by_stage"] == {}


def test_qwgjk_project_still_appears_in_prebid_after_aidfa_context_exclusion():
    _budget("LED 가로등 교체", key="P1", amount=200000000, executed=50000000)
    _prepare()

    rows = budget_procurement_lifecycle_vnext.prebid_budget_projects(
        fiscal_year=2026
    )

    assert len(rows) == 1
    assert rows[0]["budget_source_layer"] == "DETAIL_EXECUTION"
    assert rows[0]["budget_raw_source_key"] == "P1"
    assert rows[0]["latest_known_stage"] == "BUDGET_ONLY"


def test_detail_project_pipeline_nests_exact_appropriation_context():
    vnext_store.preserve_raw(
        "budget_appropriation", "AIDFA-CONTEXT",
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
            "biz_bdg_tott_amt": "500000000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    vnext_store.preserve_raw(
        "budget", "DETAIL-P1",
        {
            "fyr": "2026",
            "exe_ymd": "20260918",
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
            "bdg_cash_amt": "120000000",
            "ep_amt": "20000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-18",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation", "budget"]
    )
    classification_vnext.classify_dataset("budget")

    rows = budget_procurement_lifecycle_vnext.budget_project_procurement_rows(
        fiscal_year=2026
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["budget_raw_source_key"] == "DETAIL-P1"
    assert row["latest_known_stage"] == "BUDGET_ONLY"
    assert row["appropriation_context_count"] == 1
    context = row["appropriation_contexts"][0]
    assert context["appropriation_raw_key"] == "AIDFA-CONTEXT"
    assert context["appropriation_amount"] == 500000000
    assert context["detail_raw_key"] == "DETAIL-P1"
    assert context["detail_project_name"] == "LED 가로등 교체"
    assert context["detail_budget_amount"] == 120000000
    assert context["detail_executed_amount"] == 20000000
    assert context["detail_remaining_amount"] == 100000000


def test_prebid_detail_project_keeps_nested_appropriation_context():
    vnext_store.preserve_raw(
        "budget_appropriation", "AIDFA-CONTEXT",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "fld_cd": "F1",
            "fld_nm": "교통및물류",
            "sect_cd": "S1",
            "sect_nm": "도로",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "500000000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    vnext_store.preserve_raw(
        "budget", "DETAIL-P1",
        {
            "fyr": "2026",
            "exe_ymd": "20260918",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "fld_cd": "F1",
            "fld_nm": "교통및물류",
            "sect_cd": "S1",
            "sect_nm": "도로",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "120000000",
            "ep_amt": "20000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-18",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation", "budget"]
    )
    classification_vnext.classify_dataset("budget")

    rows = budget_procurement_lifecycle_vnext.prebid_budget_projects(
        fiscal_year=2026
    )

    assert len(rows) == 1
    assert rows[0]["appropriation_context_count"] == 1
    assert rows[0]["appropriation_contexts"][0]["appropriation_amount"] == 500000000


def test_project_pipeline_exposes_compiled_and_current_budget_amounts_separately():
    vnext_store.preserve_raw(
        "budget", "P-AMOUNTS",
        {
            "fyr": "2026",
            "exe_ymd": "20260918",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1",
            "cpl_amt": "100000000",
            "bdg_cash_amt": "150000000",
            "ep_amt": "30000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-18",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    classification_vnext.classify_dataset("budget")

    rows = budget_procurement_lifecycle_vnext.budget_project_procurement_rows(
        fiscal_year=2026
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["appropriation_amount"] == 100000000
    assert row["budget_amount"] == 150000000
    assert row["executed_amount"] == 30000000
    assert row["remaining_amount"] == 120000000


def test_pipeline_summary_aggregates_compiled_budget_separately_from_current_budget():
    vnext_store.preserve_raw(
        "budget", "P-AMOUNTS",
        {
            "fyr": "2026",
            "exe_ymd": "20260918",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1",
            "cpl_amt": "100000000",
            "bdg_cash_amt": "150000000",
            "ep_amt": "30000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-18",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    classification_vnext.classify_dataset("budget")

    summary = budget_procurement_lifecycle_vnext.budget_pipeline_summary(
        fiscal_year=2026
    )
    stage = summary["by_stage"]["BUDGET_ONLY"]
    assert stage["appropriation_amount"] == 100000000
    assert stage["budget_amount"] == 150000000
    assert stage["executed_amount"] == 30000000
    assert stage["remaining_amount"] == 120000000
