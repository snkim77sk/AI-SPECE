import datetime as dt

import db
import collection_monitor_vnext
import shopping_store_v41
from vnext_store import preserve_raw, save_checkpoint


def test_production_monitor_read_path_does_not_repeat_schema_ddl(monkeypatch):
    import inspect

    source = inspect.getsource(collection_monitor_vnext.monitor_snapshot)

    assert 'G2B_TEST_MODE' in source
    assert "if test_mode:" in source
    test_guard = source.index("if test_mode:")
    assert source.index("ensure_foundation()", test_guard) > test_guard
    assert source.index("shopping_store_v41.ensure_schema()", test_guard) > test_guard


def _stage(snapshot, dataset):
    return next(row for row in snapshot["stages"] if row["dataset"] == dataset)


def test_shopping_monitor_window_uses_exact_27_calendar_months(monkeypatch):
    monkeypatch.setenv("G2B_SHOPPING_RETENTION_MONTHS", "27")
    now = dt.datetime(
        2028, 5, 31, 3, 0, tzinfo=dt.timezone.utc
    )
    assert collection_monitor_vnext._shopping_window_start(now) == dt.date(
        2026, 2, 28
    )

    snapshot = collection_monitor_vnext.monitor_snapshot(now=now)
    operational = snapshot["operational_recent"]
    assert operational["bootstrap_start_date"] == "2026-01-01"
    assert operational["start_date"] == "2026-02-28"
    assert operational["retention_months"] == 27
    assert operational["retention_policy"] == "CALENDAR_MONTHS"


def test_budget_history_progress_counts_current_and_history_complete_days():
    scopes = [
        {
            "scope_key": "history:2026:2026-01-01",
            "status": "COMPLETE",
        },
        {
            "scope_key": "2026:2026-01-02",
            "status": "COMPLETE",
        },
        {
            "scope_key": "history:2026:2026-01-03",
            "status": "RUNNING",
        },
    ]
    progress = collection_monitor_vnext._budget_history_progress(
        scopes,
        dt.datetime(2026, 1, 4, 3, 0, tzinfo=dt.timezone.utc),
    )

    assert progress["history_start_date"] == "2026-01-01"
    assert progress["history_latest_date"] == "2026-01-03"
    assert progress["history_complete_days"] == 2
    assert progress["history_total_days"] == 3
    assert progress["history_percent"] == 66.7
    assert progress["history_next_date"] == "2026-01-03"


def test_budget_history_progress_rolls_start_after_one_year(monkeypatch):
    monkeypatch.setenv("G2B_BUDGET_RETENTION_DAYS", "365")
    progress = collection_monitor_vnext._budget_history_progress(
        [],
        dt.datetime(2027, 1, 1, 16, 0, tzinfo=dt.timezone.utc),
    )

    # 2027-01-02 KST: source-safe rolling floor is 2026-01-02.
    assert progress["history_start_date"] == "2026-01-02"
    assert progress["history_latest_date"] == "2027-01-01"
    assert progress["history_total_days"] == 365
    assert progress["history_next_date"] == "2026-01-02"


def test_aidfa_year_statuses_separate_current_and_future():
    now = dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.timezone.utc)
    scopes = [
        {
            "scope_key": "2026:ALL",
            "range_start": "2026",
            "range_end": "ALL",
            "page_no": 3,
            "page_size": 1000,
            "source_total": 1500,
            "fetched_count": 1500,
            "saved_count": 1500,
            "status": "COMPLETE",
            "last_error": "",
            "updated_at": "2026-10-02T11:55:00+00:00",
        },
        {
            "scope_key": "2027:ALL",
            "range_start": "2027",
            "range_end": "ALL",
            "page_no": 2,
            "page_size": 1000,
            "source_total": 2500,
            "fetched_count": 1000,
            "saved_count": 1000,
            "status": "RUNNING",
            "last_error": "",
            "updated_at": "2026-10-02T11:59:00+00:00",
        },
    ]

    items = collection_monitor_vnext._aidfa_year_statuses(scopes, now)

    assert [item["year"] for item in items] == [2026, 2027]
    assert items[0]["role"] == "현재연도 기초편성"
    assert items[0]["state"] == "COMPLETE"
    assert items[0]["state_label"] == "완료"
    assert items[1]["role"] == "다음연도 미래예산"
    assert items[1]["state"] == "RUNNING"
    assert items[1]["state_label"] == "실행중"
    assert collection_monitor_vnext._aidfa_combined_state(items) == "RUNNING"


def test_aidfa_combined_state_is_partial_when_only_one_year_is_complete():
    items = [
        {"state": "COMPLETE"},
        {"state": "NOT_STARTED"},
    ]
    assert collection_monitor_vnext._aidfa_combined_state(items) == "PARTIAL"


