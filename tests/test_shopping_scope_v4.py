import datetime as dt

import db
import shopping_scope_v4
import shopping_vnext
import vnext_store
from vnext_collection import verified_checkpoint


def test_scope_starts_on_2026_01_01_and_preserves_checkpoint_contract():
    assert shopping_scope_v4.START_DATE == dt.date(2026, 1, 1)
    assert shopping_scope_v4.validate_start_date("2026-01-01") == dt.date(2026, 1, 1)
    # Existing October+ resumable checkpoints keep the same contract identifier.
    assert shopping_scope_v4.SCOPE_VERSION == "shopping-lighting-pole-v2-20261001"
    try:
        shopping_scope_v4.validate_start_date("2025-12-31")
    except ValueError as exc:
        assert "2026-01-01" in str(exc)
    else:
        raise AssertionError("pre-2026 shopping date must be rejected")


def test_match_backfill_scope_is_strictly_2025():
    assert shopping_scope_v4.validate_match_backfill_date(
        "2025-01-01"
    ) == dt.date(2025, 1, 1)
    assert shopping_scope_v4.validate_match_backfill_date(
        "2025-12-31"
    ) == dt.date(2025, 12, 31)

    for value in ("2024-12-31", "2026-01-01"):
        try:
            shopping_scope_v4.validate_match_backfill_date(value)
        except ValueError as exc:
            assert "2025-01-01..2025-12-31" in str(exc)
        else:
            raise AssertionError("match backfill must stay inside 2025")


def test_scope_uses_exact_product_codes_only():
    assert shopping_scope_v4.target_group(
        {"dtilPrdctClsfcNo": "3911160302", "prdctNm": "LED 가로등"}
    ) == "LIGHTING"
    assert shopping_scope_v4.target_group(
        {"dtilPrdctClsfcNo": "3911152601", "prdctNm": "가로등주"}
    ) == "POLE"
    assert shopping_scope_v4.target_group(
        {"dtilPrdctClsfcNo": "9999999999", "prdctNm": "LED라는 단어만 포함"}
    ) == ""


def test_full_source_receipt_stores_only_lighting_and_pole(monkeypatch):
    rows = [
        {
            "dlvrReqNo": "REQ-LIGHT",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 가로등",
        },
        {
            "dlvrReqNo": "REQ-OTHER",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "9999999999",
            "prdctNm": "일반 사무용품",
        },
    ]
    monkeypatch.setattr(shopping_vnext, "fetch_page", lambda *a, **k: (rows, 2))

    result = shopping_vnext.collect_all(
        "2026-09-01", "2026-09-01",
        page_size=2, max_pages=1, resume=False,
    )

    assert result["complete"] is True
    assert result["fetched"] == 2
    assert result["saved"] == 1

    with db.connect() as conn:
        raw = conn.execute(
            "SELECT source_key FROM raw_records WHERE dataset='shopping_delivery'"
        ).fetchall()
        receipts = conn.execute(
            """SELECT source_key,stored FROM vnext_collection_items
               WHERE dataset='shopping_delivery'
               ORDER BY source_key"""
        ).fetchall()

    assert len(raw) == 1
    assert str(raw[0]["source_key"]) == shopping_vnext._source_key(rows[0])
    assert sorted(int(row["stored"]) for row in receipts) == [0, 1]

    checkpoint = vnext_store.get_checkpoint(
        "shopping_delivery", "2026-09-01:2026-09-01"
    )
    assert verified_checkpoint(checkpoint) is True
