import classification_vnext
import db
import shopping_store_v41
import procurement_read_vnext
import vnext_store


def test_shopping_read_model_uses_current_post_raw_classification_only():
    vnext_store.preserve_raw(
        "shopping_delivery",
        "S1",
        {
            "dlvrReqNo": "REQ-1",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "dtilPrdctClsfcNoNm": "LED가로등기구",
            "prdctIdntNo": "12345678",
            "prdctIdntNoNm": "가로등 A",
            "dminsttNm": "수원시",
            "cntrctCorpNm": "테스트조명",
            "cntrctCorpBizno": "1234567890",
            "dlvrReqQty": "10",
            "unitPrice": "100000",
            "dlvrReqAmt": "1000000",
        },
        source_system="G2B",
        source_date="2026-09-25",
    )
    vnext_store.preserve_raw(
        "shopping_delivery",
        "S2",
        {
            "dlvrReqNo": "REQ-2",
            "prdctSno": "1",
            "dtilPrdctClsfcNoNm": "일반 사무용품",
            "prdctIdntNoNm": "복사용지",
            "dminsttNm": "수원시",
            "cntrctCorpNm": "사무업체",
            "dlvrReqAmt": "500000",
        },
        source_system="G2B",
        source_date="2026-09-25",
    )
    classification_vnext.classify_dataset("shopping_delivery")

    rows = procurement_read_vnext.shopping_rows()

    assert len(rows) == 1
    assert rows[0]["delivery_req_no"] == "REQ-1"
    assert rows[0]["detail_item_no"] == "3911160302"
    assert rows[0]["vendor_name"] == "테스트조명"
    assert rows[0]["amount"] == 1000000
    assert rows[0]["primary_category"] == "LIGHTING"


def test_local_collector_role_reads_normalized_shopping_not_legacy_raw(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    shopping_store_v41.ensure_schema()
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "LOCAL-NORMALIZED",
        {
            "dlvrReqNo": "LOCAL-NORMALIZED",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20261001",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 로컬 정규화",
            "cntrctCorpNm": "로컬조명",
        },
        source_system="G2B",
        source_operation="local-test",
        source_date="2026-10-01",
    )
    # A legacy RAW fixture must not win when the runtime role is LOCAL_COLLECTOR.
    vnext_store.preserve_raw(
        "shopping_delivery",
        "LOCAL-LEGACY",
        {
            "dlvrReqNo": "LOCAL-LEGACY",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctIdntNoNm": "LED legacy",
        },
        source_system="G2B",
        source_date="2026-10-01",
    )
    classification_vnext.classify_dataset("shopping_delivery")

    rows = procurement_read_vnext.shopping_rows(limit=None)

    assert [row["source_key"] for row in rows] == ["LOCAL-NORMALIZED"]
    assert rows[0]["delivery_req_no"] == "LOCAL-NORMALIZED"


