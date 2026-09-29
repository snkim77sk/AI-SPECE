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
    assert "inqryDiv=1" in seen["url"]
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


def test_fetch_page_encodes_normalized_service_key_once(monkeypatch):
    seen = {}
    monkeypatch.setattr(shopping_vnext, "_service_key", lambda: "abc+def/ghi=")
    monkeypatch.setattr(
        shopping_vnext,
        "_request",
        lambda url, kind: seen.update(url=url, kind=kind) or ([], 0),
    )
    shopping_vnext.fetch_page("2026-09-28", "2026-09-28", page=1, rows=10)
    assert "serviceKey=abc%2Bdef%2Fghi%3D" in seen["url"]
    assert "%252B" not in seen["url"]


def test_shopping_source_date_prefers_receipt_date():
    row = {"dlvrReqRcptDate": "20260905"}
    assert shopping_vnext._source_date(row, "2026-09-15") == "2026-09-05"
    assert shopping_vnext._source_date({}, "2026-09-15") == "2026-09-15"


def test_source_key_distinguishes_delivery_change_orders():
    base = {"dlvrReqNo": "REQ-X", "dlvrReqChgOrd": "0", "prdctSno": "1"}
    changed = {"dlvrReqNo": "REQ-X", "dlvrReqChgOrd": "1", "prdctSno": "1"}
    assert shopping_vnext._legacy_source_key(base) == shopping_vnext._legacy_source_key(changed)
    assert shopping_vnext._source_key(base) != shopping_vnext._source_key(changed)
    assert shopping_vnext._change_order({"dlvrReqChgOrd": "001"}) == "1"


def test_collect_page_allows_same_request_item_across_change_orders(monkeypatch):
    rows = [
        {"dlvrReqNo": "REQ-X", "dlvrReqChgOrd": "0", "prdctSno": "1"},
        {"dlvrReqNo": "REQ-X", "dlvrReqChgOrd": "1", "prdctSno": "1"},
    ]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *a, **k: (rows, 2),
    )
    result = shopping_vnext.collect_all(
        "2026-09-01", "2026-09-01",
        page_size=2, max_pages=1, resume=False,
    )
    assert result["complete"] is True
    assert result["fetched"] == 2
    with __import__("db").connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM raw_records WHERE dataset='shopping_delivery'"
        ).fetchone()[0]
    assert count == 2


def test_legacy_shopping_key_migration_splits_change_orders_and_keeps_old_revisions():
    import json
    import db

    change0 = {
        "dlvrReqNo": "REQ-MIG",
        "dlvrReqChgOrd": "0",
        "prdctSno": "1",
        "dtilPrdctClsfcNo": "3911160302",
        "prdctAmt": "100",
    }
    change1 = {
        "dlvrReqNo": "REQ-MIG",
        "dlvrReqChgOrd": "1",
        "prdctSno": "1",
        "dtilPrdctClsfcNo": "3911160302",
        "prdctAmt": "120",
    }
    legacy = shopping_vnext._legacy_source_key(change0)
    vnext_store.preserve_raw(
        "shopping_delivery", legacy, change0,
        source_system="G2B",
        source_operation=shopping_vnext.SHOP_OPERATION,
        source_date="2026-09-01",
    )
    vnext_store.preserve_raw(
        "shopping_delivery", legacy, change1,
        source_system="G2B",
        source_operation=shopping_vnext.SHOP_OPERATION,
        source_date="2026-09-01",
    )

    migration = shopping_vnext.migrate_legacy_source_keys()
    assert migration["status"] == "COMPLETE"
    assert migration["migrated_current"] == 1

    key0 = shopping_vnext._source_key(change0)
    key1 = shopping_vnext._source_key(change1)
    with db.connect() as conn:
        current = conn.execute(
            "SELECT source_key,payload_json FROM raw_records "
            "WHERE dataset='shopping_delivery' ORDER BY source_key"
        ).fetchall()
        legacy_revisions = conn.execute(
            "SELECT COUNT(*) FROM raw_record_revisions "
            "WHERE dataset='shopping_delivery' AND source_key=?",
            (legacy,),
        ).fetchone()[0]
        canonical_revisions = conn.execute(
            "SELECT COUNT(*) FROM raw_record_revisions "
            "WHERE dataset='shopping_delivery' AND source_key IN (?,?)",
            (key0, key1),
        ).fetchone()[0]

    assert {row["source_key"] for row in current} == {key0, key1}
    assert {json.loads(row["payload_json"])["dlvrReqChgOrd"] for row in current} == {"0", "1"}
    assert legacy_revisions == 2
    assert canonical_revisions == 2

    second = shopping_vnext.migrate_legacy_source_keys()
    assert second["status"] == "SKIPPED"
