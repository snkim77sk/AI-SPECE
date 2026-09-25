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


def test_goods_notice_read_model_and_query():
    vnext_store.preserve_raw(
        "bid_notice_goods",
        "R26BK001|000",
        {
            "bidNtceNo": "R26BK001",
            "bidNtceOrd": "000",
            "bidNtceNm": "LED 터널등 교체 구매",
            "ntceInsttNm": "조달청",
            "dminsttNm": "한국도로공사",
            "bidNtceDt": "2026-09-25 09:00",
            "bidClseDt": "2026-10-01 10:00",
            "opengDt": "2026-10-01 11:00",
            "asignBdgtAmt": "350000000",
            "presmptPrce": "340000000",
            "dtilPrdctClsfcNoNm": "LED터널등기구",
        },
        source_system="G2B",
        source_date="2026-09-25",
    )
    classification_vnext.classify_dataset("bid_notice_goods")

    rows = procurement_read_vnext.goods_notice_rows(query="도로공사")

    assert len(rows) == 1
    row = rows[0]
    assert row["notice_no"] == "R26BK001"
    assert row["notice_name"] == "LED 터널등 교체 구매"
    assert row["demand_org"] == "한국도로공사"
    assert row["budget_amount"] == 350000000
    assert row["primary_category"] == "LIGHTING"


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


def test_changed_raw_is_hidden_until_reclassified():
    vnext_store.preserve_raw(
        "bid_notice_goods",
        "CHANGE|000",
        {"bidNtceNo": "CHANGE", "bidNtceNm": "LED 보안등 구매"},
        source_system="G2B",
        source_date="2026-09-25",
    )
    classification_vnext.classify_dataset("bid_notice_goods")
    assert len(procurement_read_vnext.goods_notice_rows()) == 1

    vnext_store.preserve_raw(
        "bid_notice_goods",
        "CHANGE|000",
        {"bidNtceNo": "CHANGE", "bidNtceNm": "일반 비품 구매"},
        source_system="G2B",
        source_date="2026-09-25",
    )

    assert procurement_read_vnext.goods_notice_rows() == []
