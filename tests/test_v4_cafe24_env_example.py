from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _parse_env_example():
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_cafe24_env_example_has_safe_first_boot_contract():
    values = _parse_env_example()

    assert values["G2B_TEST_MODE"] == "0"
    assert values["G2B_FULL_RUNTIME_ENABLE"] == "0"
    assert values["G2B_BACKEND_INIT_ENABLE"] == "0"
    assert values["G2B_POST_BOOT_MAINTENANCE_ENABLE"] == "0"
    assert values["G2B_AUTO_SYNC"] == "0"
    assert values["G2B_AUTO_SYNC_DISABLE"] == "1"
    assert values["G2B_V41_FRESH_START"] == "0"
    assert values["G2B_DESTRUCTIVE_RESET_CONFIRM"] == "0"
    assert values["G2B_RUNTIME_ROLE"] == "UNIFIED"
    assert values["G2B_APP_SCHEMA"] == "g2b_app"
    assert values["G2B_BUDGET_SCHEMA"] == "g2b_budget"
    assert "G2B_SQLITE_WAL" not in values
    assert "G2B_DB_PATH" not in values


def test_cafe24_env_example_requires_placeholder_postgres_and_empty_source_keys():
    values = _parse_env_example()

    url = values["G2B_DATABASE_URL"]
    assert url == "postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
    assert values["G2B_SERVICE_KEY"] == ""
    assert values["LOFIN_API_KEY"] == ""
    assert values["EDUINFO_API_KEY"] == ""


def test_cafe24_env_example_contains_only_placeholders_or_empty_credentials():
    values = _parse_env_example()

    assert values["G2B_DATABASE_URL"] == (
        "postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
    )
    assert values["G2B_SERVICE_KEY"] == ""
    assert values["LOFIN_API_KEY"] == ""
    assert values["EDUINFO_API_KEY"] == ""

    for key, value in values.items():
        if key.endswith("_KEY"):
            assert value == "", key


def test_release_runbook_points_to_env_example():
    text = (ROOT / "DEPLOYMENT_V4_RUNBOOK.md").read_text(encoding="utf-8")
    assert ".env.example" in text
    assert "never" in text.lower() and "commit" in text.lower()
