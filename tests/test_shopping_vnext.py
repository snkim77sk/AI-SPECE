import json

import pytest

import db
import shopping_vnext
import shopping_store_v41
import vnext_collection
import vnext_http
import vnext_store


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


def test_prepare_collection_storage_installs_required_schemas_once(monkeypatch):
    import vnext_collection

    calls = []
    monkeypatch.setattr(
        shopping_vnext.shopping_store_v41,
        "ensure_schema",
        lambda: calls.append("shopping"),
    )
    monkeypatch.setattr(
        vnext_collection,
        "ensure_collection_storage",
        lambda: calls.append("collection"),
    )

    shopping_vnext.prepare_collection_storage()

    assert calls == ["shopping", "collection"]


def test_collect_all_reuses_prepared_storage_without_schema_setup(monkeypatch):
    import vnext_collection

    seen = {}

    def forbidden_prepare():
        raise AssertionError("prepared collect_all must not rerun schema setup")

    def fake_collect_pages(**kwargs):
        seen.update(kwargs)
        return {
            "dataset": "shopping_delivery",
            "scope": "2026-09-01:2026-09-01",
            "fetched": 0,
            "saved": 0,
            "source_total": 0,
            "complete": True,
            "resumed": False,
            "status": "COMPLETE",
            "reason": "",
            "completion_reason": "TOTAL_REACHED",
        }

    monkeypatch.setattr(shopping_vnext, "prepare_collection_storage", forbidden_prepare)
    monkeypatch.setattr(vnext_collection, "collect_pages", fake_collect_pages)

    result = shopping_vnext.collect_all(
        "2026-09-01",
        "2026-09-01",
        storage_prepared=True,
    )

    assert result["complete"] is True
    assert seen["storage_prepared"] is True
    assert seen["scope"] == "2026-09-01:2026-09-01"


def test_complete_shopping_can_compact_receipts_and_fast_skip(monkeypatch):
    rows = [{
        "dlvrReqNo": "COMPACT-1",
        "dlvrReqChgOrd": "0",
        "prdctSno": "1",
        "dlvrReqRcptDate": "20260901",
        "dtilPrdctClsfcNo": "3911160302",
        "prdctNm": "LED 가로등기구",
    }]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (rows, 1),
    )
    monkeypatch.setattr(
        shopping_vnext,
        "compact_complete_enabled",
        lambda: True,
    )

    first = shopping_vnext.collect_all(
        "2026-09-01",
        "2026-09-01",
        page_size=1,
        max_pages=1,
        resume=True,
    )
    assert first["complete"] is True

    cp = vnext_store.get_checkpoint(
        "shopping_delivery",
        "2026-09-01:2026-09-01",
    )
    assert vnext_collection.verified_compact_completion(cp) is True
    with db.connect() as conn:
        pages = conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_pages
               WHERE dataset='shopping_delivery'
                 AND scope_key='2026-09-01:2026-09-01'"""
        ).fetchone()[0]
        items = conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_items
               WHERE dataset='shopping_delivery'
                 AND scope_key='2026-09-01:2026-09-01'"""
        ).fetchone()[0]
    assert pages == 0
    assert items == 0

    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("compact COMPLETE scope must not refetch")
        ),
    )
    repeated = shopping_vnext.collect_all(
        "2026-09-01",
        "2026-09-01",
        page_size=1,
        max_pages=1,
        resume=True,
    )
    assert repeated["complete"] is True
    assert repeated["resumed"] is True


