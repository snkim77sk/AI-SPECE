"""4.1.241 dashboard auto-fetch must not trigger full DB readiness or DDL."""
import inspect

from starlette.requests import Request

import budget_storage
import readiness_vnext
import vnext_clean_app


def _forbidden(name):
    def fail(*args, **kwargs):
        raise AssertionError(name)
    return fail


def _mock_dashboard_counts(monkeypatch):
    monkeypatch.setattr(vnext_clean_app, "is_result_server", lambda: False)
    monkeypatch.setattr(vnext_clean_app, "raw_counts", lambda: [
        {
            "dataset": "shopping_delivery",
            "n": 8,
            "history_n": 10,
            "inactive_n": 2,
        },
    ])
    monkeypatch.setattr(
        vnext_clean_app,
        "target_dataset_counts",
        lambda: {"shopping_delivery": 6, "budget": 3},
    )
    monkeypatch.setattr(
        readiness_vnext,
        "build_readiness_report",
        _forbidden("full readiness forbidden on automatic dashboard fetch"),
    )
    monkeypatch.setattr(
        budget_storage,
        "storage_ready",
        _forbidden("schema or index readiness forbidden on automatic dashboard"),
    )


def test_dashboard_auto_fetch_uses_cached_web_readiness(monkeypatch):
    _mock_dashboard_counts(monkeypatch)
    monkeypatch.setattr(
        vnext_clean_app, "backend_status", lambda: {"backend_ok": True},
    )
    calls = []
    def cached_budget(*, probe=True):
        calls.append(probe)
        assert probe is False
        return {
            "required": True,
            "configured": True,
            "ready": True,
            "error_code": "",
        }
    monkeypatch.setattr(
        vnext_clean_app, "_budget_postgres_readiness", cached_budget,
    )

    result = vnext_clean_app._dashboard_summary_payload()

    assert result["ok"] is True
    assert result["total"] == 8
    assert result["target_shopping"] == 6
    assert result["target_budget"] == 3
    assert result["readiness_status"] == "WEB_RUNNING_DETAILED_READY_UNVERIFIED"
    assert result["readiness_scope"] == "WEB_CACHED_HINT_EXPLICIT_READY_REQUIRED"
    assert calls == [False]


def test_dashboard_unready_backend_skips_all_database_probes(monkeypatch):
    _mock_dashboard_counts(monkeypatch)
    monkeypatch.setattr(
        vnext_clean_app, "backend_status", lambda: {"backend_ok": False},
    )
    monkeypatch.setattr(
        vnext_clean_app,
        "_budget_postgres_readiness",
        _forbidden("backend not ready; no PG probe"),
    )
    result = vnext_clean_app._dashboard_summary_payload()
    assert result["readiness_status"] == "WEB_BACKEND_WAITING"


def test_dashboard_postgres_waiting_never_claims_operational_ready(monkeypatch):
    _mock_dashboard_counts(monkeypatch)
    monkeypatch.setattr(
        vnext_clean_app, "backend_status", lambda: {"backend_ok": True},
    )
    monkeypatch.setattr(
        vnext_clean_app,
        "_budget_postgres_readiness",
        lambda *, probe=True: {
            "required": True, "configured": True, "ready": False,
        } if not probe else _forbidden("live PG probe")(),
    )
    result = vnext_clean_app._dashboard_summary_payload()
    assert result["readiness_status"] == "BUDGET_POSTGRES_CHECK_PENDING"
    assert "OPERATIONAL_READY" not in str(result)


def test_dashboard_authenticated_response_logs_safe_latency(monkeypatch, capsys):
    _mock_dashboard_counts(monkeypatch)
    monkeypatch.setattr(
        vnext_clean_app,
        "backend_status",
        lambda: {"backend_ok": True},
    )
    monkeypatch.setattr(
        vnext_clean_app,
        "_budget_postgres_readiness",
        lambda *, probe=True: {
            "required": False, "configured": False, "ready": True,
        },
    )
    monkeypatch.setattr(
        vnext_clean_app, "require_user",
        lambda _request: {"username": "admin"},
    )
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/dashboard-summary",
        "query_string": b"",
        "headers": [],
    })
    response = vnext_clean_app.api_dashboard_summary(request)
    assert response.status_code == 200
    logged = capsys.readouterr().out
    assert "G2B_DASHBOARD_SUMMARY_MS" in logged
    assert "admin" not in logged
    src = inspect.getsource(vnext_clean_app._dashboard_snapshot)
    assert "readiness_vnext.build_readiness_report()" not in (
        src.replace("# Never run readiness_vnext.build_readiness_report() here:", "")
    )
