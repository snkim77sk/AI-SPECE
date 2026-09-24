import award_projection
import classification_vnext
import db
import service_reorganize_vnext
import vnext_store
from vnext_schema import NORMALIZER_VERSION


def _seed_service_raw():
    notice = {
        "bidNtceNo": "R26BK00000001",
        "bidNtceOrd": "00",
        "bidNtceNm": "LED 가로등 교체 설계",
        "bidNtceDt": "2026-09-18",
        "dminsttCd": "4111000",
        "dminsttNm": "수원시",
    }
    vnext_store.preserve_raw(
        "bid_notice_service", "R26BK00000001|00", notice,
        source_system="G2B", source_date="2026-09-18",
    )

    opening = {
        "bidNtceNo": "R26BK00000001",
        "bidNtceOrd": "000",
        "bidClsfcNo": "1",
        "rbidNo": "0",
        "progrsDivCdNm": "개찰완료",
        "opengDt": "2026-09-18 10:00:00",
        "prtcptCnum": "7",
        "opengCorpInfo": "가격1순위업체^1111111111^REP^100000000^88.1",
    }
    execution = award_projection.execution_key(opening)
    vnext_store.preserve_raw(
        "opening_result_service", execution, opening,
        source_system="G2B", source_date="2026-09-18",
    )

    final = dict(
        opening,
        bidwinnrNm="최종낙찰업체",
        bidwinnrBizno="2222222222",
        sucsfbidAmt="101000000",
        sucsfbidRate="89.0",
    )
    vnext_store.preserve_raw(
        "award_result_service", execution, final,
        source_system="G2B", source_date="2026-09-18",
    )

    contract = {
        "dcsnCntrctNo": "C1",
        "ntceNo": "R26BK00000001000",
        "thtmCntrctAmt": "101000000",
        "corpList": "[1^단독^^최종계약업체^REP^KR^100^^CONTACT^2222222222]",
    }
    vnext_store.preserve_raw(
        "contract_service", "C1", contract,
        source_system="G2B", source_date="2026-09-18",
    )
    return execution


def _counts():
    with db.connect() as conn:
        return {
            dataset: conn.execute(
                "SELECT COUNT(*) FROM raw_records WHERE dataset=?", (dataset,)
            ).fetchone()[0]
            for dataset in service_reorganize_vnext.SERVICE_CLASSIFICATION_DATASETS
        }


def test_existing_raw_reorganization_builds_service_lifecycle_without_new_raw():
    execution = _seed_service_raw()
    before = _counts()

    result = service_reorganize_vnext.reorganize_existing_service_lifecycle()

    after = _counts()
    assert result["source_traffic"] is False
    assert result["mode"] == "EXISTING_RAW_REORGANIZATION_ONLY"
    assert result["complete"] is True
    assert result["raw_counts_unchanged"] is True
    assert before == after
    assert result["opening"]["first_rank"] == 1
    assert result["final_award"]["final_award"] == 1
    assert result["contract"]["award_summary_linked"] == 1
    with db.connect() as conn:
        row = conn.execute(
            """SELECT first_rank_vendor,final_vendor,contract_no
               FROM award_results WHERE source_key=?""",
            (execution,),
        ).fetchone()
    assert row["first_rank_vendor"] == "가격1순위업체"
    assert row["final_vendor"] == "최종낙찰업체"
    assert row["contract_no"] == "C1"


def test_existing_old_normalizer_markers_are_reorganized_without_refetch():
    execution = _seed_service_raw()
    with db.connect() as conn:
        conn.execute(
            """UPDATE raw_records
               SET normalized_at='2026-09-01 00:00:00',
                   normalizer_version='2.0.0-provenance'
               WHERE dataset IN ('opening_result_service','award_result_service','contract_service')"""
        )

    result = service_reorganize_vnext.reorganize_existing_service_lifecycle()

    assert result["complete"] is True
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT dataset,normalizer_version FROM raw_records
               WHERE dataset IN ('opening_result_service','award_result_service','contract_service')
               ORDER BY dataset"""
        ).fetchall()
    assert {row["normalizer_version"] for row in rows} == {NORMALIZER_VERSION}
    assert result["organization_status"]["normalization_pending"] == {
        "opening_result_service": 0,
        "award_result_service": 0,
        "contract_service": 0,
    }
    with db.connect() as conn:
        assert conn.execute(
            "SELECT contract_no FROM award_results WHERE source_key=?", (execution,)
        ).fetchone()["contract_no"] == "C1"


def test_reorganization_classifies_current_raw_only_after_normalization_completes():
    _seed_service_raw()

    result = service_reorganize_vnext.reorganize_existing_service_lifecycle(
        classify_batch_size=2,
    )

    assert result["classification"] is not None
    assert result["classification"]["dataset_count"] == 4
    with db.connect() as conn:
        notice = conn.execute(
            """SELECT c.primary_category
               FROM classifications c
               JOIN raw_records r
                 ON r.dataset=c.entity_type AND r.source_key=c.entity_key
                AND r.payload_sha256=c.source_payload_sha256
               WHERE r.dataset='bid_notice_service'"""
        ).fetchone()
    assert notice["primary_category"] == "LIGHTING"


def test_partial_normalization_does_not_claim_complete_or_run_classification():
    _seed_service_raw()
    # Add a second opening row so limit=1 necessarily leaves pending work.
    second = {
        "bidNtceNo": "R26BK00000002", "bidNtceOrd": "000",
        "bidClsfcNo": "1", "rbidNo": "0",
        "progrsDivCdNm": "개찰완료",
        "opengCorpInfo": "업체2^3333333333^REP^90000000^88.0",
    }
    key = award_projection.execution_key(second)
    vnext_store.preserve_raw("opening_result_service", key, second)

    result = service_reorganize_vnext.reorganize_existing_service_lifecycle(
        normalize_limit=1,
    )

    assert result["complete"] is False
    assert result["opening"]["pending"] >= 1
    assert result["classification"] is None
    assert result["source_collection_completeness_verified"] is False
