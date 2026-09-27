import ast
import importlib
from pathlib import Path

import db


def _reload_clean_modules():
    import vnext_clean_db
    import vnext_clean_app
    importlib.reload(vnext_clean_db)
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.initialize_backend() is True
    return vnext_clean_db, vnext_clean_app


def test_main_entrypoint_has_safe_bootstrap_fallback():
    text = Path("main.py").read_text(encoding="utf-8")
    assert "vnext_clean_app" in text
    assert "build_runtime" in text
    assert "G2B_VNEXT_IMPORT_FAILURE" in text
    assert "sinsung_" not in text
    assert "scheduler" not in text

    import main

    def broken_import(_name):
        raise RuntimeError("synthetic import failure")

    fallback, error = main.build_runtime(broken_import)
    assert "RuntimeError" in error
    paths = {route.path for route in fallback.routes}
    assert {"/", "/live", "/health", "/__ai_space_health", "/ready"} <= paths


def test_clean_app_exposes_only_new_runtime_routes():
    _db, clean = _reload_clean_modules()
    paths = {route.path for route in clean.app.routes}
    expected = {
        "/", "/health", "/__ai_space_health", "/live", "/ready",
        "/setup", "/login", "/logout",
        "/dashboard", "/collection-monitor", "/shopping", "/service", "/vendors",
        "/budget", "/raw", "/settings",
        "/organize/budget", "/organize/service",
        "/api/status", "/api/collection-status", "/api/shopping", "/api/vendors",
        "/api/budget", "/api/service",
    }
    assert expected <= paths
    assert "/goods" not in paths
    assert "/api/goods" not in paths
    legacy = {
        "/g2b/shopping/prdct_detail.php", "/vendor", "/org",
        "/market", "/ranking", "/sales", "/products", "/bids", "/budgets",
        "/annual", "/category", "/admin/users",
    }
    assert not (legacy & paths)


def test_first_admin_can_be_created_directly_without_setup_token():
    clean_db, _clean = _reload_clean_modules()

    assert clean_db.users_empty() is True
    clean_db.create_admin("admin1", "AdminPassword123!")
    assert clean_db.users_empty() is False
    assert clean_db.authenticate("admin1", "AdminPassword123!") == {
        "username": "admin1",
        "role": "admin",
    }

    import pytest
    with pytest.raises(ValueError, match="최초 관리자가 이미 생성"):
        clean_db.create_admin("admin2", "SecondAdminPassword123!")


def test_clean_health_and_auth_round_trip():
    clean_db, clean = _reload_clean_modules()
    assert clean_db.users_empty()
    clean_db.create_admin("admin1", "AdminPassword123!")
    assert not clean_db.users_empty()
    assert clean_db.authenticate("admin1", "wrong") is None
    user = clean_db.authenticate("admin1", "AdminPassword123!")
    assert user == {"username": "admin1", "role": "admin"}

    token = clean_db.create_session("admin1")
    assert clean_db.session_user(token) == user
    clean_db.delete_session(token)
    assert clean_db.session_user(token) is None

    health = clean.health()
    assert health["status"] == "ok"
    assert health["process_alive"] is True
    assert health["backend_ok"] is True
    assert health["runtime"] == "G2B_VNEXT_CLEAN"
    assert "raw_rows" not in health
    assert health["db_path"] == db.current_db_path()



def test_backend_initialization_failure_is_fail_soft(monkeypatch):
    _db, clean = _reload_clean_modules()

    def broken_storage():
        raise RuntimeError("synthetic storage unavailable")

    monkeypatch.setattr(clean, "ensure_clean_schema", broken_storage)
    clean._BACKEND_STATE.update(
        initialized=False,
        initializing=False,
        backend_ok=False,
        backend_error="",
        attempts=0,
    )

    assert clean.initialize_backend(force=True) is False
    status = clean.health()
    assert status["status"] == "ok"
    assert status["process_alive"] is True
    assert status["backend_ok"] is False
    assert "raw_rows" not in status
    assert status["required_boot_env"] == []
    assert "RuntimeError" in status["backend_error"]

    live = clean.live()
    assert live["status"] == "ok"
    assert live["process_alive"] is True

    ready = clean.ready()
    assert ready.status_code == 503

def test_clean_schema_is_non_destructive_and_cleanup_is_explicit():
    clean_db, _clean = _reload_clean_modules()

    with db.connect() as conn:
        for table in clean_db.LEGACY_TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table}(id INTEGER)")
            conn.execute(f"INSERT INTO {table}(id) VALUES(1)")
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('auto_sync_enabled','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )

    clean_db.ensure_clean_schema()
    assert clean_db.legacy_tables_absent() is False

    clean_db.cleanup_legacy_tables()
    assert clean_db.legacy_tables_absent() is True
    with db.connect() as conn:
        settings = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key,value FROM app_settings").fetchall()
        }
    assert "auto_sync_enabled" not in settings
    assert settings["vnext_legacy_cleanup_complete"] == "1"


def test_runtime_port_resolution_is_platform_independent(monkeypatch):
    import run

    assert run.resolve_port("9123") == 9123
    assert run.resolve_port("not-a-number") == 8000
    assert run.resolve_port("70000") == 8000
    monkeypatch.setenv("PORT", "8765")
    assert run.resolve_port() == 8765


def test_operational_layout_exposes_version_and_username_limiter_is_account_bound():
    _db, clean = _reload_clean_modules()
    body = clean.layout("운영", "<p>ok</p>", user={"username": "admin1"}).body.decode("utf-8")
    assert clean.APP_VERSION in body
    assert "G2B vNext " + clean.APP_VERSION in body

    clean._LOGIN_FAILURES.clear()
    first = ("ip:203.0.113.10", "user:admin1")
    spoofed_ip_same_user = ("ip:203.0.113.99", "user:admin1")
    assert clean._login_allowed(first) is True
    for _ in range(clean.LOGIN_MAX_FAILURES):
        clean._login_failed(first)
    assert clean._login_allowed(first) is False
    # Changing/spoofing an IP cannot bypass the account-side limiter.
    assert clean._login_allowed(spoofed_ip_same_user) is False

    clean._login_success(first)
    assert clean._login_allowed(first) is True


def test_public_error_is_minimal_outside_test_mode(monkeypatch):
    _db, clean = _reload_clean_modules()
    monkeypatch.setattr(clean, "TEST_MODE", False)
    assert clean._public_error("OperationalError: /secret/path/file.sqlite3") == "OperationalError"
    monkeypatch.setattr(clean, "TEST_MODE", True)
    assert "/secret/path/file.sqlite3" in clean._public_error(
        "OperationalError: /secret/path/file.sqlite3"
    )
