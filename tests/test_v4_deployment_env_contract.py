from pathlib import Path

import vnext_clean_app


ROOT = Path(__file__).resolve().parents[1]


def test_cafe24_environment_contract_is_documented_and_live():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    source = (ROOT / "vnext_clean_app.py").read_text(encoding="utf-8")
    pg = (ROOT / "budget_pg_store.py").read_text(encoding="utf-8")
    db = (ROOT / "db.py").read_text(encoding="utf-8")

    required_docs = {
        "G2B_BUDGET_DATABASE_URL",
        "G2B_AUTO_SYNC",
        "G2B_SHOPPING_SYNC_INTERVAL_SECONDS",
        "G2B_SHOPPING_SYNC_DAYS_PER_RUN",
        "G2B_BUDGET_SYNC_MAX_PAGES",
        "G2B_BUDGET_SYNC_MAX_REQUESTS",
        "G2B_OPERATIONAL_LEASE_RETRY_SECONDS",
        "G2B_BUDGET_SCHEMA",
        "G2B_BUDGET_POOL_SIZE",
        "G2B_BUDGET_MAX_OVERFLOW",
        "G2B_BUDGET_POOL_TIMEOUT_SECONDS",
        "G2B_BUDGET_POOL_RECYCLE_SECONDS",
        "G2B_BUDGET_CONNECT_TIMEOUT_SECONDS",
        "G2B_BUDGET_LOCK_TIMEOUT_MS",
        "G2B_BUDGET_STATEMENT_TIMEOUT_MS",
        "G2B_BUDGET_RETENTION_BATCH_SIZE",
        "G2B_BUDGET_RETENTION_DAYS",
        "G2B_BUDGET_RECEIPT_RETENTION_DAYS",
        "G2B_SQLITE_TIMEOUT",
        "G2B_SQLITE_WAL",
    }
    for name in sorted(required_docs):
        assert name in readme, name

    for name in {
        "G2B_SHOPPING_SYNC_INTERVAL_SECONDS",
        "G2B_SHOPPING_SYNC_DAYS_PER_RUN",
        "G2B_BUDGET_SYNC_MAX_PAGES",
        "G2B_BUDGET_SYNC_MAX_REQUESTS",
        "G2B_OPERATIONAL_LEASE_RETRY_SECONDS",
    }:
        assert name in source, name

    for name in {
        "G2B_BUDGET_SCHEMA",
        "G2B_BUDGET_POOL_SIZE",
        "G2B_BUDGET_MAX_OVERFLOW",
        "G2B_BUDGET_POOL_TIMEOUT_SECONDS",
        "G2B_BUDGET_POOL_RECYCLE_SECONDS",
        "G2B_BUDGET_CONNECT_TIMEOUT_SECONDS",
        "G2B_BUDGET_LOCK_TIMEOUT_MS",
        "G2B_BUDGET_STATEMENT_TIMEOUT_MS",
        "G2B_BUDGET_RETENTION_BATCH_SIZE",
    }:
        assert name in pg, name

    assert "G2B_SQLITE_TIMEOUT" in db
    assert "G2B_SQLITE_WAL" in db


def test_removed_unused_shopping_lookback_setting_does_not_return():
    assert "G2B_SHOPPING_SYNC_LOOKBACK_DAYS" not in (
        ROOT / "vnext_clean_app.py"
    ).read_text(encoding="utf-8")
    assert not hasattr(vnext_clean_app, "SHOPPING_SYNC_LOOKBACK_DAYS")
