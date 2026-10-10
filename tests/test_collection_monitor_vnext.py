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
    assert snapshot["recent_activity"][0]["status"] == "RUNNING"
    assert snapshot["recent_activity"][0]["status_label"] == "실행중"
    assert snapshot["recent_activity"][0]["checkpoint_status"] == "RUNNING"


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
    recent = next(row for row in snapshot["recent_activity"]
                  if row["dataset"] == "shopping_delivery")
    assert recent["status"] == "STALE"
    assert recent["status_label"] == "갱신중단"
    assert recent["checkpoint_status"] == "RUNNING"
    assert recent["saved_count"] == 100


def test_budget_recent_activity_stale_badge_matches_stage_without_checkpoint_write():
    save_checkpoint(
        "budget",
        "history:2026:2026-01-02",
        range_start="2026",
        range_end="2026-01-02",
        page_no=272,
        page_size=1000,
        source_total=420000,
        fetched_count=271000,
        saved_count=271000,
        status="RUNNING",
    )
    # Historic worker died after durable page 271; its checkpoint is still
    # RUNNING by design. No source call or checkpoint mutation is allowed.
    original = "2026-10-08T15:00:00+00:00"
    with db.connect() as conn:
        conn.execute(
            "UPDATE collection_checkpoints SET updated_at=? WHERE dataset=?",
            (original, "budget"),
        )
    snapshot = collection_monitor_vnext.monitor_snapshot(
        now=dt.datetime(2026, 10, 8, 15, 10, tzinfo=dt.timezone.utc)
    )
    stage = _stage(snapshot, "budget")
    recent = next(row for row in snapshot["recent_activity"]
                  if row["dataset"] == "budget")
    assert stage["state"] == "STALE"
    assert recent["status"] == "STALE"
    assert recent["status_label"] == "갱신중단"
    assert recent["checkpoint_status"] == "RUNNING"
    assert recent["saved_count"] == 271000
    assert recent["pages_processed"] == 271
    with db.connect() as conn:
        persisted = conn.execute(
            "SELECT status,updated_at,fetched_count,saved_count "
            "FROM collection_checkpoints WHERE dataset=?",
            ("budget",),
        ).fetchone()
    assert persisted["status"] == "RUNNING"
    assert persisted["updated_at"] == original
    assert persisted["fetched_count"] == 271000
    assert persisted["saved_count"] == 271000


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

def test_shopping_monitor_bounds_recent_checkpoint_rows_but_keeps_total_count():
    for index in range(5):
        save_checkpoint(
            "shopping_delivery",
            f"2026-09-{index + 1:02d}:2026-09-{index + 1:02d}",
            range_start=f"2026-09-{index + 1:02d}",
            range_end=f"2026-09-{index + 1:02d}",
            page_no=2,
            page_size=100,
            source_total=10,
            fetched_count=10,
            saved_count=10,
            status="COMPLETE",
        )

    with db.connect() as conn:
        stage, rows = collection_monitor_vnext._shopping_stage(
            conn,
            collection_monitor_vnext.STAGES[0],
            dt.datetime.now(dt.timezone.utc),
            recent_limit=2,
        )

    assert len(rows) == 2
    assert stage["checkpoint_count"] == 5
    assert stage["complete_scopes"] == 5



def test_partition_complete_checkpoint_renders_as_completed_stage():
    now = collection_monitor_vnext._utc_now()
    state = collection_monitor_vnext._state_for(
        {
            "status": "PARTITION_COMPLETE",
            "updated_at": now.isoformat(),
        },
        100,
        now,
    )

    assert state == "COMPLETE"
    assert (
        collection_monitor_vnext.STATUS_LABELS["PARTITION_COMPLETE"]
        == "지역분할완료"
    )


def test_budget_partition_progress_shows_completed_active_and_region_name(monkeypatch):
    import budget_vnext

    monkeypatch.setattr(
        budget_vnext,
        "operational_region_partition_plan",
        lambda year: {
            "ready": True,
            "reason": "STORED_CURRENT_REGION_PLAN",
            "region_codes": ["1100000", "2600000", "2800000"],
            "region_names": {
                "1100000": "서울특별시",
                "2600000": "부산광역시",
                "2800000": "인천광역시",
            },
            "region_count": 3,
            "minimum_regions": 3,
        },
    )
    scopes = [
        {
            "scope_key": "2026:2026-10-07",
            "status": "INCOMPLETE",
            "last_error": "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED",
            "updated_at": "2026-10-08T15:00:00+00:00",
        },
        {
            "scope_key": "2026:2026-10-07:1100000",
            "status": "COMPLETE",
            "page_no": 4,
            "page_size": 1000,
            "source_total": 3000,
            "fetched_count": 3000,
            "saved_count": 3000,
            "updated_at": "2026-10-08T15:01:00+00:00",
        },
        {
            "scope_key": "2026:2026-10-07:2600000",
            "status": "COMPLETE",
            "page_no": 3,
            "page_size": 1000,
            "source_total": 2000,
            "fetched_count": 2000,
            "saved_count": 2000,
            "updated_at": "2026-10-08T15:02:00+00:00",
        },
        {
            "scope_key": "2026:2026-10-07:2800000",
            "status": "RUNNING",
            "page_no": 5,
            "page_size": 1000,
            "source_total": 9000,
            "fetched_count": 4000,
            "saved_count": 4000,
            "updated_at": "2026-10-08T15:03:00+00:00",
        },
    ]

    progress = collection_monitor_vnext._budget_partition_progress(
        scopes, now=dt.datetime(2026, 10, 8, 15, 4, tzinfo=dt.timezone.utc)
    )

    assert progress["partition_mode"] is True
    assert progress["partition_snapshot_date"] == "2026-10-07"
    assert progress["partition_total_regions"] == 3
    assert progress["partition_complete_regions"] == 2
    assert progress["partition_percent"] == 66.7
    assert progress["partition_active_region_name"] == "인천광역시"
    assert progress["partition_active_pages"] == 4
    assert progress["partition_state"] == "RUNNING"
    assert "2/3 지역 완료" in progress["partition_message"]
    assert "현재 인천광역시" in progress["partition_message"]
    assert collection_monitor_vnext._budget_scope_display(
        "budget",
        "2026:2026-10-07:2800000",
        progress["partition_region_names"],
    ) == "지역분할 · 인천광역시 · 2026-10-07"
    assert collection_monitor_vnext._budget_scope_display(
        "budget",
        "history:2026:2026-01-02",
        progress["partition_region_names"],
    ) == "과거이력 · 2026-01-02"



