import vnext_store
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
    monkeypatch.setattr(shopping_vnext,'fetch_page',lambda *a,**k:([{'dlvrReqNo':'A','prdctSno':'1'},{'dlvrReqNo':'B','prdctSno':'1'}],None))
    result=shopping_vnext.collect_all('2026-09-16','2026-09-16',page_size=2,max_pages=1,resume=False)
    assert result['fetched']==2 and result['source_total'] is None and not result['complete']
    cp=vnext_store.get_checkpoint('shopping_delivery','2026-09-16:2026-09-16')
    assert cp['status']=='RUNNING' and cp['page_no']==2


def test_oversized_shopping_page_size_uses_api_max_for_completion(monkeypatch):
    seen=[]
    def fetch(start,end,page=1,rows=999):
        seen.append(rows);return ([{'dlvrReqNo':'REQ','prdctSno':str(i)} for i in range(rows)],None)
    monkeypatch.setattr(shopping_vnext,'fetch_page',fetch)
    result=shopping_vnext.collect_all('2026-09-16','2026-09-16',page_size=5000,max_pages=1,resume=False)
    assert seen==[999] and result['fetched']==999 and not result['complete']
    assert vnext_store.get_checkpoint('shopping_delivery','2026-09-16:2026-09-16')['page_no']==2
