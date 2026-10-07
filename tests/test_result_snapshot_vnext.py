import datetime as dt
import os

import db
import budget_pg_store
import budget_reorganize_vnext
import collection_monitor_vnext
import result_server_maintenance
import result_snapshot_vnext
import shopping_store_v41
import runtime_role
import vnext_clean_db
import vnext_store
from vnext_schema import CLASSIFIER_VERSION


def _snapshot(snapshot_id, *, shopping=None, vendors=None):
    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "generated_at_utc": "2026-09-29T03:00:00+00:00",
        "source_version": "3.2.0",
        "sections": {
            "shopping": shopping or [],
            "vendors": vendors or [],
            "budget_targets": [],
            "budget_prebid": [],
        },
        "collection_status": {"summary": {"total_raw": 1234}, "stages": [], "recent_activity": []},
        "readiness": {"status": "LOCAL_RESULT_READY", "status_scope": "LOCAL"},
        "source_counts": {
            "raw": {"shopping_delivery": len(shopping or [])},
            "target": {"shopping_delivery": len(shopping or [])},
            "history": {"shopping_delivery": 1234},
            "inactive": {
                "shopping_delivery": max(0, 1234 - len(shopping or []))
            },
        },
    }


def test_local_snapshot_reports_current_shopping_separately_from_history():
    vnext_store.preserve_raw(
        "shopping_delivery",
        "SNAP-SHOP",
        {
            "dlvrReqNo": "SNAP-SHOP",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctIdntNoNm": "LED 보안등",
        },
        source_system="G2B",
        source_date="2026-09-25",
    )
    import classification_vnext
    classification_vnext.classify_dataset("shopping_delivery")

    snapshot = result_snapshot_vnext.build_local_snapshot()

    assert snapshot["source_counts"]["raw"]["shopping_delivery"] == 1
    assert snapshot["source_counts"]["target"]["shopping_delivery"] == 1
    assert snapshot["source_counts"]["history"]["shopping_delivery"] == 1
    assert snapshot["source_counts"]["inactive"]["shopping_delivery"] == 0
    assert len(snapshot["sections"]["shopping"]) == 1


def test_local_snapshot_includes_bounded_regional_vendor_sections(monkeypatch):
    import procurement_read_vnext

    calls = []
    original_vendor_rows = procurement_read_vnext.vendor_rows

    def wrapped_vendor_rows(**kwargs):
        calls.append(dict(kwargs))
        return original_vendor_rows(**kwargs)

    monkeypatch.setattr(
        procurement_read_vnext,
        "vendor_rows",
        wrapped_vendor_rows,
    )

    snapshot = result_snapshot_vnext.build_local_snapshot()

    assert "vendors" in snapshot["sections"]
    assert "vendors:인천광역시" in snapshot["sections"]
    regional_calls = [call for call in calls if call.get("region")]
    assert regional_calls
    assert all(
        call["limit"] == result_snapshot_vnext.MAX_VENDOR_REGION_ROWS
        for call in regional_calls
    )


