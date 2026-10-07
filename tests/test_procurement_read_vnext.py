from contextlib import contextmanager

import pytest
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


def test_current_merged_region_shopping_filter_includes_predecessor_regions():
    where, params = procurement_read_vnext._shopping_filter_parts(
        categories=("LIGHTING",),
        region="전남광주통합특별시",
        start_date="2026-01-01",
        end_date="2026-12-31",
    )

    sql = " ".join(where)
    assert sql.count("demand_region=?") == 3
    assert "전남광주통합특별시" in params
    assert "광주광역시" in params
    assert "전라남도" in params
    assert "전남광주통합특별시 %" in params
    assert "광주광역시 %" in params
    assert "전라남도 %" in params


def test_production_shopping_read_path_does_not_run_schema_ddl():
    source = __import__("pathlib").Path("procurement_read_vnext.py").read_text(encoding="utf-8")
    block = source.split("def shopping_rows", 1)[1].split("def vendor_rows", 1)[0]
    assert "if test_mode:" in block
    assert "shopping_store_v41.ensure_schema()" in block
    assert block.index("if test_mode:") < block.index("shopping_store_v41.ensure_schema()")


def test_request_level_shopping_indexes_cover_both_access_shapes():
    shopping_store_v41.ensure_schema()

    with db.connect() as conn:
        selection = conn.execute(
            "PRAGMA index_info("
            "ix_shopping_records_active_category_date_request)"
        ).fetchall()
        detail = conn.execute(
            "PRAGMA index_info("
            "ix_shopping_records_request_active_category_date)"
        ).fetchall()
        latest = conn.execute(
            "PRAGMA index_info("
            "ix_shopping_records_request_latest)"
        ).fetchall()

    assert [row["name"] for row in selection] == [
        "is_active",
        "primary_category",
        "source_date",
        "delivery_req_no",
    ]
    assert [row["name"] for row in detail] == [
        "delivery_req_no",
        "is_active",
        "primary_category",
        "source_date",
    ]
    assert [row["name"] for row in latest] == [
        "delivery_req_no",
        "source_date",
        "updated_at",
        "source_key",
    ]


def test_request_page_offset_has_operational_upper_bound():
    with pytest.raises(ValueError, match="SHOPPING_REQUEST_OFFSET_TOO_LARGE"):
        procurement_read_vnext._request_page_detail_rows(
            categories=("LIGHTING",),
            offset=procurement_read_vnext.MAX_REQUEST_PAGE_OFFSET + 1,
        )


def test_request_level_read_path_does_not_run_schema_ddl():
    source = __import__("pathlib").Path(
        "procurement_read_vnext.py"
    ).read_text(encoding="utf-8")
    block = source.split("def _request_page_detail_rows", 1)[1].split(
        "def _new_vendor", 1
    )[0]
    assert "ensure_schema()" not in block
    assert "CREATE INDEX" not in block
    assert "ALTER TABLE" not in block


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


def test_postgres_streaming_adapter_bounds_driver_buffer():
    source = __import__("pathlib").Path("db.py").read_text(encoding="utf-8")
    block = source.split("def execute_streaming", 1)[1].split(
        "def executemany", 1
    )[0]

    assert "stream_results=True" in block
    assert "max_row_buffer=buffer_size" in block
    assert "exec_driver_sql" in block


