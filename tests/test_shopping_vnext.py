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
    assert seen["kind"] == "shopping"
    assert "dtilPrdctClsfcNo" not in seen["url"]
    assert "detailItem" not in seen["url"]
    assert "inqryBgnDate=20260901" in seen["url"]
    assert "inqryEndDate=20260915" in seen["url"]


def test_missing_total_full_shopping_page_stays_running(monkeypatch):
    checkpoints = []
    rows = [{"dlvrReqNo": "REQ", "prdctSno": str(i)} for i in range(2)]
    monkeypatch.setattr(shopping_vnext, "get_checkpoint", lambda dataset, scope: None)
    monkeypatch.setattr(shopping_vnext, "fetch_page", lambda *a, **k: (rows, 0))
    monkeypatch.setattr(shopping_vnext, "preserve_raw", lambda *a, **k: None)
    monkeypatch.setattr(
        shopping_vnext,
        "save_checkpoint",
        lambda dataset, scope, **values: checkpoints.append((dataset, scope, values)),
    )

    result = shopping_vnext.collect_all(
        "2026-09-16", "2026-09-16", page_size=2, max_pages=1, resume=False
    )
    assert result["fetched"] == 2
    assert result["source_total"] == 0
    assert result["complete"] is False
    assert checkpoints[-1][2]["status"] == "RUNNING"
    assert checkpoints[-1][2]["page_no"] == 2
