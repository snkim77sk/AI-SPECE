import shopping_vnext


def test_source_key_is_not_based_on_target_product_classification():
    ordinary = {
        "dlvrReqNo": "REQ-1", "prdctSno": "1", "prdctIdntNo": "12345678",
        "prdctIdntNoNm": "일반 사무용품",
    }
    lighting = {
        "dlvrReqNo": "REQ-2", "prdctSno": "1", "prdctIdntNo": "87654321",
        "prdctIdntNoNm": "LED가로등기구",
    }
    assert shopping_vnext._source_key(ordinary)
    assert shopping_vnext._source_key(lighting)
    assert shopping_vnext._source_key(ordinary) != shopping_vnext._source_key(lighting)


def test_fetch_page_builds_no_detail_item_filter(monkeypatch):
    seen = {}
    monkeypatch.setattr(shopping_vnext, "_service_key", lambda: "KEY")
    def fake_request(url, kind):
        seen["url"] = url
        seen["kind"] = kind
        return [], 0
    monkeypatch.setattr(shopping_vnext, "_request", fake_request)
    shopping_vnext.fetch_page("2026-09-01", "2026-09-15", page=1, rows=100)
    assert seen["kind"] == "shop"
    assert "dtilPrdctClsfcNo" not in seen["url"]
    assert "detailItem" not in seen["url"]
    assert "inqryBgnDate=20260901" in seen["url"]
    assert "inqryEndDate=20260915" in seen["url"]