def test_production_vendor_stream_uses_bounded_fetchmany_and_latest_change(monkeypatch):
    batches = [
        [
            {
                "source_key": "A-C0",
                "source_date": "2026-09-01",
                "fetched_at": "2026-09-01T01:00:00Z",
                "primary_category": "LIGHTING",
                "delivery_req_no": "REQ-A",
                "detail_seq": "1",
                "delivery_change_order": "0",
                "is_final_delivery_request": "N",
                "demand_org": "A기관",
                "vendor_name": "스트림조명",
                "vendor_bizno": "1234567890",
                "amount": 1000,
                "delivery_req_total_amount": 1000,
            },
            {
                "source_key": "A-C1",
                "source_date": "2026-09-02",
                "fetched_at": "2026-09-02T01:00:00Z",
                "primary_category": "LIGHTING",
                "delivery_req_no": "REQ-A",
                "detail_seq": "1",
                "delivery_change_order": "1",
                "is_final_delivery_request": "Y",
                "demand_org": "A기관",
                "vendor_name": "스트림조명",
                "vendor_bizno": "1234567890",
                "amount": 1200,
                "delivery_req_total_amount": 1200,
            },
            {
                "source_key": "B",
                "source_date": "2026-09-03",
                "fetched_at": "2026-09-03T01:00:00Z",
                "primary_category": "POLE",
                "delivery_req_no": "REQ-B",
                "detail_seq": "1",
                "delivery_change_order": "0",
                "is_final_delivery_request": "Y",
                "demand_org": "B기관",
                "vendor_name": "스트림조명",
                "vendor_bizno": "1234567890",
                "amount": 800,
                "delivery_req_total_amount": 800,
            },
        ],
        [],
    ]
    fetch_sizes = []
    seen_sql = {}

    class Cursor:
        def fetchmany(self, size):
            fetch_sizes.append(size)
            return batches.pop(0)

    class Conn:
        def execute_streaming(self, sql, params=(), *, max_row_buffer=250):
            seen_sql["sql"] = sql
            seen_sql["params"] = tuple(params)
            seen_sql["max_row_buffer"] = max_row_buffer
            return Cursor()

        def execute(self, *_args, **_kwargs):
            raise AssertionError("vendor stream must use execute_streaming")

    @contextmanager
    def fake_connect():
        yield Conn()

    monkeypatch.setattr(procurement_read_vnext, "connect", fake_connect)

    rows = list(
        procurement_read_vnext._iter_latest_normalized_vendor_rows(
            region="",
        )
    )

    assert [row["source_key"] for row in rows] == ["A-C1", "B"]
    assert fetch_sizes == [250, 250]
    assert "ORDER BY delivery_req_no,detail_seq,source_key" in seen_sql["sql"]
    assert seen_sql["max_row_buffer"] == 250
    source = __import__("inspect").getsource(
        procurement_read_vnext._iter_latest_normalized_vendor_rows
    )
    assert ".fetchall()" not in source
    assert "fetchmany(250)" in source


def test_production_vendor_rows_never_calls_unbounded_shopping_rows(monkeypatch):
    monkeypatch.setattr(
        procurement_read_vnext,
        "_uses_normalized_shopping_store",
        lambda: True,
    )
    monkeypatch.setattr(
        procurement_read_vnext,
        "backend_name",
        lambda: "POSTGRESQL",
    )
    monkeypatch.setattr(
        procurement_read_vnext,
        "_iter_latest_normalized_vendor_rows",
        lambda **kwargs: iter([
            {
                "source_key": "STREAM-1",
                "source_date": "2026-09-01",
                "fetched_at": "2026-09-01T01:00:00Z",
                "primary_category": "LIGHTING",
                "delivery_req_no": "REQ-STREAM",
                "detail_seq": "1",
                "delivery_change_order": "0",
                "is_final_delivery_request": "Y",
                "demand_org": "스트림기관",
                "vendor_name": "스트림조명",
                "vendor_bizno": "123-45-67890",
                "amount": 1500,
                "delivery_req_total_amount": 1500,
            }
        ]),
    )
    monkeypatch.setattr(
        procurement_read_vnext,
        "shopping_rows",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError(
                "production vendor aggregation must not materialize shopping_rows"
            )
        ),
    )

    rows = procurement_read_vnext.vendor_rows(
        query="스트림",
        region="인천광역시",
        limit=1000,
    )

    assert len(rows) == 1
    assert rows[0]["vendor_name"] == "스트림조명"
    assert rows[0]["vendor_bizno"] == "1234567890"
    assert rows[0]["shopping_rows"] == 1
    assert rows[0]["shopping_amount"] == 1500


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


