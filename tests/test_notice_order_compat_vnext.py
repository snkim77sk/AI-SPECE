import award_projection
import contract_projection
import db
import vnext_store
import vnext_schema
from notice_identity_vnext import canonical_notice_order, notice_keys_equivalent


def _final_award(order="000"):
    return {
        "bidNtceNo": "R26BK00000001",
        "bidNtceOrd": order,
        "bidClsfcNo": "1",
        "rbidNo": "0",
        "bidwinnrNm": "최종낙찰업체",
        "bidwinnrBizno": "1234567890",
        "sucsfbidAmt": "100000000",
        "sucsfbidRate": "88.5",
    }


def test_numeric_notice_order_padding_is_equivalent_without_rewriting_raw_keys():
    assert canonical_notice_order("00") == "0"
    assert canonical_notice_order("000") == "0"
    assert notice_keys_equivalent("R26BK00000001|00", "R26BK00000001|000")
    assert not notice_keys_equivalent("R26BK00000001|01", "R26BK00000001|000")


def test_final_award_with_000_links_to_stored_notice_with_00():
    vnext_store.preserve_raw(
        "bid_notice_service", "R26BK00000001|00",
        {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "00", "bidNtceNm": "LED 가로등 설계"},
    )
    final = _final_award("000")
    execution = award_projection.execution_key(final)
    vnext_store.preserve_raw("award_result_service", execution, final)

    result = award_projection.project_final_award_row(final, raw_source_key=execution)

    assert result["award_summary_key"] == "R26BK00000001|000|1|0"
    with db.connect() as conn:
        links = [dict(row) for row in conn.execute(
            """SELECT from_key,to_key FROM lifecycle_links
               WHERE from_type='bid_notice' AND link_type='HAS_AWARD_EXECUTION'
                 AND confidence>0"""
        )]
    assert links == [{
        "from_key": "R26BK00000001|00",
        "to_key": "R26BK00000001|000|1|0",
    }]
    assert contract_projection.resolve_unique_final_award_key(
        "R26BK00000001|00"
    ) == execution


def test_contract_combined_notice_reference_resolves_000_to_stored_00_and_final_award():
    vnext_store.preserve_raw(
        "bid_notice_service", "R26BK00000001|00",
        {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "00"},
    )
    # Different official notice order must not be confused with zero-padded order 00.
    vnext_store.preserve_raw(
        "bid_notice_service", "R26BK00000001|01",
        {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "01"},
    )
    final = _final_award("000")
    execution = award_projection.execution_key(final)
    vnext_store.preserve_raw("award_result_service", execution, final)
    award_projection.project_final_award_row(final, raw_source_key=execution)

    contract = {
        "dcsnCntrctNo": "C1",
        "ntceNo": "R26BK00000001000",
        "thtmCntrctAmt": "101000000",
        "corpList": "[1^단독^^최종계약업체^REP^KR^100^^CONTACT^1234567890]",
    }
    vnext_store.preserve_raw("contract_service", "C1", contract)
    result = contract_projection.project_contract_row(contract, raw_source_key="C1")

    assert result["notice_key"] == "R26BK00000001|00"
    assert result["award_summary_key"] == execution
    assert result["award_summary_linked"] is True
    with db.connect() as conn:
        row = conn.execute(
            "SELECT contract_no,contract_vendor FROM award_results WHERE source_key=?",
            (execution,),
        ).fetchone()
    assert row["contract_no"] == "C1"
    assert row["contract_vendor"] == "최종계약업체"


def test_old_normalizer_version_is_reprocessed_from_existing_raw_without_refetch():
    vnext_store.preserve_raw(
        "bid_notice_service", "R26BK00000001|00",
        {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "00"},
    )
    final = _final_award("000")
    execution = award_projection.execution_key(final)
    vnext_store.preserve_raw("award_result_service", execution, final)
    with db.connect() as conn:
        conn.execute(
            """UPDATE raw_records
               SET normalized_at='2026-09-01 00:00:00',
                   normalizer_version='2.0.0-provenance'
               WHERE dataset='award_result_service' AND source_key=?""",
            (execution,),
        )

    result = award_projection.normalize_dataset("award_result_service")

    assert result["processed"] == 1
    assert result["complete"] is True
    with db.connect() as conn:
        raw = conn.execute(
            """SELECT normalizer_version FROM raw_records
               WHERE dataset='award_result_service' AND source_key=?""",
            (execution,),
        ).fetchone()
        link = conn.execute(
            """SELECT from_key FROM lifecycle_links
               WHERE from_type='bid_notice' AND to_type='award_summary'
                 AND to_key=? AND link_type='HAS_AWARD_EXECUTION' AND confidence>0""",
            (execution,),
        ).fetchone()
    assert raw["normalizer_version"] == vnext_schema.NORMALIZER_VERSION
    assert link["from_key"] == "R26BK00000001|00"
