from pathlib import Path

import vnext_clean_app
import g2b_database


ROOT = Path(__file__).resolve().parents[1]


def test_cafe24_environment_contract_is_documented_and_live():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    source = (ROOT / "vnext_clean_app.py").read_text(encoding="utf-8")
    database = (ROOT / "g2b_database.py").read_text(encoding="utf-8")
    pg = (ROOT / "budget_pg_store.py").read_text(encoding="utf-8")

    required_docs = {
        "G2B_TEST_MODE",
        "G2B_DATABASE_URL",
        "G2B_AUTO_SYNC",
        "G2B_APP_SCHEMA",
        "G2B_BUDGET_SCHEMA",
        "G2B_DB_POOL_SIZE",
        "G2B_DB_MAX_OVERFLOW",
        "G2B_DB_POOL_TIMEOUT_SECONDS",
        "G2B_DB_POOL_RECYCLE_SECONDS",
        "G2B_DB_CONNECT_TIMEOUT_SECONDS",
        "G2B_DB_LOCK_TIMEOUT_MS",
        "G2B_DB_STATEMENT_TIMEOUT_MS",
        "G2B_BUDGET_RETENTION_BATCH_SIZE",
        "G2B_BUDGET_RETENTION_DAYS",
        "G2B_BUDGET_RECEIPT_RETENTION_DAYS",
        "G2B_SHOPPING_SYNC_INTERVAL_SECONDS",
        "G2B_SHOPPING_SYNC_DAYS_PER_RUN",
        "G2B_BUDGET_SYNC_MAX_PAGES",
        "G2B_BUDGET_SYNC_MAX_REQUESTS",
        "G2B_FUTURE_BUDGET_SYNC_MAX_PAGES",
        "G2B_OPERATIONAL_LEASE_RETRY_SECONDS",
    }
    for name in sorted(required_docs):
        assert name in readme, name

    for name in {
        "G2B_SHOPPING_SYNC_INTERVAL_SECONDS",
        "G2B_SHOPPING_SYNC_DAYS_PER_RUN",
        "G2B_BUDGET_SYNC_MAX_PAGES",
        "G2B_BUDGET_SYNC_MAX_REQUESTS",
        "G2B_FUTURE_BUDGET_SYNC_MAX_PAGES",
        "G2B_OPERATIONAL_LEASE_RETRY_SECONDS",
    }:
        assert name in source, name

    for name in {
        "G2B_DATABASE_URL",
        "G2B_APP_SCHEMA",
        "G2B_BUDGET_SCHEMA",
        "G2B_DB_POOL_SIZE",
        "G2B_DB_MAX_OVERFLOW",
        "G2B_DB_POOL_TIMEOUT_SECONDS",
        "G2B_DB_POOL_RECYCLE_SECONDS",
        "G2B_DB_CONNECT_TIMEOUT_SECONDS",
        "G2B_DB_LOCK_TIMEOUT_MS",
        "G2B_DB_STATEMENT_TIMEOUT_MS",
    }:
        assert name in database, name

    for name in {
        "G2B_BUDGET_RETENTION_BATCH_SIZE",
        "G2B_BUDGET_RETENTION_DAYS",
        "G2B_BUDGET_RECEIPT_RETENTION_DAYS",
    }:
        assert name in (readme + pg), name

def test_removed_unused_shopping_lookback_setting_does_not_return():
    assert "G2B_SHOPPING_SYNC_LOOKBACK_DAYS" not in (
        ROOT / "vnext_clean_app.py"
    ).read_text(encoding="utf-8")
    assert not hasattr(vnext_clean_app, "SHOPPING_SYNC_LOOKBACK_DAYS")


def test_production_schema_layout_rejects_collisions(monkeypatch):
    monkeypatch.setenv("G2B_APP_SCHEMA", "g2b_app")
    monkeypatch.setenv("G2B_BUDGET_SCHEMA", "g2b_app")
    import pytest
    with pytest.raises(RuntimeError, match="G2B_SCHEMA_LAYOUT_INVALID"):
        g2b_database.validate_schema_layout()

    monkeypatch.setenv("G2B_APP_SCHEMA", "g2b_meta")
    monkeypatch.setenv("G2B_BUDGET_SCHEMA", "g2b_budget")
    with pytest.raises(RuntimeError, match="G2B_SCHEMA_LAYOUT_INVALID"):
        g2b_database.validate_schema_layout()

    monkeypatch.setenv("G2B_APP_SCHEMA", "g2b_app")
    monkeypatch.setenv("G2B_BUDGET_SCHEMA", "g2b_meta")
    with pytest.raises(RuntimeError, match="G2B_SCHEMA_LAYOUT_INVALID"):
        g2b_database.validate_schema_layout()


def test_default_production_schema_layout_is_distinct(monkeypatch):
    monkeypatch.delenv("G2B_APP_SCHEMA", raising=False)
    monkeypatch.delenv("G2B_BUDGET_SCHEMA", raising=False)
    assert g2b_database.validate_schema_layout() == (
        "g2b_app",
        "g2b_budget",
    )