def test_existing_complete_receipts_can_be_promoted_to_compact_marker(monkeypatch):
    rows = [{
        "dlvrReqNo": "LEGACY-COMPACT-1",
        "dlvrReqChgOrd": "0",
        "prdctSno": "1",
        "dlvrReqRcptDate": "20260902",
        "dtilPrdctClsfcNo": "3911160302",
        "prdctNm": "LED 보안등기구",
    }]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (rows, 1),
    )
    monkeypatch.setattr(
        shopping_vnext,
        "compact_complete_enabled",
        lambda: False,
    )
    first = shopping_vnext.collect_all(
        "2026-09-02",
        "2026-09-02",
        page_size=1,
        max_pages=1,
        resume=True,
    )
    assert first["complete"] is True

    cp = vnext_store.get_checkpoint(
        "shopping_delivery",
        "2026-09-02:2026-09-02",
    )
    assert vnext_collection.verified_compact_completion(cp) is False
    assert vnext_collection.verified_terminal_receipt(
        cp, schema_prepared=True
    ) is True

    assert vnext_collection.compact_verified_terminal_receipt(
        cp,
        schema_prepared=True,
        receipt_verified=True,
    ) is True
    promoted = vnext_store.get_checkpoint(
        "shopping_delivery",
        "2026-09-02:2026-09-02",
        schema_prepared=True,
    )
    assert vnext_collection.verified_compact_completion(promoted) is True
    with db.connect() as conn:
        assert conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_pages
               WHERE dataset='shopping_delivery'
                 AND scope_key='2026-09-02:2026-09-02'"""
        ).fetchone()[0] == 0
        assert conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_items
               WHERE dataset='shopping_delivery'
                 AND scope_key='2026-09-02:2026-09-02'"""
        ).fetchone()[0] == 0


def _shopping_row(req, *, change="0", code="3911160302", date="20260905"):
    return {
        "dlvrReqNo": req,
        "dlvrReqChgOrd": change,
        "prdctSno": "1",
        "dlvrReqRcptDate": date,
        "dtilPrdctClsfcNo": code,
        "prdctNm": "LED 보안등기구" if code == "3911160302" else "일반 사무용품",
    }


def test_shopping_complete_reconcile_preserves_missing_change_as_inactive_history(
    monkeypatch,
):
    day = "2026-09-05"
    first_rows = [
        _shopping_row("HIST-REQ", change="0"),
        _shopping_row("HIST-REQ", change="1"),
    ]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (first_rows, 2),
    )
    first = shopping_vnext.collect_all(
        day, day, page_size=2, max_pages=1, resume=False
    )
    assert first["complete"] is True
    assert first["reconcile"]["deactivated"] == 0

    second_rows = [_shopping_row("HIST-REQ", change="1")]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (second_rows, 1),
    )
    second = shopping_vnext.collect_all(
        day, day, page_size=2, max_pages=1, resume=False
    )
    assert second["complete"] is True
    assert second["reconcile"]["deactivated"] == 1

    with db.connect() as conn:
        rows = conn.execute(
            """SELECT delivery_change_order,is_active,inactive_reason
               FROM shopping_records
               WHERE delivery_req_no='HIST-REQ'
               ORDER BY delivery_change_order"""
        ).fetchall()
    assert len(rows) == 2
    assert dict(rows[0]) == {
        "delivery_change_order": "0",
        "is_active": 0,
        "inactive_reason": "MISSING_FROM_COMPLETE_SOURCE",
    }
    assert dict(rows[1]) == {
        "delivery_change_order": "1",
        "is_active": 1,
        "inactive_reason": "",
    }


def test_shopping_complete_reconcile_marks_non_target_transition_inactive(monkeypatch):
    day = "2026-09-06"
    target = _shopping_row("SCOPE-REQ", code="3911160302", date="20260906")
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: ([target], 1),
    )
    assert shopping_vnext.collect_all(
        day, day, page_size=1, max_pages=1, resume=False
    )["complete"] is True

    outside = _shopping_row("SCOPE-REQ", code="9999999999", date="20260906")
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: ([outside], 1),
    )
    result = shopping_vnext.collect_all(
        day, day, page_size=1, max_pages=1, resume=False
    )
    assert result["complete"] is True
    assert result["saved"] == 0
    assert result["reconcile"]["deactivated"] == 1
    assert result["reconcile"]["target_observed"] == 0

    with db.connect() as conn:
        row = conn.execute(
            """SELECT is_active,inactive_reason
               FROM shopping_records
               WHERE delivery_req_no='SCOPE-REQ'"""
        ).fetchone()
    assert row["is_active"] == 0
    assert row["inactive_reason"] == "OUTSIDE_TARGET_SCOPE"