def test_normalized_shopping_date_range_is_inclusive_for_full_year(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    shopping_store_v41.ensure_schema()

    for key, source_date in (
        ("DATE-JAN1", "2026-01-01"),
        ("DATE-JUN", "2026-06-15"),
        ("DATE-DEC31", "2026-12-31"),
        ("DATE-NEXT", "2027-01-01"),
    ):
        shopping_store_v41.preserve_record(
            "shopping_delivery",
            key,
            {
                "dlvrReqNo": key,
                "dlvrReqChgOrd": "0",
                "prdctSno": "1",
                "dlvrReqRcptDate": source_date.replace("-", ""),
                "dtilPrdctClsfcNo": "3911160302",
                "prdctNm": "LED 날짜조회",
                "cntrctCorpNm": "날짜조회조명",
            },
            source_system="G2B",
            source_operation="date-range-test",
            source_date=source_date,
        )

    rows = procurement_read_vnext.shopping_rows(
        categories=("LIGHTING",),
        start_date="2026-01-01",
        end_date="2026-12-31",
        limit=None,
    )

    assert [row["source_key"] for row in rows] == [
        "DATE-DEC31",
        "DATE-JUN",
        "DATE-JAN1",
    ]


def test_legacy_shopping_date_filter_runs_before_limit():
    for key, source_date in (
        ("DATE-NEW", "2027-01-01"),
        ("DATE-IN", "2026-06-15"),
    ):
        vnext_store.preserve_raw(
            "shopping_delivery",
            key,
            {
                "dlvrReqNo": key,
                "prdctSno": "1",
                "dtilPrdctClsfcNo": "3911160302",
                "prdctIdntNoNm": "LED 날짜 필터",
            },
            source_system="G2B",
            source_date=source_date,
        )
    classification_vnext.classify_dataset("shopping_delivery")

    rows = procurement_read_vnext.shopping_rows(
        start_date="2026-01-01",
        end_date="2026-12-31",
        limit=1,
    )

    assert len(rows) == 1
    assert rows[0]["delivery_req_no"] == "DATE-IN"


def test_production_shopping_read_path_does_not_run_schema_ddl():
    source = __import__("pathlib").Path("procurement_read_vnext.py").read_text(encoding="utf-8")
    block = source.split("def shopping_rows", 1)[1].split("def vendor_rows", 1)[0]
    assert "if test_mode:" in block
    assert "shopping_store_v41.ensure_schema()" in block
    assert block.index("if test_mode:") < block.index("shopping_store_v41.ensure_schema()")


def test_goods_notice_read_model_is_removed():
    assert not hasattr(procurement_read_vnext, "goods_notice_rows")


def test_vendor_summary_is_derived_from_current_vnext_rows():
    for idx, amount, org in ((1, 1000000, "A기관"), (2, 2000000, "B기관")):
        vnext_store.preserve_raw(
            "shopping_delivery",
            f"V{idx}",
            {
                "dlvrReqNo": f"REQ-{idx}",
                "prdctSno": "1",
                "dtilPrdctClsfcNo": "3911160302",
                "prdctIdntNoNm": "LED가로등기구",
                "dminsttNm": org,
                "cntrctCorpNm": "테스트조명",
                "cntrctCorpBizno": "1234567890",
                "dlvrReqAmt": str(amount),
            },
            source_system="G2B",
            source_date="2026-09-25",
        )
    classification_vnext.classify_dataset("shopping_delivery")

    rows = procurement_read_vnext.vendor_rows()

    assert len(rows) == 1
    row = rows[0]
    assert row["vendor_name"] == "테스트조명"
    assert row["shopping_rows"] == 2
    assert row["shopping_amount"] == 3000000
    assert row["total_amount"] == 3000000
    assert row["demand_org_count"] == 2
    assert row["categories"] == ["LIGHTING"]


def test_changed_shopping_raw_is_hidden_until_reclassified():
    vnext_store.preserve_raw(
        "shopping_delivery",
        "CHANGE",
        {
            "dlvrReqNo": "CHANGE",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctIdntNoNm": "LED 보안등",
        },
        source_system="G2B",
        source_date="2026-09-25",
    )
    classification_vnext.classify_dataset("shopping_delivery")
    assert len(procurement_read_vnext.shopping_rows()) == 1

    vnext_store.preserve_raw(
        "shopping_delivery",
        "CHANGE",
        {
            "dlvrReqNo": "CHANGE",
            "prdctSno": "1",
            "dtilPrdctClsfcNoNm": "일반 사무용품",
            "prdctIdntNoNm": "복사용지",
        },
        source_system="G2B",
        source_date="2026-09-25",
    )

    assert procurement_read_vnext.shopping_rows() == []


def test_shopping_query_searches_full_store_before_limit():
    rows = [
        ("NEW", "2026-09-25", "LED 투광등", "최근기관"),
        ("OLD", "2026-09-20", "LED 가로등", "찾을기관"),
    ]
    for key, source_date, item_name, org in rows:
        vnext_store.preserve_raw(
            "shopping_delivery",
            key,
            {
                "dlvrReqNo": key,
                "prdctSno": "1",
                "dtilPrdctClsfcNo": "3911160302",
                "prdctIdntNoNm": item_name,
                "dminsttNm": org,
                "cntrctCorpNm": "검색업체",
                "cntrctCorpBizno": "1234567890",
            },
            source_system="G2B",
            source_date=source_date,
        )
    classification_vnext.classify_dataset("shopping_delivery")

    result = procurement_read_vnext.shopping_rows(query="찾을기관", limit=1)

    assert len(result) == 1
    assert result[0]["delivery_req_no"] == "OLD"


def test_same_vendor_name_with_different_business_numbers_stays_separate():
    for key, bizno, amount in (
        ("A", "111-11-11111", 1000000),
        ("B", "222-22-22222", 2000000),
    ):
        vnext_store.preserve_raw(
            "shopping_delivery",
            key,
            {
                "dlvrReqNo": key,
                "prdctSno": "1",
                "dtilPrdctClsfcNo": "3911160302",
                "prdctIdntNoNm": "LED가로등기구",
                "cntrctCorpNm": "동일상호조명",
                "cntrctCorpBizno": bizno,
                "dlvrReqAmt": str(amount),
            },
            source_system="G2B",
            source_date="2026-09-25",
        )
    classification_vnext.classify_dataset("shopping_delivery")

    rows = procurement_read_vnext.vendor_rows(limit=None)

    assert len(rows) == 2
    assert {row["vendor_bizno"] for row in rows} == {"1111111111", "2222222222"}
    assert {row["total_amount"] for row in rows} == {1000000, 2000000}


def test_procurement_summary_requests_unbounded_current_rows(monkeypatch):
    calls = {}

    def fake_shopping(**kwargs):
        calls["shopping"] = kwargs.get("limit")
        return [{"amount": 10}]

    def fake_vendors(**kwargs):
        calls["vendors"] = kwargs.get("limit")
        return [{"total_amount": 20}]

    monkeypatch.setattr(procurement_read_vnext, "shopping_rows", fake_shopping)
    monkeypatch.setattr(procurement_read_vnext, "vendor_rows", fake_vendors)

    summary = procurement_read_vnext.procurement_summary()

    assert calls == {"shopping": None, "vendors": None}
    assert summary["shopping_target_rows"] == 1
    assert summary["vendors"] == 1
    assert summary["shopping_amount"] == 10
    assert summary["vendor_total_amount"] == 20



def test_shopping_item_amount_never_reuses_request_total():
    vnext_store.preserve_raw(
        "shopping_delivery",
        "AMOUNT-ITEM",
        {
            "dlvrReqNo": "REQ-AMOUNT",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "dtilPrdctClsfcNoNm": "LED가로등기구",
            "prdctIdntNoNm": "가로등 금액검증",
            "dminsttNm": "검증기관",
            "cntrctCorpNm": "검증업체",
            "prdctQty": "2",
            "prdctUprc": "100",
            "prdctAmt": "150",
            "dlvrReqAmt": "9999",
            "fnlDlvrReqYn": "Y",
        },
        source_system="G2B",
        source_date="2026-09-05",
    )
    classification_vnext.classify_dataset("shopping_delivery")
    row = procurement_read_vnext.shopping_rows(query="REQ-AMOUNT", limit=10)[0]
    assert row["amount"] == 150
    assert row["delivery_req_total_amount"] == 9999
    assert row["delivery_change_order"] == "0"
    assert row["is_final_delivery_request"] == "Y"


def test_shopping_item_amount_falls_back_to_unit_price_times_quantity():
    vnext_store.preserve_raw(
        "shopping_delivery",
        "AMOUNT-CALC",
        {
            "dlvrReqNo": "REQ-CALC",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctIdntNoNm": "가로등 계산검증",
            "dminsttNm": "검증기관",
            "cntrctCorpNm": "검증업체",
            "prdctQty": "3",
            "prdctUprc": "200",
            "dlvrReqAmt": "10000",
        },
        source_system="G2B",
        source_date="2026-09-05",
    )
    classification_vnext.classify_dataset("shopping_delivery")
    row = procurement_read_vnext.shopping_rows(query="REQ-CALC", limit=10)[0]
    assert row["amount"] == 600
    assert row["delivery_req_total_amount"] == 10000


def test_production_read_model_hides_inactive_but_preserves_history(monkeypatch):
    monkeypatch.setenv("G2B_DB_BACKEND", "sqlite")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    shopping_store_v41.ensure_schema()

    base = {
        "dlvrReqNo": "ACTIVE-HISTORY",
        "prdctSno": "1",
        "dlvrReqRcptDate": "20260910",
        "dtilPrdctClsfcNo": "3911160302",
        "prdctNm": "LED 보안등기구",
        "cntrctCorpNm": "이력검증조명",
        "prdctAmt": "1000",
    }
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "ACTIVE-HISTORY-0",
        {**base, "dlvrReqChgOrd": "0"},
        source_system="G2B",
        source_operation="test",
        source_date="2026-09-10",
    )
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "ACTIVE-HISTORY-1",
        {**base, "dlvrReqChgOrd": "1", "prdctAmt": "1200"},
        source_system="G2B",
        source_operation="test",
        source_date="2026-09-10",
    )
    with db.connect() as conn:
        conn.execute(
            """UPDATE shopping_records
               SET is_active=0,
                   inactive_reason='MISSING_FROM_COMPLETE_SOURCE'
               WHERE source_key='ACTIVE-HISTORY-0'"""
        )

    current = procurement_read_vnext.shopping_rows(
        query="ACTIVE-HISTORY", limit=None
    )
    history = procurement_read_vnext.shopping_rows(
        query="ACTIVE-HISTORY", limit=None, include_inactive=True
    )
    summary = procurement_read_vnext.procurement_summary()

    assert len(current) == 1
    assert current[0]["delivery_change_order"] == "1"
    assert len(history) == 2
    assert {int(row["is_active"]) for row in history} == {0, 1}
    assert summary["shopping_target_rows"] >= 1
    assert summary["shopping_active_history_rows"] >= 1
    assert summary["shopping_inactive_history_rows"] >= 1
    assert summary["shopping_history_rows"] >= 2


