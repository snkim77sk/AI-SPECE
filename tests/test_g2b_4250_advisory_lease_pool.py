"""4.1.250 protects the shared 1+1 DB pool and long-lived source leases."""

import inspect

import psycopg

import db
import g2b_database
import vnext_clean_app


def test_advisory_connector_uses_disposable_autocommit_psycopg(monkeypatch):
    captured = {}
    marker = object()
    monkeypatch.setattr(
        g2b_database, "resolve_database_url",
        lambda: "postgresql+psycopg://example:secret@db.example:5432/g2b",
    )

    def connect(dsn, **kwargs):
        captured.update(dsn=dsn, kwargs=kwargs)
        return marker

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setenv("G2B_DB_CONNECT_TIMEOUT_SECONDS", "3")
    assert g2b_database._operational_lease_connection() is marker
    assert captured["dsn"].startswith("postgresql://")
    assert captured["kwargs"]["autocommit"] is True
    assert captured["kwargs"]["connect_timeout"] == 3
    assert captured["kwargs"]["application_name"] == "g2b_v41_operational_lease"


def test_data_pool_and_initial_app_schema_installer_remain_unchanged():
    engine_source = inspect.getsource(g2b_database.engine)
    assert '_env_int("G2B_DB_POOL_SIZE", 1, lower=1, upper=2)' in engine_source
    assert '_env_int("G2B_DB_MAX_OVERFLOW", 1, lower=0, upper=1)' in engine_source
    assert "idle_in_transaction_session_timeout=60000" in engine_source
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in inspect.getsource(db.init_db)
    assert "budget_boot_ok = bool(_boot_budget_storage.storage_ready())" in inspect.getsource(
        vnext_clean_app.initialize_backend
    )


def test_source_lease_does_not_checkout_normal_pool():
    source = inspect.getsource(g2b_database.operational_source_cycle_lease)
    assert "_operational_lease_connection()" in source
    assert "engine()" not in source
    assert "pg_try_advisory_lock_shared" in source
    assert "pg_try_advisory_lock(" in source
