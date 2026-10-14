"""V4.1.300 CI-only budget classification memory / normalized PG integration audit."""
from pathlib import Path

import pytest

from scripts import g2b_256mib_budget_classification_ci as classify_ci
import safe_boot_vnext

ROOT = Path(__file__).resolve().parents[1]


def _enable_ci(monkeypatch):
    monkeypatch.setenv("G2B_CI_BUDGET_CLASSIFY", "1")
    monkeypatch.setenv("G2B_DATABASE_URL", classify_ci.CI_DATABASE)
    monkeypatch.setenv("G2B_APP_SCHEMA", "g2b_app_4292_ci")
    monkeypatch.setenv("G2B_BUDGET_SCHEMA", "g2b_budget_4292_ci")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_AUTO_SYNC_DISABLE", "1")
    for key in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"):
        monkeypatch.setenv(key, "")


def test_4296_fail_closed_credentials_and_non_disposable_database(monkeypatch):
    assert classify_ci.authorized() is False
    _enable_ci(monkeypatch)
    assert classify_ci.authorized() is True
    monkeypatch.setenv("G2B_DATABASE_URL", "postgresql://another.example/live")
    assert classify_ci.authorized() is False
    _enable_ci(monkeypatch)
    monkeypatch.setenv("LOFIN_API_KEY", "never-allow-key")
    assert classify_ci.authorized() is False
    _enable_ci(monkeypatch)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    assert classify_ci.authorized() is False
    _enable_ci(monkeypatch)
    monkeypatch.setenv("G2B_AUTO_SYNC_DISABLE", "0")
    assert classify_ci.authorized() is False


def test_4296_bound_real_budget_classification_but_not_prod_formulas():
    assert classify_ci.EXPECTED_MIN_CURRENT == 2477
    assert classify_ci.BATCH_SIZE == 250
    assert classify_ci.MAX_BATCHES == 16
    assert classify_ci.DATASETS == (
        "budget", "budget_appropriation", "education_budget"
    )
    source = (ROOT / "scripts/g2b_256mib_budget_classification_ci.py").read_text()
    assert "classification_vnext.classify_dataset(" in source
    assert "budget_pg_store.current_classified_counts(" in source
    assert "budget_pg_collection.verified_checkpoint(checkpoint)" in source
    assert "CI4296_EXTERNAL_SOURCE_HTTP_FORBIDDEN" in source
    assert "CI4296_REPEAT_RECLASSIFIED_EXISTING_ROWS" in source
    assert 'force=False' in source
    assert 'source_api_calls": 0' in source


def test_4296_256mib_same_cgroup_real_web_survives_and_reports_memory():
    script = (ROOT / "scripts/g2b_256mib_cgroup_canary.sh").read_text()
    workflow = (ROOT / ".github/workflows/g2b-vnext.yml").read_text()
    assert "--memory=256m --memory-swap=256m" in script
    assert 'docker exec -e G2B_CI_BUDGET_CLASSIFY=1 "$name"' in script
    assert "G2B_4296_256MIB_BUDGET_CLASSIFICATION_PROFILE" in script
    assert "G2B_4296_256MIB_REAL_CLASSIFICATION_CGROUP_OK" in script
    assert "G2B_4296_EXTERNAL_SOURCE_API_CALLS=0" in script
    assert "effective+64.0<256.0" in script
    assert "classification_kills" in script
    assert "startsWith(github.head_ref, 'fix/g2b-4.1.300-')" in workflow


def test_4296_minimum_112_plus_64_child_preflight_still_denies_cafe24_effective():
    assert safe_boot_vnext.CHILD_MIN_SOFT_MIB == 96.0
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64.0
    assert safe_boot_vnext.child_admission({
        "guard_ok": True, "cgroup_oom_group": 0,
        "cgroup_limit_mib": 256, "cgroup_effective_mib": 113.3,
    }, worker_soft_limit_mib=112) == (False, "CGRP_CHILD_HEADROOM_UNSAFE")


@pytest.mark.parametrize("path", [
    "scripts/g2b_256mib_budget_classification_ci.py",
    "scripts/g2b_256mib_cgroup_canary.sh",
])
def test_4296_no_live_source_endpoint_or_db_table_reset(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "DROP TABLE" not in text
    assert "TRUNCATE " not in text
    assert "https://apis.data.go.kr" not in text