def test_shopping_request_rows_sum_latest_target_detail_amounts_only():
    rows = [
        {
            "source_key": "REQ-GROUP-1-C0",
            "source_date": "2026-09-05",
            "fetched_at": "2026-09-05T01:00:00+00:00",
            "primary_category": "LIGHTING",
            "delivery_req_no": "REQ-GROUP",
            "detail_seq": "1",
            "delivery_req_name": "보안등 및 등주 구매",
            "delivery_change_order": "0",
            "is_final_delivery_request": "N",
            "detail_item_name": "LED보안등기구",
            "item_name": "LED 보안등기구 50W",
            "model_name": "L-50",
            "demand_org": "인천광역시 옹진군",
            "demand_region": "인천광역시",
            "vendor_name": "테스트조명",
            "amount": 1000,
            "quantity": 1,
            "delivery_req_total_amount": 9999,
        },
        {
            "source_key": "REQ-GROUP-1-C1",
            "source_date": "2026-09-05",
            "fetched_at": "2026-09-06T01:00:00+00:00",
            "primary_category": "LIGHTING",
            "delivery_req_no": "REQ-GROUP",
            "detail_seq": "1",
            "delivery_req_name": "보안등 및 등주 구매",
            "delivery_change_order": "1",
            "is_final_delivery_request": "Y",
            "detail_item_name": "LED보안등기구",
            "item_name": "LED 보안등기구 50W",
            "model_name": "L-50",
            "demand_org": "인천광역시 옹진군",
            "demand_region": "인천광역시",
            "vendor_name": "테스트조명",
            "amount": 1200,
            "quantity": 1,
            "delivery_req_total_amount": 9999,
        },
        {
            "source_key": "REQ-GROUP-2",
            "source_date": "2026-09-05",
            "fetched_at": "2026-09-05T01:00:00+00:00",
            "primary_category": "POLE",
            "delivery_req_no": "REQ-GROUP",
            "detail_seq": "2",
            "delivery_req_name": "보안등 및 등주 구매",
            "delivery_change_order": "0",
            "is_final_delivery_request": "Y",
            "detail_item_name": "보안등주",
            "item_name": "스테인리스 보안등주",
            "model_name": "P-01",
            "demand_org": "인천광역시 옹진군",
            "demand_region": "인천광역시",
            "vendor_name": "테스트조명",
            "amount": 800,
            "quantity": 2,
            "delivery_req_total_amount": 9999,
        },
    ]

    requests = procurement_read_vnext.shopping_request_rows_from_rows(rows)

    assert len(requests) == 1
    row = requests[0]
    assert row["source_key"] == "REQUEST:REQ-GROUP"
    assert row["delivery_req_no"] == "REQ-GROUP"
    assert row["request_detail_rows"] == 2
    assert row["amount"] == 2000
    assert row["delivery_req_total_amount"] == 9999
    assert row["amount_basis"] == "SUM_LATEST_TARGET_DETAIL_ITEM_AMOUNT"
    assert row["primary_category"] == "MIXED_TARGET"
    assert row["primary_categories"] == ["LIGHTING", "POLE"]
    assert "LED보안등기구" in row["detail_item_name"]
    assert "보안등주" in row["detail_item_name"]


def test_shopping_request_integrity_flags_conflicting_org_vendor_and_contract():
    rows = [
        {
            "source_key": "CONFLICT-1",
            "source_date": "2026-09-05",
            "fetched_at": "2026-09-05T01:00:00+00:00",
            "primary_category": "LIGHTING",
            "delivery_req_no": "REQ-CONFLICT",
            "detail_seq": "1",
            "delivery_req_name": "보안등 구매",
            "delivery_change_order": "0",
            "is_final_delivery_request": "Y",
            "detail_item_name": "LED보안등기구",
            "item_name": "LED 보안등기구",
            "demand_org": "인천광역시 옹진군",
            "demand_region": "인천광역시",
            "vendor_name": "업체A",
            "vendor_bizno": "111-11-11111",
            "contract_no": "C-001",
            "amount": 1000,
        },
        {
            "source_key": "CONFLICT-2",
            "source_date": "2026-09-05",
            "fetched_at": "2026-09-05T01:00:01+00:00",
            "primary_category": "POLE",
            "delivery_req_no": "REQ-CONFLICT",
            "detail_seq": "2",
            "delivery_req_name": "보안등 구매",
            "delivery_change_order": "0",
            "is_final_delivery_request": "Y",
            "detail_item_name": "보안등주",
            "item_name": "보안등주",
            "demand_org": "인천광역시 강화군",
            "demand_region": "인천광역시",
            "vendor_name": "업체B",
            "vendor_bizno": "222-22-22222",
            "contract_no": "C-002",
            "amount": 800,
        },
    ]

    requests = procurement_read_vnext.shopping_request_rows_from_rows(rows)

    assert len(requests) == 1
    row = requests[0]
    assert row["request_integrity_valid"] is False
    assert row["request_integrity_status"] == "EXCLUDED_CONFLICT"
    assert row["request_integrity_issues"] == [
        "CONTRACT_NO_CONFLICT",
        "DEMAND_ORG_CONFLICT",
        "VENDOR_BIZNO_CONFLICT",
    ]


