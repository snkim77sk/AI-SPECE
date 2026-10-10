"""G2B 4.1.243: one source-credential lookup and 502-safe settings UI."""
from contextlib import contextmanager

from starlette.requests import Request

import db
import vnext_clean_app


def _request():
    return Request({
        "type": "http", "method": "GET", "path": "/settings",
        "query_string": b"", "headers": [],
    })


def test_settings_credential_snapshot_batches_missing_environment_keys(monkeypatch):
    for env in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"):
        monkeypatch.setenv(env, "")
    monkeypatch.setattr(db, "_ensure_runtime_settings_storage", lambda: None)
    calls = []

    class Conn:
        def execute(self, sql, params):
            calls.append((str(sql), tuple(params)))
            return type("Result", (), {
                "fetchall": lambda _: [
                    {"name": "g2b_service_key", "value": "test%2Bservice"},
                    {"name": "lofin_api_key", "value": "test-lofin"},
                    {"name": "eduinfo_api_key", "value": "test-eduinfo"},
                ]
            })()

    @contextmanager
    def connect():
        yield Conn()

    monkeypatch.setattr(db, "connect", connect)
    result = db.settings_source_credential_snapshot()

    assert result["storage_unavailable"] is False
    assert result["credentials"] == {
        "g2b_service_key": "test+service",
        "lofin_api_key": "test-lofin",
        "eduinfo_api_key": "test-eduinfo",
    }
    assert len(calls) == 1
    sql, params = calls[0]
    assert "FROM vnext_source_credentials" in sql
    assert sql.count("?") == 3
    assert params == ("g2b_service_key", "lofin_api_key", "eduinfo_api_key")


def test_settings_credential_snapshot_environment_precedence_without_db(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "environment%2Bkey")
    monkeypatch.setenv("LOFIN_API_KEY", "env-lofin")
    monkeypatch.setenv("EDUINFO_API_KEY", "env-eduinfo")
    monkeypatch.setattr(
        db,
        "connect",
        lambda: (_ for _ in ()).throw(AssertionError("no DB needed")),
    )
    info = db.settings_source_credential_snapshot()
    assert info["credentials"] == {
        "g2b_service_key": "environment+key",
        "lofin_api_key": "env-lofin",
        "eduinfo_api_key": "env-eduinfo",
    }
    assert info["storage_unavailable"] is False


def test_settings_credential_snapshot_storage_outage_degrades_not_502(monkeypatch):
    for env in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"):
        monkeypatch.setenv(env, "")
    monkeypatch.setattr(db, "_ensure_runtime_settings_storage", lambda: None)

    @contextmanager
    def unavailable():
        raise TimeoutError("PostgreSQL temporarily unavailable")
        yield

    monkeypatch.setattr(db, "connect", unavailable)
    info = db.settings_source_credential_snapshot()
    assert info["storage_unavailable"] is True
    assert info["credentials"] == {
        "g2b_service_key": "", "lofin_api_key": "", "eduinfo_api_key": "",
    }


def test_settings_page_database_key_outage_reports_unknown_not_missing(
    monkeypatch, capsys
):
    app = vnext_clean_app
    monkeypatch.setattr(
        app, "require_user",
        lambda _: {"username": "operator", "role": "admin"},
    )
    monkeypatch.setattr(app, "backend_status", lambda: {
        "backend_ok": True,
        "fresh_start_marker_ok": True,
        "fresh_start_marker_value": "NORMALIZED_NO_RAW_V1",
    })
    monkeypatch.setattr(app, "is_result_server", lambda: False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "runtime_role", lambda: "UNIFIED")
    monkeypatch.setattr(app, "db_is_persistent", lambda: True)
    monkeypatch.setattr(app.result_snapshot_vnext, "snapshot_available", lambda: False)
    monkeypatch.setattr(
        app,
        "_budget_postgres_readiness",
        lambda *, probe=True: {
            "required": True, "configured": True, "ready": False,
            "error_code": "", "database_source": "DB_*",
        },
    )
    monkeypatch.setattr(app, "settings_source_credential_snapshot", lambda: {
        "credentials": {
            "g2b_service_key": "",
            "lofin_api_key": "",
            "eduinfo_api_key": "",
        },
        "storage_unavailable": True,
    })
    monkeypatch.setattr(
        app, "_settings_connection_snapshot",
        lambda: (_ for _ in ()).throw(
            AssertionError("do not retry metadata DB after credential timeout")
        ),
    )

    response = app.settings_page(_request())
    content = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "저장소 응답 지연 · 기존 저장키 확인 대기" in content
    assert "확인 대기" in content
    assert 'action="/settings/keys"' in content
    assert 'href="/ready"' in content
    assert "G2B_SETTINGS_CREDENTIAL_STATUS_DEGRADED" in capsys.readouterr().out
