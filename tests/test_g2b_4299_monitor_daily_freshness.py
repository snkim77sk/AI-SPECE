"""V4.1.299: make D-1 source freshness independent of a January page's 100%.

All monitor checks are read-only on the API/render path. The test fixtures
use disposable SQLite, never live shopping/LOFIN APIs or production PG.
"""
import datetime as dt
import inspect

import collection_monitor_vnext as monitor
import vnext_clean_app as app
from vnext_store import save_checkpoint


FIXED_NOW = dt.datetime(2026, 10, 14, 8, 0, tzinfo=dt.timezone.utc)


def _shopping(snapshot):
    return next(
        row for row in snapshot["stages"]
        if row["dataset"] == "shopping_delivery"
    )


def _completed_scope(day, *, fetched=900, saved=208):
    save_checkpoint(
        "shopping_delivery", f"{day}:{day}",
        range_start=day, range_end=day,
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
    assert "개별 날짜의 진행률" in html
    assert "2026-01-06:2026-01-06" in html
    assert snap["source_io_performed"] is False


def test_yesterday_complete_shows_separate_daily_green_evidence():
    _completed_scope("2026-01-06")
    _completed_scope("2026-10-13")
    stage = _shopping(monitor.monitor_snapshot(now=FIXED_NOW))
    assert stage["daily_checkpoint_complete"] is True
    assert stage["daily_checkpoint_status"] == "COMPLETE"
    html = app._collector_stage_html(stage)
    assert "전일 신규 자료 (2026-10-13):" in html
    assert "체크포인트 완료" in html
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
