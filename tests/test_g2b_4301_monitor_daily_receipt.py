"""V4.1.300 audit: do not equate a bare COMPLETE flag with verified D-1 data.

All verification is read-only on the monitor's indexed day checkpoint.
The real production collector supplies compact completion markers. Synthetic
fixtures here test the published marker contract and rejection of contradictions.
"""
import datetime as dt
import json

import collection_monitor_vnext as mon
import db
import vnext_clean_app as app
from vnext_collection import COLLECTION_VERSION, _compact_marker
from vnext_store import save_checkpoint


_NOW = dt.datetime(2026, 10, 15, 1, 0, tzinfo=dt.timezone.utc)
_DAY = "2026-10-14"
_SCOPE = f"{_DAY}:{_DAY}"


def _save_daily(*, cursor_value="", saved=1, status="COMPLETE"):
    save_checkpoint(
        "shopping_delivery", _SCOPE,
        range_start=_DAY, range_end=_DAY,
        cursor_value=cursor_value,
        page_no=2, page_size=2, source_total=2,
        fetched_count=2, saved_count=saved, status=status,
    )


def _stage():
    with db.connect() as conn:
        sql = []
        conn.set_trace_callback(sql.append)
        stage, _rows = mon._shopping_stage(conn, mon.STAGES[0], _NOW)
    # Never make monitor DDL/write calls, fetch source APIs, or stream receipts.
    normalized = "\n".join(sql).upper()
    assert "VNEXT_COLLECTION_ITEMS" not in normalized
    assert "VNEXT_COLLECTION_PAGES" not in normalized
    assert not any(verb in normalized for verb in
                   ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "ALTER TABLE"))
    return stage


def _compact_cursor():
    meta = {
        "version": COLLECTION_VERSION,
        "generation": "ci-compact-test",
        "page_size": 2,
        "completion_reason": "TOTAL_REACHED",
        "checkpoint_contract": "shopping-v4-fixture",
        "query_fingerprint": "fixture-fingerprint",
    }
    cp = {
        "dataset": "shopping_delivery", "scope_key": _SCOPE,
        "status": "COMPLETE", "page_no": 2, "page_size": 2,
        "source_total": 2, "fetched_count": 2, "saved_count": 1,
    }
    page_rows = [{
        "page_no": 1, "page_size": 2, "response_hash": "fixture-hash",
        "item_count": 2, "source_total": 2,
        "terminal_reason": "TOTAL_REACHED",
    }]
    meta["compact_completion"] = _compact_marker(meta, cp, page_rows)
    return json.dumps(meta, sort_keys=True)


def test_day_missing_is_not_marked_complete():
    stage = _stage()
    assert stage["daily_due_date"] == _DAY
    assert stage["daily_checkpoint_status"] == "NOT_STARTED"
    assert stage["daily_checkpoint_complete"] is False
    assert stage["daily_checkpoint_verification"] == "NOT_COMPLETE"


def test_complete_flag_without_verified_receipt_is_not_reassuring():
    _save_daily()
    stage = _stage()
    assert stage["daily_checkpoint_status"] == "COMPLETE"
    assert stage["daily_checkpoint_complete"] is False
    assert stage["daily_checkpoint_verification"] == "UNVERIFIED_RECEIPT"
    html = app._collector_stage_html(stage)
    assert "전일 영수증 검증 대기" in html
    assert "미수집 확정이 아니며" in html


def test_compact_terminal_receipt_is_verified_without_loading_pages():
    _save_daily(cursor_value=_compact_cursor())
    stage = _stage()
    assert stage["daily_checkpoint_complete"] is True
    assert stage["daily_checkpoint_verification"] == "VERIFIED_COMPACT"
    assert "수집 영수증 검증 완료" in app._collector_stage_html(stage)


def test_stale_or_mismatched_compact_counters_fail_closed():
    _save_daily(cursor_value=_compact_cursor(), saved=0)
    stage = _stage()
    assert stage["daily_checkpoint_status"] == "COMPLETE"
    assert stage["daily_checkpoint_complete"] is False
    assert stage["daily_checkpoint_verification"] == "UNVERIFIED_RECEIPT"