def test_monitor_reports_real_checkpoint_progress_without_source_io():
    preserve_raw(
        "shopping_delivery",
        "shopping-1",
        {"dlvrReqNo": "REQ-1", "prdctSno": "1", "dtilPrdctClsfcNo": "3911160302"},
        source_system="G2B",
        source_operation="test",
        source_date="2026-09-25",
    )
    save_checkpoint(
        "shopping_delivery",
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
    stage = _stage(snapshot, "shopping_delivery")

    assert snapshot["source_io_performed"] is False
    assert snapshot["collection_controls_enabled"] is True
    assert snapshot["operational_recent"]["order"] == "FORWARD"
    assert stage["state"] == "RUNNING"
    assert stage["state_label"] == "실행중"
    assert stage["pages_processed"] == 2
    assert stage["total_pages"] == 5
    assert stage["fetched_count"] == 200
    assert stage["saved_count"] == 200
    assert stage["raw_count"] == 1
    assert stage["percent"] == 44.4
    assert snapshot["summary"]["running"] == 1
    assert snapshot["recent_activity"][0]["dataset"] == "shopping_delivery"


def test_production_shopping_stage_separates_active_and_history(monkeypatch):
    shopping_store_v41.ensure_schema()
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "ACTIVE-1",
        {
            "dlvrReqNo": "ACTIVE-1",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20260925",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 보안등기구",
        },
        source_system="G2B",
        source_operation="test",
        source_date="2026-09-25",
    )
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "INACTIVE-1",
        {
            "dlvrReqNo": "INACTIVE-1",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20260925",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 보안등기구",
        },
        source_system="G2B",
        source_operation="test",
        source_date="2026-09-25",
    )
    with db.connect() as conn:
        conn.execute(
            """UPDATE shopping_records
               SET is_active=0,
                   inactive_reason='MISSING_FROM_COMPLETE_SOURCE'
               WHERE source_key='INACTIVE-1'"""
        )

    with db.connect() as conn:
        monkeypatch.setenv("G2B_TEST_MODE", "0")
        stage, _rows = collection_monitor_vnext._shopping_stage(
            conn,
            collection_monitor_vnext.STAGES[0],
            dt.datetime.now(dt.timezone.utc),
        )

        assert stage["raw_count"] == 1
        assert stage["active_count"] == 1
        assert stage["history_count"] == 2
        assert stage["inactive_count"] == 1
        assert stage["state"] == "DATA_ONLY"
        assert stage["message"] == "현재 유효 1건 · 보존 이력 2건"

        # If only inactive history remains, the stage still represents stored
        # history rather than an uncollected source.
        conn.execute(
            """UPDATE shopping_records
               SET is_active=0,
                   inactive_reason='MISSING_FROM_COMPLETE_SOURCE'
               WHERE source_key='ACTIVE-1'"""
        )
        stage, _rows = collection_monitor_vnext._shopping_stage(
            conn,
            collection_monitor_vnext.STAGES[0],
            dt.datetime.now(dt.timezone.utc),
        )

        assert stage["raw_count"] == 0
        assert stage["history_count"] == 2
        assert stage["inactive_count"] == 2
        assert stage["state"] == "DATA_ONLY"
        assert stage["message"] == "현재 유효 0건 · 보존 이력 2건"


def test_local_collector_monitor_uses_normalized_shopping(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    shopping_store_v41.ensure_schema()
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "LOCAL-MONITOR",
        {
            "dlvrReqNo": "LOCAL-MONITOR",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20261001",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 로컬 모니터",
        },
        source_system="G2B",
        source_operation="local-test",
        source_date="2026-10-01",
    )
    # Legacy RAW exists but must not become the LOCAL_COLLECTOR count.
    preserve_raw(
        "shopping_delivery",
        "LOCAL-MONITOR-LEGACY",
        {
            "dlvrReqNo": "LOCAL-MONITOR-LEGACY",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
        },
        source_system="G2B",
        source_date="2026-10-01",
    )

    snapshot = collection_monitor_vnext.monitor_snapshot(
        now=dt.datetime(2026, 10, 3, 3, 0, tzinfo=dt.timezone.utc)
    )
    stage = _stage(snapshot, "shopping_delivery")

    assert stage["raw_count"] == 1
    assert stage["active_count"] == 1
    assert stage["history_count"] == 1
    assert stage["inactive_count"] == 0


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
    assert education["live_gate"] == "HOLD · TRANSPORT_VALIDATION_REQUIRED"
    assert snapshot["safety"]["education_live_transport_hold"] is True
    assert snapshot["safety"]["service_collection_removed"] is True
    assert snapshot["safety"]["goods_bid_collection_removed"] is True


def test_monitor_reports_failed_checkpoint_and_error_message():
    save_checkpoint(
        "shopping_delivery",
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
    stage = _stage(snapshot, "shopping_delivery")

    assert stage["state"] == "FAILED"
    assert stage["state_label"] == "오류"
    assert stage["last_error"] == "SyntheticFailure"
    assert "SyntheticFailure" in stage["message"]
    assert snapshot["summary"]["errors"] == 1
