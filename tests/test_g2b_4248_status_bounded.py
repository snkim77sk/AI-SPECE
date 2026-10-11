"""G2B 4.1.248 /api/status avoids unbounded receipt scans and schema probes."""
from contextlib import contextmanager
import json

from starlette.requests import Request

import budget_collection_status_vnext
import budget_pg_store
import budget_storage
import readiness_vnext
import vnext_clean_app


def _req():
    return Request({
        "type": "http", "method": "GET", "path": "/api/status",
        "query_string": b"", "headers": [],
    })


def test_shopping_web_status_uses_sql_grouped_counts_without_stability_scan(
    monkeypatch,
):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(
        readiness_vnext, "_stability_summary",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("full checkpoint stability scan forbidden")
        ),
    )
    statements = []

    class Rows:
        def __init__(self, sql):
            self.sql = sql
        def fetchone(self):
            return {"n": 3}
        def fetchall(self):
            return [{"status": "COMPLETE", "n": 2}]

    class Conn:
        def execute(self, sql, params=()):
            statements.append(str(sql))
            return Rows(str(sql))

    @contextmanager
    def connect():
        yield Conn()

    monkeypatch.setattr(readiness_vnext, "connect", connect)
    result = readiness_vnext._shopping_storage_readiness(web_fast=True)
    assert result["latest_raw_rows"] == 3
    assert result["checkpoint_status_counts"] == {"COMPLETE": 2}
    assert result["stability_verification_performed"] is False
    assert result["source_collection_completeness_verified"] is False
    assert any("set_config('statement_timeout'" in sql for sql in statements)
    assert not any("SELECT * FROM collection_checkpoints" in sql for sql in statements)


def test_status_web_uses_budget_monitor_counts_without_receipt_fanout(
    monkeypatch,
):
    monkeypatch.setattr(readiness_vnext.budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        readiness_vnext, "_shopping_storage_readiness",
        lambda *, web_fast=False: {
            "readiness_scope": "CURRENT_NORMALIZED_SHOPPING_STORAGE_ONLY",
            "stability_verification_performed": False,
        },
    )
    monkeypatch.setattr(
        budget_storage, "dataset_counts_all",
        lambda datasets: {
            name: {"current_records": 3, "observations": 5}
            for name in datasets
        },
    )
    monkeypatch.setattr(
        budget_pg_store, "current_classified_counts",
        lambda datasets, version: {name: 2 for name in datasets},
    )
    monitor_calls = []
    monkeypatch.setattr(
        budget_collection_status_vnext,
        "budget_collection_monitor_status",
        lambda: (
            monitor_calls.append("bounded")
            or {"datasets": [
                {"dataset": name, "checkpoint_status_counts": {"COMPLETE": 4}}
                for name in sorted(readiness_vnext.BUDGET_RAW_DATASETS)
            ]}
        ),
    )
    monkeypatch.setattr(
        budget_collection_status_vnext,
        "budget_collection_status",
        lambda: (_ for _ in ()).throw(
            AssertionError("unbounded historical receipt audit forbidden")
        ),
    )
    result = readiness_vnext.storage_readiness(web_fast=True)
    assert monitor_calls == ["bounded"]
    for dataset in sorted(readiness_vnext.BUDGET_RAW_DATASETS):
        assert result[dataset]["checkpoint_status_counts"] == {"COMPLETE": 4}
        assert result[dataset]["latest_raw_rows"] == 3


def test_api_status_production_requests_bounded_report(monkeypatch, capsys):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "admin"})
    monkeypatch.setattr(app, "is_result_server", lambda: False)
    observed = []
    monkeypatch.setattr(
        readiness_vnext, "build_readiness_report",
        lambda *, web_fast=False: (
            observed.append(web_fast)
            or {
                "status": "OPERATIONAL_READY",
                "status_scope": "EXECUTION_READINESS_NOT_SOURCE_COMPLETENESS",
                "readiness_detail_level": "BOUNDED_WEB_STATUS",
                "source_collection_completeness_verified": False,
            }
        ),
    )
    payload = app.api_status(_req())
    assert payload["status"] == "OPERATIONAL_READY"
    assert payload["source_collection_completeness_verified"] is False
    assert payload["readiness_detail_level"] == "BOUNDED_WEB_STATUS"
    assert observed == [True]
    assert "G2B_API_STATUS_MS" in capsys.readouterr().out


def test_api_status_does_not_leak_sql_error_or_crash(monkeypatch, capsys):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "admin"})
    monkeypatch.setattr(app, "is_result_server", lambda: False)
    monkeypatch.setattr(
        readiness_vnext, "build_readiness_report",
        lambda *, web_fast=False: (_ for _ in ()).throw(
            TimeoutError("password=never-print")
        ),
    )
    response = app.api_status(_req())
    assert response.status_code == 503
    output = json.loads(response.body)
    assert output["status"] == "TEMPORARILY_UNAVAILABLE"
    assert output["source_collection_completeness_verified"] is False
    assert "password" not in str(output)
    assert "password" not in capsys.readouterr().out


def test_fast_readiness_reports_shopping_failure_not_operational_ready(
    monkeypatch,
):
    monkeypatch.setattr(readiness_vnext, "static_coverage", lambda: {
        "missing_collectors": [], "unexpected_collectors": [],
        "missing_historical": [], "unexpected_historical": [],
        "missing_canary": [], "unexpected_canary": [],
    })
    monkeypatch.setattr(readiness_vnext, "credential_readiness", lambda: {
        "g2b_service_key_configured": True,
        "lofin_api_key_configured": True,
        "eduinfo_api_key_configured": True,
    })
    monkeypatch.setattr(readiness_vnext, "storage_readiness", lambda *, web_fast=False: {
        readiness_vnext.shopping_vnext.DATASET: {"storage_error": "TimeoutError"},
    })
    monkeypatch.setattr(
        budget_storage, "storage_ready",
        lambda *, read_only=False: True,
    )
    monkeypatch.setattr(budget_storage, "storage_error_code", lambda: "")
    monkeypatch.setattr(budget_storage, "backend_name", lambda: "POSTGRESQL")
    report = readiness_vnext.build_readiness_report(web_fast=True)
    assert report["status"] == "SHOPPING_STORAGE_WAITING"
    assert report["shopping_operational_ready"] is False
    assert report["source_collection_completeness_verified"] is False
    assert report["readiness_detail_level"] == "BOUNDED_WEB_STATUS"


def test_explicit_offline_full_report_is_unmodified_default(monkeypatch):
    calls = []
    monkeypatch.setattr(readiness_vnext, "_shopping_storage_readiness",
                        lambda: calls.append("full") or {"readiness_scope": "TEST"})
    monkeypatch.setattr(readiness_vnext.budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "dataset_counts_all",
        lambda datasets: {name: {
            "current_records": 0, "observations": 0,
        } for name in datasets},
    )
    monkeypatch.setattr(budget_pg_store, "current_classified_counts",
                        lambda datasets, version: {name: 0 for name in datasets})
    monkeypatch.setattr(budget_collection_status_vnext, "budget_collection_status",
                        lambda: calls.append("full-receipts") or {"datasets":[]})
    result = readiness_vnext.storage_readiness()
    assert result[readiness_vnext.shopping_vnext.DATASET]["readiness_scope"] == "TEST"
    assert calls == ["full", "full-receipts"]
