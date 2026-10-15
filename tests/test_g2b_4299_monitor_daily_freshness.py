"""V4.1.299: make D-1 source freshness independent of a January page's 100%.

All monitor checks are read-only on the API/render path. The test fixtures
use disposable SQLite, never live shopping/LOFIN APIs or production PG.
"""
import datetime as dt
import inspect
import json

from vnext_collection import COLLECTION_VERSION, _compact_marker
import collection_monitor_vnext as monitor
import vnext_clean_app as app
from vnext_store import save_checkpoint


FIXED_NOW = dt.datetime(2026, 10, 14, 8, 0, tzinfo=dt.timezone.utc)


def _shopping(snapshot):
    return next(
        row for row in snapshot["stages"]
        if row["dataset"] == "shopping_delivery"
    )


def _completed_scope(day, *, fetched=900, saved=208, verified=False):
    scope = f"{day}:{day}"
    cursor = ""
    if verified:
        # The D-1 green badge requires the collector's compact terminal
        # receipt contract, not only a legacy/unverified COMPLETE status.
        meta = {
            "version": COLLECTION_VERSION,
            "generation": f"fixture-{day}",
            "page_size": 100,
            "query_fingerprint": "synthetic-scope-fingerprint",
            "checkpoint_contract": "synthetic-shopping-scope",
            "completion_reason": "TOTAL_REACHED",
        }
        cp = {
            "dataset": "shopping_delivery",
            "scope_key": scope,
            "status": "COMPLETE",
            "page_no": 10,
            "page_size": 100,
            "source_total": fetched,
            "fetched_count": fetched,
            "saved_count": saved,
        }
        page_rows = [{
            "page_no": page,
            "page_size": 100,
            "response_hash": f"fixture-page-{page}",
            "item_count": 100,
            "source_total": fetched,
            "terminal_reason": "TOTAL_REACHED" if page == 9 else "",
        } for page in range(1, 10)]
        meta["compact_completion"] = _compact_marker(meta, cp, page_rows)
        cursor = json.dumps(meta, sort_keys=True)
    save_checkpoint(
        "shopping_delivery", scope,
        range_start=day, range_end=day,
        cursor_value=cursor,
        page_no=10, page_size=100, source_total=fetched,
        fetched_count=fetched, saved_count=saved,
        status="COMPLETE",
    )


def test_january_100_percent_does_not_claim_october_d_minus_one_is_current():
    _completed_scope("2026-01-06")
    snap = monitor.monitor_snapshot(now=FIXED_NOW)
    stage = _shopping(snap)
    assert stage["state"] == "COMPLETE"  # this remains the SINGLE receipt
    assert stage["percent"] == 100.0
    assert stage["pages_processed"] == 9
    assert stage["daily_due_date"] == "2026-10-13"
    assert stage["daily_checkpoint_status"] == "NOT_STARTED"
    assert stage["daily_checkpoint_complete"] is False
    html = app._collector_stage_html(stage)
    assert "전일 신규 자료 (2026-10-13): 미수집/미완료" in html
    assert "전일 미수집" in html
    assert "개별 날짜 진행률" in html
    assert "개별 날짜의 진행률" in html
    assert "2026-01-06:2026-01-06" in html
    assert snap["source_io_performed"] is False


def test_yesterday_complete_shows_separate_daily_green_evidence():
    _completed_scope("2026-01-06")
    _completed_scope("2026-10-13", verified=True)
    stage = _shopping(monitor.monitor_snapshot(now=FIXED_NOW))
    assert stage["daily_checkpoint_complete"] is True
    assert stage["daily_checkpoint_status"] == "COMPLETE"
    html = app._collector_stage_html(stage)
    assert "전일 신규 자료 (2026-10-13):" in html
    assert "수집 영수증 검증 완료" in html
    assert "전일 미수집" not in html
    assert "미수집/미완료" not in html


def test_today_only_has_an_unfinished_checkpoint_does_not_claim_complete():
    _completed_scope("2026-01-06")
    save_checkpoint(
        "shopping_delivery", "2026-10-13:2026-10-13",
        range_start="2026-10-13", range_end="2026-10-13",
        page_no=2, page_size=999, source_total=3000,
        fetched_count=999, saved_count=100,
        status="INCOMPLETE", last_error="MEMORY_PRESSURE",
    )
    stage = _shopping(monitor.monitor_snapshot(now=FIXED_NOW))
    assert stage["daily_checkpoint_status"] == "INCOMPLETE"
    assert stage["daily_checkpoint_complete"] is False
    assert "미수집/미완료" in app._collector_stage_html(stage)


def test_d1_monitor_lookup_is_bounded_and_read_only():
    source = inspect.getsource(monitor._shopping_stage)
    assert "WHERE dataset=? AND scope_key=?" in source
    assert "daily_checkpoint_status" in source
    assert "daily_checkpoint_complete" in source
    assert "LIMIT 1" in source
    assert "DROP TABLE" not in source
    assert "UPDATE collection_checkpoints" not in source
    assert "source_request" not in source
