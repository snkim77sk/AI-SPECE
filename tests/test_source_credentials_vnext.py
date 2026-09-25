import pytest

import db
import lofin_vnext_http


def test_admin_saved_source_credentials_are_used_without_environment(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "")
    monkeypatch.setenv("LOFIN_API_KEY", "")

    db.set_source_credential("g2b_service_key", "saved-g2b-key")
    db.set_source_credential("lofin_api_key", "saved-lofin-key")

    assert db.get_service_key("") == "saved-g2b-key"
    assert lofin_vnext_http.get_lofin_key() == "saved-lofin-key"

    # Source secrets live in a dedicated credential table and are never returned
    # by the general app_settings snapshot.
    settings = db.settings_dict()
    assert "saved-g2b-key" not in settings.values()
    assert "saved-lofin-key" not in settings.values()


def test_environment_credentials_override_admin_saved_values(monkeypatch):
    db.set_source_credential("g2b_service_key", "saved-g2b-key")
    db.set_source_credential("lofin_api_key", "saved-lofin-key")

    monkeypatch.setenv("G2B_SERVICE_KEY", "env-g2b-key")
    monkeypatch.setenv("LOFIN_API_KEY", "env-lofin-key")

    assert db.get_service_key("") == "env-g2b-key"
    assert lofin_vnext_http.get_lofin_key() == "env-lofin-key"


def test_admin_can_replace_and_clear_saved_credentials(monkeypatch):
    monkeypatch.setenv("G2B_SERVICE_KEY", "")
    monkeypatch.setenv("LOFIN_API_KEY", "")

    db.set_source_credential("g2b_service_key", "first")
    db.set_source_credential("g2b_service_key", "second")
    assert db.get_service_key("") == "second"

    db.set_source_credential("g2b_service_key", "")
    assert db.get_service_key("") == ""

    with pytest.raises(ValueError, match="unsupported"):
        db.set_source_credential("eduinfo_api_key", "not-enabled")