def test_vendor_summary_uses_latest_delivery_change_order_only():
    rows = [
        ("C0", "0", "1000", "N"),
        ("C1", "1", "1200", "Y"),
    ]
    for key, change, amount, final in rows:
        vnext_store.preserve_raw(
            "shopping_delivery",
            key,
            {
                "dlvrReqNo": "REQ-CHANGE",
                "dlvrReqChgOrd": change,
                "prdctSno": "1",
                "dtilPrdctClsfcNo": "3911160302",
                "prdctIdntNoNm": "LED 변경주문",
                "cntrctCorpNm": "변경조명",
                "cntrctCorpBizno": "1234567890",
                "prdctAmt": amount,
                "fnlDlvrReqYn": final,
            },
            source_system="G2B",
            source_date="2026-09-01",
        )
    classification_vnext.classify_dataset("shopping_delivery")

    history = procurement_read_vnext.shopping_rows(query="REQ-CHANGE", limit=None)
    assert len(history) == 2

    vendors = procurement_read_vnext.vendor_rows(query="변경조명", limit=None)
    assert len(vendors) == 1
    assert vendors[0]["shopping_rows"] == 1
    assert vendors[0]["shopping_amount"] == 1200

    summary = procurement_read_vnext.procurement_summary()
    assert summary["shopping_history_rows"] >= 2
