import analysis_vnext
import award_projection
import budget_procurement_lifecycle_vnext
import budget_projection_vnext
import budget_read_vnext
import classification_vnext
import contract_projection
import db
import vnext_store


def _budget(name="LED 가로등 교체"):
    vnext_store.preserve_raw(
        "budget", "P1",
        {
            "fyr": "2026", "exe_ymd": "20260918",
            "laf_cd": "4111000", "laf_hg_nm": "수원시",
            "dbiz_cd": "P1", "dbiz_nm": name, "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "100000000", "ep_amt": "10000000",
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
