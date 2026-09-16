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


def test_coverage_reports_missing_until_every_raw_row_is_classified(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    vnext_store.preserve_raw("bid_notice_service", "A|000", {"bidNtceNm": "LED 가로등 용역"})
    vnext_store.preserve_raw("bid_notice_service", "B|000", {"bidNtceNm": "청소 용역"})

    vnext_store.save_classification(
        "bid_notice_service", "A|000", "LIGHTING", subcategory="STREET_LIGHT",
        classifier_version=classification_vnext.CLASSIFIER_VERSION,
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


def test_default_analysis_returns_other_as_well_as_target_rows(monkeypatch, tmp_path):
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
    vnext_store.upsert_award_result(
        "A|000", notice_no="A", notice_order="000", business_type="용역",
        first_rank_vendor="가격1순위", first_rank_amount=100,
        final_vendor="최종업체", final_award_amount=110,
        contract_no="C1", contract_vendor="최종업체", contract_amount=110,
    )

    all_rows = analysis_vnext.service_lifecycle_rows()
    assert [row["primary_category"] for row in all_rows] == ["LIGHTING", "OTHER"]
    assert all_rows[0]["first_rank_vendor"] == "가격1순위"
    assert all_rows[0]["final_vendor"] == "최종업체"
    assert all_rows[0]["contract_no"] == "C1"

    target_rows = analysis_vnext.target_service_lifecycle_rows()
    assert len(target_rows) == 1
    assert target_rows[0]["primary_category"] == "LIGHTING"
    assert target_rows[0]["notice_name"] == "LED 가로등 교체 설계"


def test_explicit_empty_category_filter_returns_no_rows(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    assert analysis_vnext.service_lifecycle_rows(categories=[]) == []
