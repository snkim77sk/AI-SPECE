import ast
import importlib
from pathlib import Path

import db


def _reload_clean_modules():
    import vnext_clean_db
    import vnext_clean_app
    importlib.reload(vnext_clean_db)
    importlib.reload(vnext_clean_app)
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
        "/", "/health", "/__ai_space_health", "/setup", "/login", "/logout",
        "/dashboard", "/budget", "/service", "/raw", "/settings",
        "/organize/budget", "/organize/service", "/api/status", "/api/budget",
        "/api/service",
    }
    assert expected <= paths
    legacy = {
        "/g2b/shopping/prdct_detail.php", "/vendors", "/vendor", "/org",
        "/market", "/ranking", "/sales", "/products", "/bids", "/budgets",
        "/annual", "/category", "/admin/users",
    }
    assert not (legacy & paths)


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


def test_clean_schema_can_remove_legacy_serving_tables():
    clean_db, _clean = _reload_clean_modules()

    # conftest creates the historical schema for broad regression compatibility.
    before = clean_db.legacy_table_status()
    assert before["shopping_contracts"]["exists"] is True
    assert before["bids"]["exists"] is True
    assert before["budget_items"]["exists"] is True

    clean_db.drop_legacy_tables()

    after = clean_db.legacy_table_status()
    assert all(not value["exists"] for value in after.values())
    with db.connect() as conn:
        # vNext foundation and new auth survive destructive legacy cleanup.
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "raw_records" in names
    assert "raw_record_revisions" in names
    assert "vnext_users" in names
    assert "vnext_sessions" in names
    assert "app_settings" in names