def test_shopping_empty_complete_recheck_is_fail_safe(monkeypatch):
    day = "2026-09-07"
    target = _shopping_row("EMPTY-SAFE", date="20260907")
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: ([target], 1),
    )
    shopping_vnext.collect_all(
        day, day, page_size=1, max_pages=1, resume=False
    )

    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: ([], 0),
    )
    result = shopping_vnext.collect_all(
        day, day, page_size=1, max_pages=1, resume=False
    )
    assert result["complete"] is True
    assert result["reconcile"]["status"] == "SKIPPED_EMPTY_FAILSAFE"
    with db.connect() as conn:
        row = conn.execute(
            "SELECT is_active,inactive_reason FROM shopping_records "
            "WHERE delivery_req_no='EMPTY-SAFE'"
        ).fetchone()
    assert row["is_active"] == 1
    assert row["inactive_reason"] == ""


def test_shopping_reappearing_target_reactivates_inactive_row(monkeypatch):
    day = "2026-09-08"
    target = _shopping_row("RETURN-REQ", date="20260908")
    outside = _shopping_row(
        "RETURN-REQ", code="9999999999", date="20260908"
    )

    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: ([target], 1),
    )
    shopping_vnext.collect_all(
        day, day, page_size=1, max_pages=1, resume=False
    )
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: ([outside], 1),
    )
    shopping_vnext.collect_all(
        day, day, page_size=1, max_pages=1, resume=False
    )
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: ([target], 1),
    )
    result = shopping_vnext.collect_all(
        day, day, page_size=1, max_pages=1, resume=False
    )

    assert result["complete"] is True
    with db.connect() as conn:
        row = conn.execute(
            """SELECT is_active,inactive_reason,inactive_at
               FROM shopping_records
               WHERE delivery_req_no='RETURN-REQ'"""
        ).fetchone()
    assert row["is_active"] == 1
    assert row["inactive_reason"] == ""
    assert row["inactive_at"] == ""


def test_multi_day_shopping_reconcile_is_fail_safe(monkeypatch):
    rows = [_shopping_row("MULTI-REQ", date="20260909")]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (rows, 1),
    )
    result = shopping_vnext.collect_all(
        "2026-09-09",
        "2026-09-10",
        page_size=1,
        max_pages=1,
        resume=False,
    )
    assert result["complete"] is True
    assert result["reconcile"]["status"] == "SKIPPED_MULTI_DAY_FAILSAFE"


def test_shopping_schema_migrates_existing_records_to_active(monkeypatch):
    import shopping_store_v41

    with db.connect() as conn:
        conn.execute(
            """CREATE TABLE shopping_records(
                 source_key TEXT PRIMARY KEY,
                 source_date TEXT NOT NULL DEFAULT '',
                 primary_category TEXT NOT NULL DEFAULT '',
                 demand_region TEXT NOT NULL DEFAULT '',
                 demand_org TEXT NOT NULL DEFAULT '',
                 vendor_name TEXT NOT NULL DEFAULT ''
               )"""
        )
        conn.execute(
            """INSERT INTO shopping_records(
                 source_key,source_date,primary_category,
                 demand_region,demand_org,vendor_name
               ) VALUES('OLD','2026-09-01','LIGHTING','','','')"""
        )

    shopping_store_v41.ensure_schema()

    with db.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(shopping_records)"
            ).fetchall()
        }
        row = conn.execute(
            """SELECT is_active,inactive_reason,inactive_at
               FROM shopping_records WHERE source_key='OLD'"""
        ).fetchone()
    assert {"is_active", "inactive_reason", "inactive_at"} <= columns
    assert row["is_active"] == 1
    assert row["inactive_reason"] == ""
    assert row["inactive_at"] == ""


def test_shopping_store_count_separates_active_inactive_and_history(monkeypatch):
    day = "2026-09-09"
    rows = [
        {
            "dlvrReqNo": "COUNT-A",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20260909",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 보안등기구",
        },
        {
            "dlvrReqNo": "COUNT-B",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20260909",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 보안등기구",
        },
    ]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (rows, 2),
    )
    shopping_vnext.collect_all(
        day, day, page_size=2, max_pages=1, resume=False
    )
    with db.connect() as conn:
        conn.execute(
            """UPDATE shopping_records
               SET is_active=0,
                   inactive_reason='MISSING_FROM_COMPLETE_SOURCE'
               WHERE delivery_req_no='COUNT-A'"""
        )

    counts = shopping_store_v41.count()

    assert counts["records"] == 2
    assert counts["history_records"] == 2
    assert counts["active_records"] == 1
    assert counts["inactive_records"] == 1
    assert counts["active_last_at"]


