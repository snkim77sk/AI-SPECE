import award_projection
import contract_projection
import db
import analysis_vnext
import classification_vnext
import vnext_store


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "analysis.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


def test_coverage_reports_missing_until_every_current_raw_payload_is_classified(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    sha_a = vnext_store.preserve_raw("bid_notice_service", "A|000", {"bidNtceNm": "LED 가로등 용역"})
    vnext_store.preserve_raw("bid_notice_service", "B|000", {"bidNtceNm": "청소 용역"})

    vnext_store.save_classification(
        "bid_notice_service", "A|000", "LIGHTING", subcategory="STREET_LIGHT",
        classifier_version=classification_vnext.CLASSIFIER_VERSION,
        source_payload_sha256=sha_a,
    )
    first = analysis_vnext.classification_coverage(datasets=["bid_notice_service"])
    assert first["raw_total"] == 2
    assert first["classified_total"] == 1
    assert first["missing_total"] == 1
    assert first["complete"] is False

    classification_vnext.classify_dataset("bid_notice_service")
    second = analysis_vnext.classification_coverage(datasets=["bid_notice_service"])
    assert second["classified_total"] == 2
    assert second["missing_total"] == 0
    assert second["complete"] is True
    assert second["datasets"]["bid_notice_service"]["categories"] == {"LIGHTING": 1, "OTHER": 1}


def test_stale_classification_is_not_counted_as_current_coverage(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    dataset = "bid_notice_service"
    key = "A|000"
    vnext_store.preserve_raw(dataset, key, {"bidNtceNm": "청소 용역"})
    classification_vnext.classify_dataset(dataset)
    assert analysis_vnext.classification_coverage(datasets=[dataset])["complete"] is True

    vnext_store.preserve_raw(dataset, key, {"bidNtceNm": "LED 가로등 용역"})
    stale = analysis_vnext.classification_coverage(datasets=[dataset])
    assert stale["classified_total"] == 0
    assert stale["missing_total"] == 1
    assert stale["complete"] is False


def test_default_analysis_returns_other_and_execution_level_target_rows(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    vnext_store.preserve_raw(
        "bid_notice_service", "A|000",
        {"bidNtceNm": "LED 가로등 교체 설계", "dminsttNm": "A기관", "bidNtceDt": "2026-09-16"},
        source_system="G2B", source_date="2026-09-16",
    )
    vnext_store.preserve_raw(
        "bid_notice_service", "B|000",
        {"bidNtceNm": "청사 청소 용역", "dminsttNm": "B기관", "bidNtceDt": "2026-09-15"},
        source_system="G2B", source_date="2026-09-15",
    )
    classification_vnext.classify_dataset("bid_notice_service")
    execution = "A|000|1|0"
    opening = {"bidNtceNo":"A","bidNtceOrd":"000","bidClsfcNo":"1","rbidNo":"0",
               "progrsDivCdNm":"개찰완료","opengCorpInfo":"가격1순위^1234567890^REP^100^88.1"}
    final = dict(opening, bidwinnrNm="최종업체",bidwinnrBizno="1234567890",sucsfbidAmt="110")
    contract = {"bidNtceNo":"A","bidNtceOrd":"000","dcsnCntrctNo":"C1","thtmCntrctAmt":"110",
                "corpList":"[1^단독^^최종업체^REP^KR^100^^CONTACT^1234567890]"}
    vnext_store.preserve_raw("opening_result_service",execution,opening)
    award_projection.project_opening_row(opening,raw_source_key=execution)
    vnext_store.preserve_raw("award_result_service",execution,final)
    award_projection.project_final_award_row(final,raw_source_key=execution)
    vnext_store.preserve_raw("contract_service","C1",contract)
    contract_projection.project_contract_row(contract,raw_source_key="C1")
    vnext_store.save_lifecycle_link(
        "bid_notice", "A|000", "award_summary", execution, "HAS_AWARD_EXECUTION",
        confidence=1.0, reason="test execution",
    )

    all_rows = analysis_vnext.service_lifecycle_rows()
    assert [row["primary_category"] for row in all_rows] == ["LIGHTING", "OTHER"]
    assert all_rows[0]["award_summary_key"] == execution
    assert all_rows[0]["first_rank_vendor"] == "가격1순위"
    assert all_rows[0]["final_vendor"] == "최종업체"
    assert all_rows[0]["contract_no"] == "C1"

    target_rows = analysis_vnext.target_service_lifecycle_rows()
    assert len(target_rows) == 1
    assert target_rows[0]["primary_category"] == "LIGHTING"
    assert target_rows[0]["notice_name"] == "LED 가로등 교체 설계"


def test_multiple_rebid_executions_remain_separate_analysis_rows(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    vnext_store.preserve_raw(
        "bid_notice_service", "A|000", {"bidNtceNm": "LED 보안등 교체"},
        source_system="G2B", source_date="2026-09-16",
    )
    classification_vnext.classify_dataset("bid_notice_service")
    for rebid, vendor in (("0", "첫개찰"), ("1", "재입찰")):
        execution = f"A|000|1|{rebid}"
        opening = {"bidNtceNo":"A","bidNtceOrd":"000","bidClsfcNo":"1","rbidNo":rebid,
                   "progrsDivCdNm":"개찰완료","opengCorpInfo":f"{vendor}^1234567890^REP^{100+int(rebid)}^88.1"}
        vnext_store.preserve_raw("opening_result_service",execution,opening)
        award_projection.project_opening_row(opening,raw_source_key=execution)
        vnext_store.save_lifecycle_link(
            "bid_notice", "A|000", "award_summary", execution, "HAS_AWARD_EXECUTION",
            confidence=1.0, reason="test execution",
        )

    rows = analysis_vnext.service_lifecycle_rows()
    assert len(rows) == 2
    assert {row["award_summary_key"] for row in rows} == {"A|000|1|0", "A|000|1|1"}
    assert {row["first_rank_vendor"] for row in rows} == {"첫개찰", "재입찰"}


def test_explicit_empty_category_filter_returns_no_rows(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    assert analysis_vnext.service_lifecycle_rows(categories=[]) == []
