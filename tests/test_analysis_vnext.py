import db
import analysis_vnext
import classification_vnext
import projection_store_vnext
import vnext_store


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "analysis.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


def _mark_normalized(dataset, key):
    with db.connect() as conn:
        conn.execute("UPDATE raw_records SET normalized_at=CURRENT_TIMESTAMP WHERE dataset=? AND source_key=?", (dataset,key))


def test_coverage_reports_missing_until_every_current_raw_payload_is_classified(monkeypatch, tmp_path):
    _fresh_db(monkeypatch,tmp_path)
    sha=vnext_store.preserve_raw("bid_notice_service","A|000",{"bidNtceNm":"LED 가로등 용역"})
    vnext_store.preserve_raw("bid_notice_service","B|000",{"bidNtceNm":"청소 용역"})
    vnext_store.save_classification("bid_notice_service","A|000","LIGHTING",
                                    classifier_version=classification_vnext.CLASSIFIER_VERSION,
                                    source_payload_sha256=sha)
    first=analysis_vnext.classification_coverage(datasets=["bid_notice_service"])
    assert first["missing_total"] == 1 and first["complete"] is False
    classification_vnext.classify_dataset("bid_notice_service")
    second=analysis_vnext.classification_coverage(datasets=["bid_notice_service"])
    assert second["missing_total"] == 0 and second["complete"] is True


def test_default_analysis_returns_other_and_current_projection_facts(monkeypatch,tmp_path):
    _fresh_db(monkeypatch,tmp_path)
    vnext_store.preserve_raw("bid_notice_service","A|000",{"bidNtceNm":"LED 가로등 교체 설계"},source_date="2026-09-16")
    vnext_store.preserve_raw("bid_notice_service","B|000",{"bidNtceNm":"청사 청소 용역"},source_date="2026-09-15")
    classification_vnext.classify_dataset("bid_notice_service")
    execution="A|000|1|0"
    vnext_store.preserve_raw("opening_result_service","ORAW",{"x":1}); _mark_normalized("opening_result_service","ORAW")
    vnext_store.preserve_raw("award_result_service","ARAW",{"x":1}); _mark_normalized("award_result_service","ARAW")
    vnext_store.preserve_raw("contract_service","CRAW",{"x":1}); _mark_normalized("contract_service","CRAW")
    projection_store_vnext.replace_fact_group(execution,"opening","ORAW",
        base_facts={"notice_no":"A","notice_order":"000","business_type":"용역"},
        first_rank_vendor="가격1순위",first_rank_amount=100)
    projection_store_vnext.replace_fact_group(execution,"final_award","ARAW",
        final_vendor="최종업체",final_award_amount=110)
    projection_store_vnext.replace_fact_group(execution,"contract","CRAW",
        contract_no="C1",contract_vendor="최종업체",contract_amount=110)
    vnext_store.save_lifecycle_link("bid_notice","A|000","award_summary",execution,"HAS_AWARD_EXECUTION",confidence=1.0)
    rows=analysis_vnext.service_lifecycle_rows()
    assert [row["primary_category"] for row in rows] == ["LIGHTING","OTHER"]
    assert rows[0]["first_rank_vendor"] == "가격1순위"
    assert rows[0]["final_vendor"] == "최종업체"
    assert rows[0]["contract_no"] == "C1"


def test_multiple_rebid_executions_remain_separate_analysis_rows(monkeypatch,tmp_path):
    _fresh_db(monkeypatch,tmp_path)
    vnext_store.preserve_raw("bid_notice_service","A|000",{"bidNtceNm":"LED 보안등 교체"},source_date="2026-09-16")
    classification_vnext.classify_dataset("bid_notice_service")
    for rebid,vendor in (("0","첫개찰"),("1","재입찰")):
        execution=f"A|000|1|{rebid}"; raw=f"O{rebid}"
        vnext_store.preserve_raw("opening_result_service",raw,{"rebid":rebid}); _mark_normalized("opening_result_service",raw)
        projection_store_vnext.replace_fact_group(execution,"opening",raw,
            base_facts={"notice_no":"A","notice_order":"000","business_type":"용역"},
            first_rank_vendor=vendor,first_rank_amount=100+int(rebid))
        vnext_store.save_lifecycle_link("bid_notice","A|000","award_summary",execution,"HAS_AWARD_EXECUTION",confidence=1.0)
    rows=analysis_vnext.service_lifecycle_rows()
    assert {r["award_summary_key"] for r in rows} == {"A|000|1|0","A|000|1|1"}
    assert {r["first_rank_vendor"] for r in rows} == {"첫개찰","재입찰"}


def test_changed_projection_raw_is_hidden_until_renormalized(monkeypatch,tmp_path):
    _fresh_db(monkeypatch,tmp_path)
    vnext_store.preserve_raw("bid_notice_service","A|000",{"bidNtceNm":"LED 용역"})
    classification_vnext.classify_dataset("bid_notice_service")
    execution="A|000|1|0"
    vnext_store.preserve_raw("opening_result_service","ORAW",{"version":1})
    _mark_normalized("opening_result_service","ORAW")
    projection_store_vnext.replace_fact_group(execution,"opening","ORAW",
        base_facts={"notice_no":"A","notice_order":"000"},first_rank_vendor="과거1순위",first_rank_amount=100)
    vnext_store.save_lifecycle_link("bid_notice","A|000","award_summary",execution,"HAS_AWARD_EXECUTION",confidence=1.0)
    assert analysis_vnext.service_lifecycle_rows()[0]["first_rank_vendor"] == "과거1순위"
    vnext_store.preserve_raw("opening_result_service","ORAW",{"version":2})
    stale=analysis_vnext.service_lifecycle_rows()[0]
    assert stale["opening_current"] is False
    assert stale["first_rank_vendor"] == ""
    assert stale["first_rank_amount"] == 0


def test_retired_execution_link_is_not_exposed(monkeypatch,tmp_path):
    _fresh_db(monkeypatch,tmp_path)
    vnext_store.preserve_raw("bid_notice_service","A|000",{"bidNtceNm":"LED 용역"})
    classification_vnext.classify_dataset("bid_notice_service")
    vnext_store.upsert_award_result("A|000|1|0",notice_no="A",notice_order="000")
    vnext_store.save_lifecycle_link("bid_notice","A|000","award_summary","A|000|1|0","HAS_AWARD_EXECUTION",confidence=0.0)
    rows=analysis_vnext.service_lifecycle_rows()
    assert len(rows) == 1
    assert rows[0]["award_summary_key"] == ""
