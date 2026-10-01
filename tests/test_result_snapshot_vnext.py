import os

import db
import result_server_maintenance
import result_snapshot_vnext
import runtime_role
import vnext_clean_db
import vnext_store


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
            "raw": {"shopping_delivery": 1234},
            "target": {"shopping_delivery": len(shopping or [])},
        },
    }


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
    assert meta["source_counts"]["raw"]["shopping_delivery"] == 1234

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