def test_partition_stale_running_checkpoint_is_not_reported_as_running(monkeypatch):
    import budget_vnext

    monkeypatch.setattr(
        budget_vnext,
        "operational_region_partition_plan",
        lambda _year: {
            "ready": True,
            "region_codes": ["2800000"],
            "region_names": {"2800000": "인천광역시"},
            "region_count": 1,
        },
    )
    scopes = [{
        "scope_key": "2026:2026-10-07:2800000",
        "status": "RUNNING",
        "updated_at": "2026-10-08T15:02:00+00:00",
        "page_no": 3,
        "page_size": 1000,
        "source_total": 5000,
        "fetched_count": 2000,
        "saved_count": 2000,
    }]
    recent = collection_monitor_vnext._budget_partition_progress(
        scopes, now=dt.datetime(2026, 10, 8, 15, 4, tzinfo=dt.timezone.utc)
    )
    assert recent["partition_state"] == "RUNNING"

    stale_now = dt.datetime(2026, 10, 8, 15, 10, tzinfo=dt.timezone.utc)
    stale = collection_monitor_vnext._budget_partition_progress(
        scopes, now=stale_now
    )
    assert stale["partition_state"] == "STALE"
    assert stale["partition_active_state"] == "STALE"
    assert "갱신중단" in stale["partition_message"]
    assert "인천광역시" in stale["partition_message"]
    assert stale["partition_active_saved"] == 2000

    stage, _ = collection_monitor_vnext._budget_stage(
        collection_monitor_vnext.STAGES[1],
        {
            "raw_rows": 2000,
            "raw_revisions": 2000,
            "raw_backend": "POSTGRESQL",
            "checkpoint_count": 1,
            "checkpoint_status_counts": {"RUNNING": 1},
            "scopes": scopes,
        },
        stale_now,
    )
    assert stage["state"] == "STALE"
    assert stage["partition_active_state"] == "STALE"
    assert stage["saved_count"] == 2000
    assert "갱신중단" in stage["message"]


def test_budget_stage_uses_region_partition_as_primary_current_progress(monkeypatch):
    import budget_vnext

    monkeypatch.setattr(
        budget_vnext,
        "operational_region_partition_plan",
        lambda year: {
            "ready": True,
            "reason": "STORED_CURRENT_REGION_PLAN",
            "region_codes": ["1100000", "2800000"],
            "region_names": {
                "1100000": "서울특별시",
                "2800000": "인천광역시",
            },
            "region_count": 2,
            "minimum_regions": 2,
        },
    )
    dataset_status = {
        "raw_rows": 5000,
        "raw_revisions": 5200,
        "raw_backend": "POSTGRESQL",
        "checkpoint_count": 3,
        "checkpoint_status_counts": {"COMPLETE": 1, "RUNNING": 1, "INCOMPLETE": 1},
        "verified_complete_scopes": 0,
        "compacted_complete_scopes": 0,
        "partition_complete_scopes": 0,
        "scopes": [
            {
                "scope_key": "2026:2026-10-07",
                "status": "INCOMPLETE",
                "last_error": "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED",
                "updated_at": "2026-10-08T15:00:00+00:00",
            },
            {
                "scope_key": "2026:2026-10-07:1100000",
                "status": "COMPLETE",
                "updated_at": "2026-10-08T15:01:00+00:00",
            },
            {
                "scope_key": "2026:2026-10-07:2800000",
                "status": "RUNNING",
                "page_no": 3,
                "page_size": 1000,
                "source_total": 5000,
                "fetched_count": 2000,
                "saved_count": 2000,
                "updated_at": "2026-10-08T15:02:00+00:00",
            },
        ],
    }

    stage, _scopes = collection_monitor_vnext._budget_stage(
        collection_monitor_vnext.STAGES[1],
        dataset_status,
        dt.datetime(2026, 10, 8, 15, 3, tzinfo=dt.timezone.utc),
    )

    assert stage["state"] == "RUNNING"
    assert stage["scope"] == "지역분할 · 2026-10-07"
    assert stage["partition_complete_regions"] == 1
    assert stage["partition_total_regions"] == 2
    assert "현재 인천광역시" in stage["message"]
    assert stage["last_error"] == ""
