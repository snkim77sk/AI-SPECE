import award_projection
import contract_projection
import db
import service_lifecycle_status_vnext
import vnext_store


def _seed_notice(key="R26BK00000001|00"):
    no, order = key.split("|", 1)
    vnext_store.preserve_raw(
        "bid_notice_service", key,
        {"bidNtceNo": no, "bidNtceOrd": order, "bidNtceNm": "LED 가로등 설계"},
        source_system="G2B", source_date="2026-09-18",
    )


def _seed_final(order="000"):
    row = {
        "bidNtceNo": "R26BK00000001", "bidNtceOrd": order,
        "bidClsfcNo": "1", "rbidNo": "0",
        "bidwinnrNm": "최종낙찰업체", "bidwinnrBizno": "1234567890",
        "sucsfbidAmt": "100000000", "sucsfbidRate": "88.5",
    }
    key = award_projection.execution_key(row)
    vnext_store.preserve_raw("award_result_service", key, row)
    award_projection.normalize_dataset("award_result_service")
    return key


def _seed_contract(key="C1", ref="R26BK00000001000"):
    row = {
        "dcsnCntrctNo": key, "ntceNo": ref, "thtmCntrctAmt": "101000000",
        "corpList": "[1^단독^^최종계약업체^REP^KR^100^^CONTACT^1234567890]",
    }
    vnext_store.preserve_raw("contract_service", key, row)
    contract_projection.normalize_contracts()
    return key


def test_empty_status_is_read_only_and_does_not_claim_source_completeness():
    status = service_lifecycle_status_vnext.service_lifecycle_organization_status()

    assert set(status["raw_counts"]) == {
        "bid_notice_service", "opening_result_service",
        "award_result_service", "contract_service",
    }
    assert sum(status["raw_counts"].values()) == 0
    assert status["read_only"] is True
    assert status["source_traffic"] is False
    assert status["scope"] == "CURRENT_STORED_RAW_ONLY"
    assert status["source_collection_completeness_verified"] is False


def test_final_award_and_contract_are_counted_after_00_000_organization():
    _seed_notice()
    execution = _seed_final("000")
    _seed_contract()

    status = service_lifecycle_status_vnext.service_lifecycle_organization_status()

    assert status["raw_counts"]["bid_notice_service"] == 1
    assert status["raw_counts"]["award_result_service"] == 1
    assert status["raw_counts"]["contract_service"] == 1
    assert status["projected"]["final_award_executions"] == 1
    assert status["projected"]["final_award_with_vendor_or_amount"] == 1
    assert status["projected"]["contracts"] == 1
    assert status["projected"]["contracts_with_notice"] == 1
    assert status["projected"]["contracts_assigned_to_unique_final_award"] == 1
    assert status["notice_organization"]["notices_with_final_award"] == 1
    assert status["notice_organization"]["notices_with_contract"] == 1
    assert status["needs_review"]["final_award_without_notice_link"] == []
    assert status["needs_review"]["contract_notice_unresolved"] == []
    assert status["needs_review"]["contract_final_award_unassigned"] == []
    with db.connect() as conn:
        assert conn.execute(
            "SELECT contract_no FROM award_results WHERE source_key=?", (execution,)
        ).fetchone()["contract_no"] == "C1"


def test_unresolved_contract_is_visible_in_needs_review_not_hidden():
    _seed_contract(key="ORPHAN", ref="UNKNOWN000")

    status = service_lifecycle_status_vnext.service_lifecycle_organization_status()

    assert status["projected"]["contracts"] == 1
    assert status["projected"]["contracts_with_notice"] == 0
    assert status["needs_review"]["contract_notice_unresolved"] == ["ORPHAN"]


def test_old_normalizer_version_is_visible_as_local_pending_work():
    row = {
        "bidNtceNo": "R26BK00000001", "bidNtceOrd": "000",
        "bidClsfcNo": "1", "rbidNo": "0",
        "bidwinnrNm": "최종낙찰업체",
    }
    key = award_projection.execution_key(row)
    vnext_store.preserve_raw("award_result_service", key, row)
    with db.connect() as conn:
        conn.execute(
            """UPDATE raw_records
               SET normalized_at='2026-09-01 00:00:00',
                   normalizer_version='2.0.0-provenance'
               WHERE dataset='award_result_service' AND source_key=?""",
            (key,),
        )

    status = service_lifecycle_status_vnext.service_lifecycle_organization_status()

    assert status["normalization_pending"]["award_result_service"] == 1
    assert status["source_collection_completeness_verified"] is False
