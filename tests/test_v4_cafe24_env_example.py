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
    assert values["G2B_AUTO_SYNC"] == "0"
    assert values["G2B_RUNTIME_ROLE"] == "UNIFIED"
    assert values["G2B_BUDGET_SCHEMA"] == "g2b_budget"
    assert values["G2B_SQLITE_WAL"] == "0"
    assert "G2B_DB_PATH" not in values


def test_cafe24_env_example_requires_placeholder_postgres_and_empty_source_keys():
    values = _parse_env_example()

    url = values["G2B_BUDGET_DATABASE_URL"]
    assert url == "postgresql://USER:PASSWORD@HOST:PORT/DBNAME"
    assert values["G2B_SERVICE_KEY"] == ""
    assert values["LOFIN_API_KEY"] == ""
    assert values["EDUINFO_API_KEY"] == ""


def test_cafe24_env_example_contains_no_obvious_real_secret_material():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    forbidden_fragments = [
        "snkim77",
        "@naver.com",
        "sinsung",
        "toplt2021",
        "cafe24",
    ]
    lower = text.lower()
    for fragment in forbidden_fragments:
        assert fragment not in lower, fragment

    assert "PASSWORD@HOST" in text
    assert "super-secret" not in lower


def test_release_runbook_points_to_env_example():
    text = (ROOT / "DEPLOYMENT_V4_RUNBOOK.md").read_text(encoding="utf-8")
    assert ".env.example" in text
    assert "never" in text.lower() and "commit" in text.lower()