def test_local_collector_snapshot_uses_normalized_shopping_counts(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    shopping_store_v41.ensure_schema()
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "LOCAL-SNAPSHOT-ACTIVE",
        {
            "dlvrReqNo": "LOCAL-SNAPSHOT-ACTIVE",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20261001",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 로컬 스냅샷 A",
            "cntrctCorpNm": "로컬스냅샷조명",
        },
        source_system="G2B",
        source_operation="local-test",
        source_date="2026-10-01",
    )
    shopping_store_v41.preserve_record(
        "shopping_delivery",
        "LOCAL-SNAPSHOT-INACTIVE",
        {
            "dlvrReqNo": "LOCAL-SNAPSHOT-INACTIVE",
            "dlvrReqChgOrd": "0",
            "prdctSno": "1",
            "dlvrReqRcptDate": "20261001",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 로컬 스냅샷 B",
            "cntrctCorpNm": "로컬스냅샷조명",
        },
        source_system="G2B",
        source_operation="local-test",
        source_date="2026-10-01",
    )
    with db.connect() as conn:
        conn.execute(
            """UPDATE shopping_records
               SET is_active=0,
                   inactive_reason='MISSING_FROM_COMPLETE_SOURCE'
               WHERE source_key='LOCAL-SNAPSHOT-INACTIVE'"""
        )
    # Legacy RAW must not affect LOCAL_COLLECTOR result sections/counts.
    vnext_store.preserve_raw(
        "shopping_delivery",
        "LOCAL-SNAPSHOT-LEGACY",
        {
            "dlvrReqNo": "LOCAL-SNAPSHOT-LEGACY",
            "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctIdntNoNm": "LED legacy local snapshot",
        },
        source_system="G2B",
        source_date="2026-10-01",
    )

    snapshot = result_snapshot_vnext.build_local_snapshot()

    assert [row["source_key"] for row in snapshot["sections"]["shopping"]] == [
        "LOCAL-SNAPSHOT-ACTIVE"
    ]
    assert snapshot["source_counts"]["raw"]["shopping_delivery"] == 1
    assert snapshot["source_counts"]["target"]["shopping_delivery"] == 1
    assert snapshot["source_counts"]["history"]["shopping_delivery"] == 2
    assert snapshot["source_counts"]["inactive"]["shopping_delivery"] == 1


def test_retention_immediately_updates_monitor_and_local_snapshot(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    shopping_store_v41.ensure_schema()

    rows = [
        ("RET-SURFACE-OLD", "2026-10-02"),
        ("RET-SURFACE-ACTIVE", "2026-10-03"),
        ("RET-SURFACE-INACTIVE", "2026-10-03"),
    ]
    for source_key, source_date in rows:
        shopping_store_v41.preserve_record(
            "shopping_delivery",
            source_key,
            {
                "dlvrReqNo": source_key,
                "dlvrReqChgOrd": "0",
                "prdctSno": "1",
                "dlvrReqRcptDate": source_date.replace("-", ""),
                "dtilPrdctClsfcNo": "3911160302",
                "prdctNm": "LED retention surface",
                "cntrctCorpNm": "retention surface vendor",
            },
            source_system="G2B",
            source_operation="surface-test",
            source_date=source_date,
        )

    with db.connect() as conn:
        conn.execute(
            """UPDATE shopping_records
               SET is_active=0,
                   inactive_reason='MISSING_FROM_COMPLETE_SOURCE'
               WHERE source_key='RET-SURFACE-INACTIVE'"""
        )

    before = shopping_store_v41.count()
    assert before["active_records"] == 2
    assert before["history_records"] == 3
    assert before["inactive_records"] == 1

    purged = shopping_store_v41.purge_history(
        365,
        now=dt.datetime(
            2027, 10, 3, 12, 0,
            tzinfo=dt.timezone(dt.timedelta(hours=9)),
        ),
    )
    assert purged["deleted_records"] == 1

    after = shopping_store_v41.count()
    assert after["active_records"] == 1
    assert after["history_records"] == 2
    assert after["inactive_records"] == 1

    monitor = collection_monitor_vnext.monitor_snapshot(
        now=dt.datetime(2027, 10, 3, 3, 0, tzinfo=dt.timezone.utc)
    )
    assert monitor["summary"]["shopping_active_records"] == 1
    assert monitor["summary"]["shopping_history_records"] == 2
    assert monitor["summary"]["shopping_inactive_records"] == 1

    snapshot = result_snapshot_vnext.build_local_snapshot()
    assert snapshot["source_counts"]["raw"]["shopping_delivery"] == 1
    assert snapshot["source_counts"]["target"]["shopping_delivery"] == 1
    assert snapshot["source_counts"]["history"]["shopping_delivery"] == 2
    assert snapshot["source_counts"]["inactive"]["shopping_delivery"] == 1
    assert [
        row["source_key"] for row in snapshot["sections"]["shopping"]
    ] == ["RET-SURFACE-ACTIVE"]


def test_runtime_role_defaults_to_unified(monkeypatch):
    monkeypatch.delenv("G2B_RUNTIME_ROLE", raising=False)
    assert runtime_role.runtime_role() == runtime_role.UNIFIED
    assert runtime_role.is_unified() is True
    assert runtime_role.can_collect_sources() is True

    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    assert runtime_role.runtime_role() == runtime_role.LOCAL_COLLECTOR
    assert runtime_role.is_local_collector() is True
    assert runtime_role.can_collect_sources() is True

    monkeypatch.setenv("G2B_RUNTIME_ROLE", "RESULT_SERVER")
    assert runtime_role.is_result_server() is True
    assert runtime_role.can_collect_sources() is False



def test_production_serving_path_never_uses_postgresql_locator(monkeypatch):
    monkeypatch.delenv("G2B_SERVING_DB_PATH", raising=False)
    monkeypatch.setattr(
        result_snapshot_vnext,
        "current_db_path",
        lambda: "postgresql://configured/g2b_app",
    )

    path = result_snapshot_vnext.serving_db_path()

    assert path == "/app/user_data/g2b-serving.sqlite3"
    assert "postgresql:" not in path


def test_local_sqlite_serving_path_stays_beside_fixture(monkeypatch, tmp_path):
    monkeypatch.delenv("G2B_SERVING_DB_PATH", raising=False)
    local_db = tmp_path / "g2b-vnext.sqlite3"
    monkeypatch.setattr(
        result_snapshot_vnext,
        "current_db_path",
        lambda: str(local_db),
    )

    assert result_snapshot_vnext.serving_db_path() == str(
        tmp_path / "g2b-serving.sqlite3"
    )


def test_configured_serving_path_remains_authoritative(monkeypatch, tmp_path):
    configured = tmp_path / "custom" / "serving.sqlite3"
    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(configured))
    monkeypatch.setattr(
        result_snapshot_vnext,
        "current_db_path",
        lambda: "postgresql://configured/g2b_app",
    )

    assert result_snapshot_vnext.serving_db_path() == str(configured.resolve())


