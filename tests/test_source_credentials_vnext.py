import os
from pathlib import Path
import stat

import pytest

import db
import lofin_vnext_http


def test_admin_saved_source_credentials_are_used_without_environment(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "")
    monkeypatch.setenv("LOFIN_API_KEY", "")

    db.set_source_credential("g2b_service_key", "saved-g2b-key")
    db.set_source_credential("lofin_api_key", "saved-lofin-key")
    db.set_source_credential("eduinfo_api_key", "saved-eduinfo-key")

    assert db.get_service_key("") == "saved-g2b-key"
    assert lofin_vnext_http.get_lofin_key() == "saved-lofin-key"
    assert db.get_setting("eduinfo_api_key", "") == "saved-eduinfo-key"

    # Source secrets live in a dedicated credential table and are never returned
    # by the general app_settings snapshot.
    settings = db.settings_dict()
    assert "saved-g2b-key" not in settings.values()
    assert "saved-lofin-key" not in settings.values()
    assert "saved-eduinfo-key" not in settings.values()


def test_environment_credentials_override_admin_saved_values(monkeypatch):
    db.set_source_credential("g2b_service_key", "saved-g2b-key")
    db.set_source_credential("lofin_api_key", "saved-lofin-key")
    db.set_source_credential("eduinfo_api_key", "saved-eduinfo-key")

    monkeypatch.setenv("G2B_SERVICE_KEY", "env-g2b-key")
    monkeypatch.setenv("LOFIN_API_KEY", "env-lofin-key")
    monkeypatch.setenv("EDUINFO_API_KEY", "env-eduinfo-key")

    assert db.get_service_key("") == "env-g2b-key"
    assert lofin_vnext_http.get_lofin_key() == "env-lofin-key"
    assert db.get_setting("eduinfo_api_key", "") == "env-eduinfo-key"


def test_admin_can_replace_and_clear_saved_credentials(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "")
    monkeypatch.setenv("LOFIN_API_KEY", "")

    db.set_source_credential("g2b_service_key", "first")
    db.set_source_credential("g2b_service_key", "second")
    assert db.get_service_key("") == "second"

    db.set_source_credential("g2b_service_key", "")
    assert db.get_service_key("") == ""

    db.set_source_credential("eduinfo_api_key", "education-key")
    assert db.get_setting("eduinfo_api_key", "") == "education-key"
    db.set_source_credential("eduinfo_api_key", "")
    assert db.get_setting("eduinfo_api_key", "") == ""

    with pytest.raises(ValueError, match="unsupported"):
        db.set_source_credential("unknown_api_key", "not-enabled")


def test_sqlite_file_permissions_are_owner_only_on_posix():
    if os.name == "nt":
        pytest.skip("POSIX permission bits are not available")
    db.init_db()
    mode = stat.S_IMODE(os.stat(db.current_db_path()).st_mode)
    assert mode & 0o077 == 0


def test_g2b_portal_encoding_key_is_normalized_like_no1(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "")
    db.set_source_credential("g2b_service_key", "abc%2Bdef%2Fghi%3D")
    assert db.get_service_key("") == "abc+def/ghi="

    monkeypatch.setenv("G2B_SERVICE_KEY", "env%2Bkey%2Fvalue%3D")
    assert db.get_service_key("") == "env+key/value="


def test_readonly_credential_presence_check_does_not_reinitialize_schema(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "")
    db.set_source_credential("g2b_service_key", "saved-g2b-key")

    def forbidden_init():
        raise AssertionError("READ_PATH_MUST_NOT_INIT_SCHEMA")

    monkeypatch.setattr(db, "init_db", forbidden_init)
    assert db.source_credential_configured("g2b_service_key") is True
    assert db.source_credential_configured("unknown") is False


def test_readonly_credential_presence_honors_environment_without_db(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "env-key")

    def forbidden_connect():
        raise AssertionError("ENV_CREDENTIAL_SHOULD_NOT_TOUCH_DB")

    monkeypatch.setattr(db, "connect", forbidden_connect)
    assert db.source_credential_configured("g2b_service_key") is True


def test_production_runtime_setting_io_does_not_repeat_schema_ddl():
    source = Path("db.py").read_text(encoding="utf-8")

    helper = source.split("def _ensure_runtime_settings_storage", 1)[1].split(
        "def _get_db_setting", 1
    )[0]
    assert "if _use_sqlite():" in helper
    assert "init_db()" in helper

    for name, next_name in (
        ("_get_db_setting", "_SOURCE_CREDENTIAL_NAMES"),
        ("_get_source_credential", "set_source_credential"),
        ("set_source_credential", "_normalize_g2b_service_key"),
        ("set_setting", "settings_dict"),
    ):
        block = source.split(f"def {name}", 1)[1].split(next_name, 1)[0]
        assert "_ensure_runtime_settings_storage()" in block
        assert "\n    init_db()" not in block
