import classification_vnext
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