def test_snapshot_availability_does_not_create_missing_storage(monkeypatch, tmp_path):
    serving = tmp_path / "nested" / "serving.sqlite3"
    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(serving))

    assert serving.exists() is False
    assert serving.parent.exists() is False
    assert result_snapshot_vnext.active_snapshot_id() == ""
    assert result_snapshot_vnext.snapshot_available() is False
    assert serving.exists() is False
    assert serving.parent.exists() is False


def test_snapshot_availability_reads_existing_file_without_mutation(monkeypatch, tmp_path):
    serving = tmp_path / "serving.sqlite3"
    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(serving))
    result_snapshot_vnext.import_snapshot(_snapshot("READONLY"))

    before = serving.stat().st_mtime_ns
    assert result_snapshot_vnext.active_snapshot_id() == "READONLY"
    assert result_snapshot_vnext.snapshot_available() is True
    after = serving.stat().st_mtime_ns
    assert after == before

def test_query_rows_applies_shopping_date_window_before_limit(monkeypatch, tmp_path):
    serving = tmp_path / "serving.sqlite3"
    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(serving))
    result_snapshot_vnext.import_snapshot(
        _snapshot(
            "DATE-WINDOW",
            shopping=[
                {
                    "source_key": "OUTSIDE-LATE",
                    "source_date": "2026-10-05",
                    "demand_org": "기관A",
                    "primary_category": "LIGHTING",
                    "item_name": "LED 조명",
                    "amount": 9999,
                },
                {
                    "source_key": "INSIDE-HIGH",
                    "source_date": "2026-09-20",
                    "demand_org": "기관B",
                    "primary_category": "LIGHTING",
                    "item_name": "LED 조명",
                    "amount": 5000,
                },
                {
                    "source_key": "INSIDE-LOW",
                    "source_date": "2026-09-10",
                    "demand_org": "기관C",
                    "primary_category": "LIGHTING",
                    "item_name": "LED 조명",
                    "amount": 1000,
                },
            ],
        )
    )

    rows = result_snapshot_vnext.query_rows(
        "shopping",
        categories=("LIGHTING",),
        start_date="2026-09-01",
        end_date="2026-09-30",
        limit=1,
    )

    assert [row["source_key"] for row in rows] == ["INSIDE-HIGH"]


