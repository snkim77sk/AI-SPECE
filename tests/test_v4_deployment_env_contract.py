from pathlib import Path

import vnext_clean_app
import g2b_database


ROOT = Path(__file__).resolve().parents[1]


def test_cafe24_environment_contract_is_documented_and_live():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    source = (ROOT / "vnext_clean_app.py").read_text(encoding="utf-8")
    database = (ROOT / "g2b_database.py").read_text(encoding="utf-8")
    pg = (ROOT / "budget_pg_store.py").read_text(encoding="utf-8")
    shopping_store = (ROOT / "shopping_store_v41.py").read_text(encoding="utf-8")

    required_docs = {
        "G2B_TEST_MODE",
        "G2B_DATABASE_URL",
        "G2B_AUTO_SYNC",
        "G2B_AUTO_SYNC_DISABLE",
        "G2B_POST_BOOT_MAINTENANCE_ENABLE",
        "G2B_MATCH_ROLLOVER_AUTO_ENABLE",
        "G2B_MEMORY_SOFT_LIMIT_MB",
        "G2B_BUILD_COMMIT",
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
        "G2B_SHOPPING_RECHECK_DAYS",
        "G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN",
        "G2B_SHOPPING_RETENTION_DAYS",
        "G2B_SHOPPING_RETENTION_MONTHS",
        "G2B_SHOPPING_RETENTION_BATCH_SIZE",
        "G2B_BUDGET_SYNC_MAX_PAGES",
        "G2B_BUDGET_SYNC_MAX_REQUESTS",
        "G2B_BUDGET_HISTORY_DAYS_PER_RUN",
        "G2B_BUDGET_HISTORY_RESERVE_REQUESTS",
        "G2B_FUTURE_BUDGET_SYNC_MAX_PAGES",
        "G2B_CURRENT_APPROPRIATION_SYNC_MAX_PAGES",
        "G2B_OPERATIONAL_LEASE_RETRY_SECONDS",
        "G2B_VNEXT_API_DAILY_LIMIT",
        "LOFIN_VNEXT_API_DAILY_LIMIT",
    }
    for name in sorted(required_docs):
        assert name in readme, name

    for name in {
        "G2B_SHOPPING_SYNC_INTERVAL_SECONDS",
        "G2B_SHOPPING_SYNC_DAYS_PER_RUN",
        "G2B_SHOPPING_RECHECK_DAYS",
        "G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN",
        "G2B_SHOPPING_RETENTION_DAYS",
        "G2B_SHOPPING_RETENTION_MONTHS",
        "G2B_BUDGET_SYNC_MAX_PAGES",
        "G2B_BUDGET_SYNC_MAX_REQUESTS",
        "G2B_BUDGET_HISTORY_DAYS_PER_RUN",
        "G2B_BUDGET_HISTORY_RESERVE_REQUESTS",
        "G2B_FUTURE_BUDGET_SYNC_MAX_PAGES",
        "G2B_CURRENT_APPROPRIATION_SYNC_MAX_PAGES",
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

    assert "G2B_SHOPPING_RETENTION_BATCH_SIZE" in (readme + shopping_store)

def test_shopping_recheck_environment_is_bounded_and_documented(monkeypatch):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    monkeypatch.setenv("G2B_SHOPPING_RECHECK_DAYS", "99")
    import importlib
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_RECHECK_DAYS == 7

    monkeypatch.setenv("G2B_SHOPPING_RECHECK_DAYS", "0")
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_RECHECK_DAYS == 0

    monkeypatch.setenv("G2B_SHOPPING_RECHECK_DAYS", "7")
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_RECHECK_DAYS == 7

    assert "G2B_SHOPPING_RECHECK_DAYS=7" in readme
    assert "G2B_SHOPPING_RECHECK_DAYS=7" in env_example


def test_shopping_longtail_environment_is_bounded_and_documented(monkeypatch):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    monkeypatch.setenv("G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN", "99")
    import importlib
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN == 2

    monkeypatch.setenv("G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN", "0")
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN == 0

    monkeypatch.setenv("G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN", "2")
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN == 2

    assert "G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN=2" in readme
    assert "G2B_SHOPPING_LONGTAIL_RECHECK_DAYS_PER_RUN=2" in env_example


def test_shopping_retention_environment_prefers_exact_27_months(monkeypatch):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    monkeypatch.setenv("G2B_SHOPPING_RETENTION_DAYS", "999")
    monkeypatch.setenv("G2B_SHOPPING_RETENTION_MONTHS", "99")
    import importlib
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_RETENTION_DAYS == 365
    assert vnext_clean_app.SHOPPING_RETENTION_MONTHS == 27

    monkeypatch.setenv("G2B_SHOPPING_RETENTION_DAYS", "365")
    monkeypatch.setenv("G2B_SHOPPING_RETENTION_MONTHS", "27")
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.SHOPPING_RETENTION_DAYS == 365
    assert vnext_clean_app.SHOPPING_RETENTION_MONTHS == 27

    assert "G2B_SHOPPING_RETENTION_MONTHS=27" in readme
    assert "G2B_SHOPPING_RETENTION_DAYS=365" in env_example
    assert "G2B_SHOPPING_RETENTION_MONTHS=27" in env_example


def test_shopping_retention_batch_environment_is_bounded_and_documented(monkeypatch):
    import shopping_store_v41

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    monkeypatch.setenv("G2B_SHOPPING_RETENTION_BATCH_SIZE", "999999")
    assert shopping_store_v41._retention_batch_size() == 10000

    monkeypatch.setenv("G2B_SHOPPING_RETENTION_BATCH_SIZE", "1")
    assert shopping_store_v41._retention_batch_size() == 100

    monkeypatch.setenv("G2B_SHOPPING_RETENTION_BATCH_SIZE", "2000")
    assert shopping_store_v41._retention_batch_size() == 2000

    assert "G2B_SHOPPING_RETENTION_BATCH_SIZE=2000" in readme
    assert "G2B_SHOPPING_RETENTION_BATCH_SIZE=2000" in env_example


def test_g2b_and_lofin_quota_environment_contracts_are_independent():
    shopping_http = (ROOT / "vnext_http.py").read_text(encoding="utf-8")
    lofin_http = (ROOT / "lofin_vnext_http.py").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "G2B_VNEXT_API_DAILY_LIMIT" in shopping_http
    assert "LOFIN_VNEXT_API_DAILY_LIMIT" not in shopping_http
    assert "LOFIN_VNEXT_API_DAILY_LIMIT" in lofin_http
    assert "G2B_VNEXT_API_DAILY_LIMIT" not in lofin_http
    assert "G2B_VNEXT_API_DAILY_LIMIT=900" in readme
    assert "LOFIN_VNEXT_API_DAILY_LIMIT=500" in readme
    assert "G2B_VNEXT_API_DAILY_LIMIT=900" in env_example
    assert "LOFIN_VNEXT_API_DAILY_LIMIT=500" in env_example
    assert "G2B_BUDGET_SYNC_MAX_REQUESTS=500" in readme
    assert "G2B_BUDGET_SYNC_MAX_REQUESTS=500" in env_example


def test_cafe24_auto_database_variables_do_not_require_duplicate_manual_url():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "DEPLOYMENT_V41_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "Cafe24 `DB_*` 자동변수" in readme
    assert "DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME" in runbook
    assert "do **not** duplicate" in runbook
    assert "Never delete or rewrite Cafe24 system" in runbook
    assert "DO NOT add a duplicate G2B_DATABASE_URL" in env_example
    assert "Use this placeholder only when platform" in env_example


def test_v41_release_policy_defaults_heavy_work_off():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "DEPLOYMENT_V41_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "G2B_AUTO_SYNC=0" in env_example
    assert "G2B_AUTO_SYNC_DISABLE=0" in env_example
    assert "G2B_POST_BOOT_MAINTENANCE_ENABLE=0" in env_example
    assert "G2B_MATCH_ROLLOVER_AUTO_ENABLE=0" in env_example
    assert "G2B_MEMORY_SOFT_LIMIT_MB=160" in env_example
    assert "자동수집 OFF" in readme
    assert "G2B_AUTO_SYNC=1" in readme
    assert "source work only by explicit opt-in" in runbook
    assert "G2B_AUTO_SYNC_DISABLE=1" in readme
    assert "G2B_AUTO_SYNC_DISABLE=1" in runbook
    assert "G2B_V41_FRESH_START=0" in readme
    assert "G2B_V41_FRESH_START=0" in runbook
    assert "G2B_V41_FRESH_START=0" in env_example
    assert "G2B_DESTRUCTIVE_RESET_CONFIRM=0" in env_example
    assert "G2B_EXPECTED_BUILD_COMMIT" in env_example
    assert "G2B_EXPECTED_SOURCE_FINGERPRINT" in env_example
    assert "G2B_BUILD_COMMIT" in readme
    assert "G2B_BUILD_COMMIT" in runbook
    assert "G2B_BUILD_COMMIT=" in env_example


def test_env_example_has_parseable_memory_and_reset_controls():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "\\n" not in env_example

    assignments = {}
    for raw_line in env_example.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        assert "=" in line, raw_line
        name, value = line.split("=", 1)
        assert name.strip() == name
        assignments[name] = value.strip()

    assert assignments["G2B_DESTRUCTIVE_RESET_CONFIRM"] == "0"
    assert assignments["G2B_MEMORY_SOFT_LIMIT_MB"] == "160"
    assert assignments["G2B_ISOLATED_WORKER_SOFT_LIMIT_MB"] == "112"
    assert assignments["G2B_V41_FRESH_START"] == "0"


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


def test_settings_ui_surfaces_cached_fresh_start_marker_without_sql():
    source = (ROOT / "vnext_clean_app.py").read_text(encoding="utf-8")

    assert "4.1 fresh-start marker" in source
    assert "fresh_start_marker_ok" in source
    assert "fresh_start_marker_value" in source
    assert "G2B_V41_FRESH_START 제거 가능" in source
    assert "NORMALIZED_NO_RAW_V1" in source


def test_settings_ui_reports_safe_database_source_without_secret_values():
    source = (ROOT / "vnext_clean_app.py").read_text(encoding="utf-8")

    assert "database_source_label()" in source
    assert "Cafe24 DB_* 자동변수" in source
    assert "PostgreSQL PG* 자동변수" in source
    assert "직접 G2B_DATABASE_URL" in source
    assert "PostgreSQL 연결정보 필요 · G2B_DATABASE_URL 또는 Cafe24 자동 DB 변수" in source


def test_settings_ui_uses_canonical_database_env_and_credential_store():
    source = (ROOT / "vnext_clean_app.py").read_text(encoding="utf-8")

    assert "G2B_DATABASE_URL 또는 Cafe24 자동 DB 변수" in source
    assert "database_source_label()" in source
    assert "G2B_BUDGET_DATABASE_URL 필요" not in source
    assert "G2B_BUDGET_DATABASE_URL 설정됨" not in source
    assert 'source_credential_configured("eduinfo_api_key")' in source