def test_shopping_retention_purges_only_dates_older_than_one_year():
    old_row = _shopping_row("RET-OLD", date="20261002")
    keep_row = _shopping_row("RET-KEEP", date="20261003")
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "RET-OLD",
        old_row,
        source_system="G2B",
        source_operation="test",
        source_date="2026-10-02",
    )
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "RET-KEEP",
        keep_row,
        source_system="G2B",
        source_operation="test",
        source_date="2026-10-03",
    )
    vnext_collection.ensure_collection_storage()
    vnext_store.save_checkpoint(
        "shopping_delivery",
        "2026-10-02:2026-10-02",
        range_start="2026-10-02",
        range_end="2026-10-02",
        cursor_value="{}",
        page_no=2,
        page_size=1,
        source_total=1,
        fetched_count=1,
        saved_count=1,
        status="COMPLETE",
    )
    vnext_store.save_checkpoint(
        "shopping_delivery",
        "2026-10-03:2026-10-03",
        range_start="2026-10-03",
        range_end="2026-10-03",
        cursor_value="{}",
        page_no=2,
        page_size=1,
        source_total=1,
        fetched_count=1,
        saved_count=1,
        status="COMPLETE",
    )
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO vnext_collection_pages(
                 dataset,scope_key,generation,page_no,page_size,response_hash,
                 item_count,source_total,terminal_reason
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                "shopping_delivery",
                "2026-10-02:2026-10-02",
                "OLDGEN",
                1,
                1,
                "hash",
                1,
                1,
                "TOTAL_REACHED",
            ),
        )
        conn.execute(
            """INSERT INTO vnext_collection_items(
                 dataset,scope_key,generation,source_key,page_no,payload_sha256,stored
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                "shopping_delivery",
                "2026-10-02:2026-10-02",
                "OLDGEN",
                "RET-OLD",
                1,
                "sha",
                1,
            ),
        )

    result = shopping_store_v41.purge_history(
        365,
        now=__import__("datetime").datetime(
            2027,
            10,
            3,
            12,
            0,
            tzinfo=__import__("datetime").timezone(
                __import__("datetime").timedelta(hours=9)
            ),
        ),
    )

    assert result["cutoff_date"] == "2026-10-03"
    assert result["deleted_records"] == 1
    assert result["deleted_checkpoints"] == 1
    assert result["deleted_collection_pages"] == 1
    assert result["deleted_collection_items"] == 1
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM shopping_records WHERE source_key='RET-OLD'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM shopping_records WHERE source_key='RET-KEEP'"
        ).fetchone()[0] == 1
        assert conn.execute(
            """SELECT COUNT(*) FROM collection_checkpoints
               WHERE dataset='shopping_delivery'
                 AND scope_key='2026-10-02:2026-10-02'"""
        ).fetchone()[0] == 0
        assert conn.execute(
            """SELECT COUNT(*) FROM collection_checkpoints
               WHERE dataset='shopping_delivery'
                 AND scope_key='2026-10-03:2026-10-03'"""
        ).fetchone()[0] == 1


def test_missing_total_full_shopping_page_stays_running(monkeypatch):
    monkeypatch.setattr(shopping_vnext,'fetch_page',lambda *a,**k:([{'dlvrReqNo':'A','prdctSno':'1'},{'dlvrReqNo':'B','prdctSno':'1'}],None))
    result=shopping_vnext.collect_all('2026-10-16','2026-10-16',page_size=2,max_pages=1,resume=False)
    assert result['fetched']==2 and result['source_total'] is None and not result['complete']
    cp=vnext_store.get_checkpoint('shopping_delivery','2026-10-16:2026-10-16')
    assert cp['status']=='RUNNING' and cp['page_no']==2


def test_oversized_shopping_page_size_uses_api_max_for_completion(monkeypatch):
    seen=[]
    def fetch(start,end,page=1,rows=999):
        seen.append(rows);return ([{'dlvrReqNo':'REQ','prdctSno':str(i)} for i in range(rows)],None)
    monkeypatch.setattr(shopping_vnext,'fetch_page',fetch)
    result=shopping_vnext.collect_all('2026-10-16','2026-10-16',page_size=5000,max_pages=1,resume=False)
    assert seen==[999] and result['fetched']==999 and not result['complete']
    assert vnext_store.get_checkpoint('shopping_delivery','2026-10-16:2026-10-16')['page_no']==2


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
        {"dlvrReqNo": "REQ-X", "dlvrReqChgOrd": "0", "prdctSno": "1", "dtilPrdctClsfcNo": "3911160302"},
        {"dlvrReqNo": "REQ-X", "dlvrReqChgOrd": "1", "prdctSno": "1", "dtilPrdctClsfcNo": "3911160302"},
    ]
    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *a, **k: (rows, 2),
    )
    result = shopping_vnext.collect_all(
        "2026-10-01", "2026-10-01",
        page_size=2, max_pages=1, resume=False,
    )
    assert result["complete"] is True
    assert result["fetched"] == 2
    with __import__("db").connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM raw_records WHERE dataset='shopping_delivery'"
        ).fetchone()[0]
    assert count == 2


def _target_row(req_no):
    return {
        "dlvrReqNo": str(req_no),
        "dlvrReqChgOrd": "0",
        "prdctSno": "1",
        "dlvrReqRcptDate": "20260901",
        "dtilPrdctClsfcNo": "3911160302",
        "prdctNm": "LED 가로등기구",
    }


def test_shopping_total_growth_continues_same_generation(monkeypatch):
    scope = "2026-09-03:2026-09-03"
    calls = []
    pages = {
        1: ([_target_row("GROW-A")], 2),
        2: ([_target_row("GROW-B")], 3),
        3: ([_target_row("GROW-C")], 3),
    }

    def fetch(start, end, page=1, rows=999):
        calls.append(page)
        assert start == end == "2026-09-03"
        assert rows == 1
        return pages[page]

    monkeypatch.setattr(shopping_vnext, "fetch_page", fetch)

    result = shopping_vnext.collect_all(
        "2026-09-03",
        "2026-09-03",
        page_size=1,
        max_pages=3,
        resume=True,
    )

    assert result["complete"] is True
    assert result["fetched"] == 3
    assert result["source_total"] == 3
    assert calls == [1, 2, 3]

    cp = vnext_store.get_checkpoint("shopping_delivery", scope)
    assert cp["status"] == "COMPLETE"
    assert cp["source_total"] == 3
    assert cp["page_no"] == 4
    assert vnext_collection.verified_terminal_receipt(
        cp, schema_prepared=True
    ) is True


def test_shopping_total_decrease_replays_scope_from_page_one(monkeypatch):
    scope = "2026-09-04:2026-09-04"
    first_calls = []

    def unstable(start, end, page=1, rows=999):
        first_calls.append(page)
        assert start == end == "2026-09-04"
        if page == 1:
            return [_target_row("DROP-A")], 3
        return [_target_row("DROP-ROGUE")], 2

    monkeypatch.setattr(shopping_vnext, "fetch_page", unstable)
    first = shopping_vnext.collect_all(
        "2026-09-04",
        "2026-09-04",
        page_size=1,
        max_pages=3,
        resume=True,
    )

    assert first["complete"] is False
    assert first["status"] == "INCOMPLETE"
    assert first["reason"] == "SOURCE_TOTAL_DECREASED"
    assert first_calls == [1, 2]

    failed = vnext_store.get_checkpoint("shopping_delivery", scope)
    failed_meta = json.loads(failed["cursor_value"])
    assert failed["status"] == "INCOMPLETE"
    assert failed["page_no"] == 2
    assert failed["source_total"] == 3
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM shopping_records"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM shopping_records WHERE delivery_req_no='DROP-ROGUE'"
        ).fetchone()[0] == 0

    second_calls = []

    def stable(start, end, page=1, rows=999):
        second_calls.append(page)
        assert start == end == "2026-09-04"
        return (
            [_target_row("DROP-A")] if page == 1 else [_target_row("DROP-B")],
            2,
        )

    monkeypatch.setattr(shopping_vnext, "fetch_page", stable)
    second = shopping_vnext.collect_all(
        "2026-09-04",
        "2026-09-04",
        page_size=1,
        max_pages=3,
        resume=True,
    )

    assert second["complete"] is True
    assert second["fetched"] == 2
    assert second["source_total"] == 2
    assert second_calls == [1, 2]

    complete = vnext_store.get_checkpoint("shopping_delivery", scope)
    complete_meta = json.loads(complete["cursor_value"])
    assert complete["status"] == "COMPLETE"
    assert complete["page_no"] == 3
    assert complete["source_total"] == 2
    assert complete_meta["generation"] != failed_meta["generation"]
    with db.connect() as conn:
        assert conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_pages
               WHERE dataset='shopping_delivery' AND scope_key=?""",
            (scope,),
        ).fetchone()[0] == 2
        assert conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_items
               WHERE dataset='shopping_delivery' AND scope_key=?""",
            (scope,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM shopping_records WHERE delivery_req_no='DROP-ROGUE'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM shopping_records WHERE delivery_req_no='DROP-B'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "failure",
    [
        vnext_http.VNextQuotaReached("22", "synthetic quota exhausted"),
        RuntimeError("synthetic network interruption"),
    ],
    ids=["quota", "network"],
)
def test_shopping_multi_page_failure_resumes_exact_next_page(monkeypatch, failure):
    scope = "2026-09-01:2026-09-01"
    calls = []

    def interrupted(start, end, page=1, rows=999):
        calls.append(page)
        assert start == end == "2026-09-01"
        assert rows == 1
        if page == 2:
            raise failure
        return [_target_row("REQ-A")], 2

    monkeypatch.setattr(shopping_vnext, "fetch_page", interrupted)

    with pytest.raises(type(failure)):
        shopping_vnext.collect_all(
            "2026-09-01",
            "2026-09-01",
            page_size=1,
            max_pages=2,
            resume=True,
        )

    failed = vnext_store.get_checkpoint("shopping_delivery", scope)
    failed_meta = json.loads(failed["cursor_value"])
    assert failed["status"] == "FAILED"
    assert failed["page_no"] == 2
    assert failed["fetched_count"] == 1
    assert failed["saved_count"] == 1

    def recovered(start, end, page=1, rows=999):
        calls.append(page)
        assert start == end == "2026-09-01"
        assert page == 2
        assert rows == 1
        return [_target_row("REQ-B")], 2

    monkeypatch.setattr(shopping_vnext, "fetch_page", recovered)
    completed = shopping_vnext.collect_all(
        "2026-09-01",
        "2026-09-01",
        page_size=1,
        max_pages=2,
        resume=True,
    )

    assert completed["complete"] is True
    assert completed["resumed"] is True
    assert completed["fetched"] == 2
    assert completed["saved"] == 2
    assert calls == [1, 2, 2]

    final = vnext_store.get_checkpoint("shopping_delivery", scope)
    final_meta = json.loads(final["cursor_value"])
    assert final["status"] == "COMPLETE"
    assert final["page_no"] == 3
    assert final_meta["generation"] == failed_meta["generation"]

    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM shopping_records"
        ).fetchone()[0] == 2
        assert conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_pages
               WHERE dataset='shopping_delivery' AND scope_key=?""",
            (scope,),
        ).fetchone()[0] == 2
        assert conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_items
               WHERE dataset='shopping_delivery' AND scope_key=?""",
            (scope,),
        ).fetchone()[0] == 2

    monkeypatch.setattr(
        shopping_vnext,
        "fetch_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("completed shopping scope must not refetch")
        ),
    )
    repeated = shopping_vnext.collect_all(
        "2026-09-01",
        "2026-09-01",
        page_size=1,
        max_pages=2,
        resume=True,
    )
    assert repeated["complete"] is True
    assert repeated["resumed"] is True
    assert calls == [1, 2, 2]


def test_legacy_shopping_key_migration_is_not_required_after_v41_fresh_start():
    migration = shopping_vnext.migrate_legacy_source_keys()
    assert migration == {
        "status": "NOT_REQUIRED_V41_FRESH_START",
        "migrated_current": 0,
        "copied_revisions": 0,
    }