def test_import_snapshot_does_not_duplicate_full_payload():
    import inspect

    source = inspect.getsource(result_snapshot_vnext.import_snapshot)

    assert "safe = _json_safe(payload)" not in source
    assert "safe = payload" in source


def test_compact_snapshot_import_query_and_replace(monkeypatch, tmp_path):
    serving = tmp_path / "serving.sqlite3"
    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(serving))

    first = _snapshot(
        "S1",
        shopping=[
            {
                "source_key": "A",
                "source_date": "2026-09-01",
                "demand_org": "수원시",
                "primary_category": "LIGHTING",
                "item_name": "LED 가로등",
                "amount": 1000,
            },
            {
                "source_key": "B",
                "source_date": "2026-09-02",
                "demand_org": "서울시",
                "primary_category": "OTHER",
                "item_name": "복사용지",
                "amount": 200,
            },
        ],
        vendors=[{
            "vendor_name": "테스트조명",
            "vendor_bizno": "1234567890",
            "total_amount": 1000,
        }],
    )
    manifest = result_snapshot_vnext.import_snapshot(first)
    assert manifest["snapshot_id"] == "S1"
    assert manifest["total_rows"] == 3

    rows = result_snapshot_vnext.query_rows(
        "shopping", query="수원", categories=("LIGHTING",), limit=20
    )
    assert [row["source_key"] for row in rows] == ["A"]

    meta = result_snapshot_vnext.snapshot_metadata()
    assert meta["manifest"]["snapshot_id"] == "S1"
    assert meta["source_counts"]["raw"]["shopping_delivery"] == 2
    assert meta["source_counts"]["history"]["shopping_delivery"] == 1234
    assert meta["source_counts"]["inactive"]["shopping_delivery"] == 1232

    second = _snapshot(
        "S2",
        shopping=[{
            "source_key": "C",
            "source_date": "2026-09-03",
            "demand_org": "인천시",
            "primary_category": "LIGHTING",
            "item_name": "LED 보안등",
            "amount": 3000,
        }],
    )
    result_snapshot_vnext.import_snapshot(second)
    assert result_snapshot_vnext.active_snapshot_id() == "S2"
    assert [row["source_key"] for row in result_snapshot_vnext.query_rows("shopping")] == ["C"]

    with result_snapshot_vnext._connect() as conn:
        old = conn.execute(
            "SELECT COUNT(*) FROM serving_rows WHERE snapshot_id='S1'"
        ).fetchone()[0]
    assert old == 0


def test_result_server_compaction_is_noop_on_postgresql(monkeypatch):
    monkeypatch.setattr(
        result_server_maintenance,
        "backend_name",
        lambda: "POSTGRESQL",
    )
    monkeypatch.setattr(
        result_server_maintenance.result_snapshot_vnext,
        "snapshot_available",
        lambda: (_ for _ in ()).throw(
            AssertionError("PostgreSQL no-op must not require serving snapshot")
        ),
    )
    monkeypatch.setattr(
        result_server_maintenance,
        "current_db_path",
        lambda: (_ for _ in ()).throw(
            AssertionError("PostgreSQL no-op must not resolve SQLite path")
        ),
    )
    monkeypatch.setattr(
        result_server_maintenance,
        "connect",
        lambda: (_ for _ in ()).throw(
            AssertionError("PostgreSQL no-op must not query sqlite_master")
        ),
    )
    monkeypatch.setattr(
        result_server_maintenance.sqlite3,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("PostgreSQL no-op must not VACUUM SQLite")
        ),
    )

    result = result_server_maintenance.compact_result_server_source_data()

    assert result["status"] == "SKIPPED_POSTGRESQL"
    assert result["storage_backend"] == "POSTGRESQL"
    assert result["dropped_tables"] == []
    assert result["vacuumed"] is False
    assert result["bytes_before"] == 0
    assert result["bytes_after"] == 0


