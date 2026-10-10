"""4.1.238: setting screen first paint stays independent of full storage readiness."""

from contextlib import contextmanager
import inspect

from starlette.requests import Request

import budget_storage
import readiness_vnext
import vnext_clean_app


def _request():
    return Request({
        "type": "http", "method": "GET", "path": "/settings",
        "query_string": b"", "headers": [],
        "scheme": "https", "server": ("test.local", 443),
    })


def _forbidden(message):
    def fail(*args, **kwargs):
        raise AssertionError(message)
    return fail


def test_settings_html_fast_without_readiness_schema_probe_or_sync_token(monkeypatch, capsys):
    app = vnext_clean_app
    import lofin_vnext_http

    monkeypatch.setattr(
        app, "require_user",
        lambda request: {"username": "operator", "role": "admin"},
    )
    monkeypatch.setattr(app, "backend_status", lambda: {
        "backend_ok": True,
        "fresh_start_marker_ok": True,
        "fresh_start_marker_value": "NORMALIZED_NO_RAW_V1",
    })
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "is_result_server", lambda: False)
    monkeypatch.setattr(app, "runtime_role", lambda: "UNIFIED")
    monkeypatch.setattr(app, "db_is_persistent", lambda: True)
    monkeypatch.setattr(app.result_snapshot_vnext, "snapshot_available", lambda: False)
    monkeypatch.setattr(
        app, "_budget_postgres_readiness",
        lambda *, probe=True: {
            "required": True, "configured": True, "ready": True,
            "error_code": "", "database_source": "DB_*",
        } if probe is False else _forbidden("db probe forbidden")(),
    )
    monkeypatch.setattr(
        readiness_vnext, "build_readiness_report",
        _forbidden("heavy readiness forbidden on /settings"),
    )
    monkeypatch.setattr(
        budget_storage, "storage_ready",
        _forbidden("DDL/index inspection forbidden on /settings"),
    )
    monkeypatch.setattr(
        app, "get_result_sync_token",
        _forbidden("result server token not needed in unified mode"),
    )
    monkeypatch.setattr(
        app, "get_setting",
        _forbidden("single-key connection metadata queries forbidden"),
    )
    monkeypatch.setattr(app, "get_service_key", lambda default="": "test-secret-g2b")
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "test-secret-lofin")
    monkeypatch.setattr(app, "source_credential_configured", lambda _: True)
    monkeypatch.setattr(app, "_settings_connection_snapshot", lambda: {
        "g2b_api_connection_status": "OK",
        "g2b_api_connection_fingerprint": app._credential_fingerprint("test-secret-g2b"),
        "lofin_api_connection_status": "FAILED",
        "lofin_api_connection_code": "SOURCE_RESPONSE_FAILED",
        "lofin_api_connection_fingerprint": app._credential_fingerprint("test-secret-lofin"),
    })

    response = app.settings_page(_request())
    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert 'action="/settings/keys"' in body
    assert 'action="/settings/probe-source"' in body
    assert 'action="/settings/result-sync-token"' not in body
    assert "PostgreSQL·전체 준비상태 상세 확인" in body
    assert 'href="/ready"' in body
    assert "API 키 설정" in body
    assert "저장정책" in body
    assert "연결확인" in body
    assert "test-secret-g2b" not in body
    assert "test-secret-lofin" not in body
    assert "G2B_SETTINGS_RENDER_MS" in capsys.readouterr().out


def test_cached_settings_probe_skips_postgresql_network_and_ddl(monkeypatch):
    app = vnext_clean_app
    import budget_storage
    import g2b_database

    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(budget_storage, "storage_configured", lambda: True)
    monkeypatch.setattr(
        budget_storage, "storage_ready",
        _forbidden("unexpected live database/schema check"),
    )
    monkeypatch.setattr(
        g2b_database, "database_source_label", lambda: "DB_*",
    )
    state = app._budget_postgres_readiness(probe=False)
    assert state["configured"] is True
    assert state["database_source"] == "DB_*"


def test_saved_source_probe_metadata_uses_one_small_sql_lookup(monkeypatch):
    app = vnext_clean_app
    recorded = []

    class Conn:
        def execute(self, sql, params):
            recorded.append((str(sql), tuple(params)))
            return type("Rows", (), {
                "fetchall": lambda self: [
                    {"key": "g2b_api_connection_status", "value": "OK"},
                    {"key": "lofin_api_connection_at", "value": "2026-10-10"},
                ]
            })()

    @contextmanager
    def connect():
        yield Conn()

    monkeypatch.setattr(app, "connect", connect)
    snapshot = app._settings_connection_snapshot()
    assert snapshot["g2b_api_connection_status"] == "OK"
    assert snapshot["lofin_api_connection_at"] == "2026-10-10"
    assert len(recorded) == 1
    sql, params = recorded[0]
    assert "FROM app_settings" in sql
    assert sql.count("?") == 8
    assert len(params) == 8
    assert "source_credentials" not in sql
    assert "password" not in sql.lower()


def test_source_probe_metadata_query_failure_degrades_not_502(monkeypatch):
    app = vnext_clean_app
    @contextmanager
    def unavailable():
        raise TimeoutError("postgres under load")
        yield

    monkeypatch.setattr(app, "connect", unavailable)
    assert app._settings_connection_snapshot() == {}
    status, _note = app._source_connection_display(
        "g2b", "configured-secret", settings={}
    )
    assert status == "저장됨"


def test_source_connection_display_preserves_old_individual_lookup(monkeypatch):
    app = vnext_clean_app
    secret = "test-legacy-key"
    settings = {
        "g2b_api_connection_status": "OK",
        "g2b_api_connection_fingerprint": app._credential_fingerprint(secret),
        "g2b_api_connection_at": "2026-10-10",
    }
    monkeypatch.setattr(app, "get_setting", lambda name, default="": settings.get(name, default))
    state, detail = app._source_connection_display("g2b", secret)
    assert state == "연결확인"
    assert "2026-10-10" in detail


def test_settings_page_has_no_synced_report_or_db_migrations():
    source = inspect.getsource(vnext_clean_app.settings_page)
    assert "readiness_vnext.build_readiness_report()" not in source
    assert "budget_storage.storage_ready()" not in source
    assert "_budget_postgres_readiness(probe=False)" in source
    assert "_settings_connection_snapshot()" in source
    assert "G2B_SETTINGS_RENDER_MS" in source


def test_settings_role_does_not_read_compat_token_on_unified():
    source = inspect.getsource(vnext_clean_app.settings_page)
    assert 'if is_result_server() else False' in source
    assert "sync_token_ready" in source
