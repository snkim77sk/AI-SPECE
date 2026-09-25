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


def test_main_entrypoint_is_clean_vnext_only():
    text = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert imports == ["vnext_clean_app"]
    assert "sinsung_" not in text
    assert "server" not in text
    assert "scheduler" not in text


def test_clean_app_exposes_only_new_runtime_routes():
    _db, clean = _reload_clean_modules()
    paths = {route.path for route in clean.app.routes}
    expected = {
        "/", "/health", "/__ai_space_health", "/live", "/ready",
        "/setup", "/login", "/logout",
        "/dashboard", "/shopping", "/goods", "/service", "/vendors",
        "/budget", "/raw", "/settings",
        "/organize/budget", "/organize/service",
        "/api/status", "/api/shopping", "/api/goods", "/api/vendors",
        "/api/budget", "/api/service",
    }
    assert expected <= paths
    legacy = {
        "/g2b/shopping/prdct_detail.php", "/vendor", "/org",
        "/market", "/ranking", "/sales", "/products", "/bids", "/budgets",
        "/annual", "/category", "/admin/users",
    }
    assert not (legacy & paths)


def test_first_admin_requires_one_time_setup_token(monkeypatch):
    monkeypatch.setenv("G2B_SETUP_TOKEN", "ci-setup-token-1234567890")
    clean_db, _clean = _reload_clean_modules()

    assert clean_db.users_empty() is True
    assert clean_db.setup_token() == "ci-setup-token-1234567890"
    assert clean_db.validate_setup_token("wrong") is False
    assert clean_db.validate_setup_token("ci-setup-token-1234567890") is True

    clean_db.create_admin("admin1", "AdminPassword123!")
    assert clean_db.users_empty() is False
    assert clean_db.setup_token() == ""


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
    assert health["backend_ok"] is True
    assert health["runtime"] == "G2B_VNEXT_CLEAN"
    assert health["raw_rows"] == 0



def test_backend_initialization_failure_is_fail_soft(monkeypatch):
    _db, clean = _reload_clean_modules()

    def broken_storage():
        raise RuntimeError("synthetic storage unavailable")

    monkeypatch.setattr(clean, "ensure_clean_schema", broken_storage)
    clean._BACKEND_STATE.update(
        initialized=False,
        backend_ok=False,
        backend_error="",
        attempts=0,
    )

    assert clean.initialize_backend(force=True) is False
    status = clean.health()
    assert status["status"] == "ok"
    assert status["backend_ok"] is False
    assert status["raw_rows"] == 0
    assert status["required_boot_env"] == []
    assert "RuntimeError" in status["backend_error"]

    live = clean.live()
    assert live["status"] == "ok"
    assert live["process_alive"] is True

    ready = clean.ready()
    assert ready.status_code == 503

def test_clean_schema_purges_legacy_22_tables_and_settings():
    clean_db, _clean = _reload_clean_modules()

    with db.connect() as conn:
        for table in clean_db.LEGACY_TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table}(id INTEGER)")
            conn.execute(f"INSERT INTO {table}(id) VALUES(1)")
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('auto_sync_enabled','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('lofin_api_key','legacy-secret') "
            "ON CONFLICT(key) DO UPDATE SET value='legacy-secret'"
        )

    clean_db.ensure_clean_schema()

    assert clean_db.legacy_tables_absent() is True
    with db.connect() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        settings = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key,value FROM app_settings").fetchall()
        }
    assert "raw_records" in names
    assert "raw_record_revisions" in names
    assert "vnext_users" in names
    assert "vnext_sessions" in names
    assert "app_settings" in names
    assert "auto_sync_enabled" not in settings
    assert "lofin_api_key" not in settings
    assert settings["vnext_legacy_cleanup_complete"] == "1"