def test_result_server_compaction_keeps_admin_credentials_and_snapshot(monkeypatch, tmp_path):
    serving = tmp_path / "serving.sqlite3"
    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(serving))

    vnext_clean_db.ensure_clean_schema()
    vnext_clean_db.create_admin("admin1", "AdminPassword123!")
    db.set_source_credential("result_sync_token", "x" * 48)
    vnext_store.preserve_raw(
        "shopping_delivery",
        "SOURCE-A",
        {"dlvrReqNo": "REQ", "dlvrReqChgOrd": "0", "prdctSno": "1"},
        source_system="G2B",
    )
    result_snapshot_vnext.import_snapshot(_snapshot("SAFE"))

    result = result_server_maintenance.compact_result_server_source_data()
    assert "raw_records" in result["dropped_tables"]
    assert result_snapshot_vnext.active_snapshot_id() == "SAFE"

    with db.connect() as conn:
        tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        user_count = conn.execute("SELECT COUNT(*) FROM vnext_users").fetchone()[0]
        token = conn.execute(
            "SELECT value FROM vnext_source_credentials WHERE name='result_sync_token'"
        ).fetchone()["value"]
    assert "raw_records" not in tables
    assert user_count == 1
    assert token == "x" * 48


def test_result_server_compaction_requires_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_SERVING_DB_PATH", str(tmp_path / "empty-serving.sqlite3"))
    try:
        result_server_maintenance.compact_result_server_source_data()
    except RuntimeError as exc:
        assert str(exc) == "RESULT_SNAPSHOT_REQUIRED_BEFORE_COMPACTION"
    else:
        raise AssertionError("compaction must require a result snapshot")



def test_local_snapshot_budget_counts_follow_postgres_current_state(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'snapshot-budget.sqlite3'}",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()

    active_payload = {
        "fyr": "2026",
        "exe_ymd": "20261001",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "laf_hg_nm": "수원시",
        "dept_cd": "D1",
        "dbiz_cd": "ACTIVE",
        "dbiz_nm": "LED 가로등 교체",
        "acnt_dv_cd": "A1",
        "bdg_cash_amt": "1000",
        "ep_amt": "100",
    }

    try:
        budget_pg_store.preserve_observation(
            "budget",
            "active-budget",
            active_payload,
            source_system="지방재정365",
            source_operation="QWGJK_FULL_V2_SNAPSHOT",
            source_date="2026-10-01",
        )
        organized = budget_reorganize_vnext.reorganize_existing_budget_raw(
            fiscal_year=2026
        )
        assert organized["complete"] is True

        # Simulate an old pre-4.x SQLite budget remnant. Snapshot metadata must not
        # count it once PostgreSQL is the selected budget source of truth.
        legacy_sha = vnext_store.preserve_raw(
            "budget",
            "legacy-stale",
            {
                **active_payload,
                "dbiz_cd": "LEGACY",
                "dbiz_nm": "LED 보안등 과거 잔여",
            },
            source_system="legacy",
            source_operation="legacy",
            source_date="2026-09-01",
        )
        vnext_store.save_classification(
            "budget",
            "legacy-stale",
            "LIGHTING",
            classifier_version=CLASSIFIER_VERSION,
            source_payload_sha256=legacy_sha,
        )

        snapshot = result_snapshot_vnext.build_local_snapshot()

        assert snapshot["source_counts"]["raw"]["budget"] == 1
        assert snapshot["source_counts"]["target"]["budget"] == 1
        assert {
            row["raw_source_key"]
            for row in snapshot["sections"]["budget_targets"]
        } == {"active-budget"}
        assert {
            row["raw_source_key"]
            for row in snapshot["sections"]["budget_prebid"]
        } == {"active-budget"}
    finally:
        budget_pg_store.reset_engine_cache()
