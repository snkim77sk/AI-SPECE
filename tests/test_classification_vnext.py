import db
import classification_vnext
import vnext_schema
import vnext_store


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "classification.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


def test_exact_detail_item_rules_run_only_as_post_raw_classification():
    lighting = classification_vnext.classify_payload(
        "shopping_delivery",
        {"dtilPrdctClsfcNo": "3911160302", "prdctIdntNoNm": "일반명"},
    )
    pole = classification_vnext.classify_payload(
        "shopping_delivery",
        {"dtilPrdctClsfcNo": "3911152601", "prdctIdntNoNm": "일반명"},
    )
    solar = classification_vnext.classify_payload(
        "shopping_delivery",
        {"dtilPrdctClsfcNo": "2611160701", "prdctIdntNoNm": "일반명"},
    )
    assert lighting["primary_category"] == "LIGHTING"
    assert lighting["confidence"] == 1.0
    assert pole["primary_category"] == "POLE"
    assert solar["primary_category"] == "SOLAR"


def test_text_rules_classify_lighting_pole_electrical_and_solar():
    assert classification_vnext.classify_payload(
        "bid_notice_goods", {"bidNtceNm": "LED 가로등기구 구매"}
    )["subcategory"] == "STREET_LIGHT"
    assert classification_vnext.classify_payload(
        "bid_notice_goods", {"bidNtceNm": "스테인리스 가로등주 제작 구매"}
    )["primary_category"] == "POLE"
    assert classification_vnext.classify_payload(
        "bid_notice_service", {"bidNtceNm": "청사 전기설계 용역"}
    )["subcategory"] == "ELECTRICAL_DESIGN"
    assert classification_vnext.classify_payload(
        "budget", {"dbiz_nm": "공공건물 태양광 발전설비 설치"}
    )["primary_category"] == "SOLAR"


def test_non_target_rows_are_classified_other_not_discarded():
    result = classification_vnext.classify_payload(
        "bid_notice_service", {"bidNtceNm": "청사 정기 청소용역", "corpNm": "LED조명주식회사"}
    )
    assert result["primary_category"] == "OTHER"
    assert result["reason"] == "no post-RAW target-domain rule matched"


def test_classify_dataset_writes_one_result_for_every_raw_row(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    rows = [
        ("A|000", {"bidNtceNm": "LED 터널등 교체"}),
        ("B|000", {"bidNtceNm": "청사 정기 청소용역"}),
        ("C|000", {"bidNtceNm": "전기감리 용역"}),
    ]
    for key, payload in rows:
        vnext_store.preserve_raw("bid_notice_service", key, payload, source_system="G2B")

    report = classification_vnext.classify_dataset("bid_notice_service", batch_size=2)
    assert report["classified"] == 3
    assert report["counts"] == {"ELECTRICAL": 1, "LIGHTING": 1, "OTHER": 1}

    with db.connect() as conn:
        raw_count = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_records WHERE dataset='bid_notice_service'"
        ).fetchone()["n"]
        classified = conn.execute(
            "SELECT primary_category,COUNT(*) AS n FROM classifications "
            "WHERE entity_type='bid_notice_service' AND classifier_version=? "
            "GROUP BY primary_category ORDER BY primary_category",
            (vnext_schema.CLASSIFIER_VERSION,),
        ).fetchall()
    assert raw_count == 3
    assert {r["primary_category"]: r["n"] for r in classified} == {
        "ELECTRICAL": 1, "LIGHTING": 1, "OTHER": 1,
    }


def test_reclassification_preserves_old_versions(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    vnext_store.preserve_raw(
        "bid_notice_goods", "A|000", {"bidNtceNm": "LED 투광등 구매"}, source_system="G2B"
    )
    classification_vnext.classify_dataset("bid_notice_goods", classifier_version="test-v1")
    classification_vnext.classify_dataset("bid_notice_goods", classifier_version="test-v2")
    classification_vnext.classify_dataset("bid_notice_goods", classifier_version="test-v2")

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT classifier_version,primary_category FROM classifications "
            "WHERE entity_type='bid_notice_goods' AND entity_key='A|000' ORDER BY classifier_version"
        ).fetchall()
    assert [(r["classifier_version"], r["primary_category"]) for r in rows] == [
        ("test-v1", "LIGHTING"), ("test-v2", "LIGHTING")
    ]


def test_schema_refreshes_classifier_version_setting(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('classifier_version','old') "
            "ON CONFLICT(key) DO UPDATE SET value='old'"
        )
        vnext_schema.ensure_vnext_schema(conn)
        value = conn.execute(
            "SELECT value FROM app_settings WHERE key='classifier_version'"
        ).fetchone()["value"]
    assert value == vnext_schema.CLASSIFIER_VERSION == "1.1.0-rule-v1"
