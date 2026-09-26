import datetime as dt

import db
import collection_monitor_vnext
from vnext_store import preserve_raw, save_checkpoint


def _stage(snapshot, dataset):
    return next(row for row in snapshot["stages"] if row["dataset"] == dataset)


def test_monitor_reports_real_checkpoint_progress_without_source_io():
    preserve_raw(
        "bid_notice_goods",
        "goods-1",
        {"bidNtceNo": "TEST-1", "bidNtceNm": "테스트 공고"},
        source_system="G2B",
        source_operation="test",
        source_date="2026-09-25",
    )
    save_checkpoint(
        "bid_notice_goods",
        "2026-09-25:2026-09-25",
        range_start="2026-09-25",
        range_end="2026-09-25",
        page_no=3,
        page_size=100,
        source_total=450,
        fetched_count=200,
        saved_count=200,
        status="RUNNING",
    )

    snapshot = collection_monitor_vnext.monitor_snapshot(now=dt.datetime.now(dt.timezone.utc))
    stage = _stage(snapshot, "bid_notice_goods")

    assert snapshot["source_io_performed"] is False
    assert snapshot["collection_controls_enabled"] is False
    assert stage["state"] == "RUNNING"
    assert stage["state_label"] == "실행중"
    assert stage["pages_processed"] == 2
    assert stage["total_pages"] == 5
    assert stage["fetched_count"] == 200
    assert stage["saved_count"] == 200
    assert stage["raw_count"] == 1
    assert stage["percent"] == 44.4
    assert snapshot["summary"]["running"] == 1
    assert snapshot["recent_activity"][0]["dataset"] == "bid_notice_goods"


def test_monitor_never_reports_stale_running_checkpoint_as_currently_running():
    save_checkpoint(
        "shopping_delivery",
        "2026-09-01:2026-09-07",
        page_no=2,
        page_size=100,
        source_total=1000,
        fetched_count=100,
        saved_count=100,
        status="RUNNING",
    )
    with db.connect() as conn:
        conn.execute(
            """UPDATE collection_checkpoints
               SET updated_at='2000-01-01 00:00:00'
               WHERE dataset='shopping_delivery'"""
        )

    snapshot = collection_monitor_vnext.monitor_snapshot(
        now=dt.datetime(2026, 9, 26, 0, 0, tzinfo=dt.timezone.utc)
    )
    stage = _stage(snapshot, "shopping_delivery")

    assert stage["state"] == "STALE"
    assert stage["state_label"] == "갱신중단"
    assert snapshot["summary"]["running"] == 0
    assert snapshot["summary"]["errors"] == 1


def test_monitor_distinguishes_raw_only_data_and_live_hold():
    preserve_raw(
        "education_budget",
        "edu-1",
        {"YMQ": "2026", "교육청명": "경기도교육청", "사업명": "학교시설 개선"},
        source_system="지방교육재정알리미",
        source_operation="offline-test",
        source_date="2026",
    )

    snapshot = collection_monitor_vnext.monitor_snapshot()
    education = _stage(snapshot, "education_budget")

    assert education["state"] == "DATA_ONLY"
    assert education["raw_count"] == 1
    assert education["live_gate"] == "HOLD"
    assert snapshot["safety"]["education_live_transport_hold"] is True
    assert snapshot["safety"]["bulk_historical_hold"] is True


def test_monitor_reports_failed_checkpoint_and_error_message():
    save_checkpoint(
        "award_result_service",
        "2026-09-25:2026-09-25",
        page_no=4,
        page_size=100,
        source_total=500,
        fetched_count=300,
        saved_count=300,
        status="FAILED",
        last_error="SyntheticFailure",
    )

    snapshot = collection_monitor_vnext.monitor_snapshot()
    stage = _stage(snapshot, "award_result_service")

    assert stage["state"] == "FAILED"
    assert stage["state_label"] == "오류"
    assert stage["last_error"] == "SyntheticFailure"
    assert "SyntheticFailure" in stage["message"]
    assert snapshot["summary"]["errors"] == 1