def test_shopping_request_integrity_allows_missing_values_and_same_vendor_bizno():
    rows = [
        {
            "source_key": "SPARSE-1",
            "source_date": "2026-09-05",
            "primary_category": "LIGHTING",
            "delivery_req_no": "REQ-SPARSE",
            "detail_seq": "1",
            "delivery_change_order": "0",
            "demand_org": "인천광역시 옹진군",
            "vendor_name": "테스트조명 주식회사",
            "vendor_bizno": "123-45-67890",
            "contract_no": "C-001",
            "amount": 1000,
        },
        {
            "source_key": "SPARSE-2",
            "source_date": "2026-09-05",
            "primary_category": "LIGHTING",
            "delivery_req_no": "REQ-SPARSE",
            "detail_seq": "2",
            "delivery_change_order": "0",
            "demand_org": "",
            "vendor_name": "(주)테스트조명",
            "vendor_bizno": "1234567890",
            "contract_no": "",
            "amount": 500,
        },
    ]

    requests = procurement_read_vnext.shopping_request_rows_from_rows(rows)

    assert len(requests) == 1
    row = requests[0]
    assert row["request_integrity_valid"] is True
    assert row["request_integrity_status"] == "VALID"
    assert row["request_integrity_issues"] == []
    assert row["demand_org"] == "인천광역시 옹진군"
    assert row["vendor_bizno"] in {"123-45-67890", "1234567890"}
    assert row["contract_no"] == "C-001"


def test_request_level_pagination_never_splits_delivery_request(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    shopping_store_v41.ensure_schema()

    rows = [
        (
            "BOUNDARY-A-1",
            "REQ-BOUNDARY-A",
            "1",
            "2026-10-02",
            "경계검색 LED 보안등",
            "1000",
        ),
        (
            "BOUNDARY-A-2",
            "REQ-BOUNDARY-A",
            "2",
            "2026-10-02",
            "LED 보안등 추가규격",
            "2000",
        ),
        (
            "BOUNDARY-B-1",
            "REQ-BOUNDARY-B",
            "1",
            "2026-10-01",
            "LED 가로등",
            "500",
        ),
    ]
    for source_key, request_no, detail_seq, source_date, item_name, amount in rows:
        shopping_store_v41.preserve_record(
            "shopping_delivery",
            source_key,
            {
                "dlvrReqNo": request_no,
                "dlvrReqChgOrd": "0",
                "prdctSno": detail_seq,
                "dlvrReqRcptDate": source_date.replace("-", ""),
                "dtilPrdctClsfcNo": "3911160302",
                "prdctIdntNoNm": item_name,
                "dminsttNm": "인천광역시 옹진군",
                "cntrctCorpNm": "경계검증조명",
                "cntrctCorpBizno": "1234567890",
                "cntrctNo": "BOUNDARY-C",
                "prdctAmt": amount,
            },
            source_system="G2B",
            source_operation="request-boundary-test",
            source_date=source_date,
        )

    first, meta = procurement_read_vnext.shopping_request_rows(
        categories=("LIGHTING",),
        query="경계검색",
        region="인천광역시",
        start_date="2026-01-01",
        end_date="2026-12-31",
        limit=1,
        offset=0,
        with_meta=True,
    )

    assert len(first) == 1
    assert first[0]["delivery_req_no"] == "REQ-BOUNDARY-A"
    assert first[0]["request_detail_rows"] == 2
    assert first[0]["amount"] == 3000
    assert "경계검색 LED 보안등" in first[0]["item_name"]
    assert "LED 보안등 추가규격" in first[0]["item_name"]
    assert meta["pagination_basis"] == "DELIVERY_REQUEST"
    assert meta["request_boundary_complete"] is True
    assert meta["request_selection_strategy"] == "PORTABLE_GROUP_BY"
    assert meta["detail_rows_scanned"] == 2
    assert meta["request_keys_selected"] == 1

    page_one, page_one_meta = procurement_read_vnext.shopping_request_rows(
        categories=("LIGHTING",),
        region="인천광역시",
        start_date="2026-01-01",
        end_date="2026-12-31",
        limit=1,
        offset=0,
        with_meta=True,
    )
    page_two, page_two_meta = procurement_read_vnext.shopping_request_rows(
        categories=("LIGHTING",),
        region="인천광역시",
        start_date="2026-01-01",
        end_date="2026-12-31",
        limit=1,
        offset=1,
        with_meta=True,
    )

    assert page_one[0]["delivery_req_no"] == "REQ-BOUNDARY-A"
    assert page_one[0]["amount"] == 3000
    assert page_one_meta["detail_rows_scanned"] == 2
    assert page_two[0]["delivery_req_no"] == "REQ-BOUNDARY-B"
    assert page_two[0]["amount"] == 500
    assert page_two_meta["detail_rows_scanned"] == 1


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
