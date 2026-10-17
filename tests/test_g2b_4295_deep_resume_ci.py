"""V4.1.301 synthetic 424/477 QWGJK resume regression safety.

CI-only generated 423 receipt pages are intentionally ONE row per page.
The independently tested V4.1.293 canary covers a 1000-row budget page.
Never touch a real production DB or procurement API.
"""
from pathlib import Path

import pytest

from scripts import g2b_256mib_budget_page424_resume_ci as deep
import safe_boot_vnext

ROOT = Path(__file__).resolve().parents[1]


def test_4295_rejects_unapproved_environment(monkeypatch):
    assert deep._authorized("seed") is False
    monkeypatch.setenv("G2B_CI_DEEP_RESUME", "1")
    monkeypatch.setenv("G2B_DATABASE_URL", deep.CI_DATABASE)
    monkeypatch.setenv("G2B_APP_SCHEMA", "g2b_app_4292_ci")
    monkeypatch.setenv("G2B_BUDGET_SCHEMA", "g2b_budget_4292_ci")
    monkeypatch.setenv("G2B_AUTO_SYNC_DISABLE", "1")
    for name in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"):
        monkeypatch.setenv(name, "")
    for phase in deep.PHASES:
        assert deep._authorized(phase) is True
    assert deep._authorized("drop") is False
    monkeypatch.setenv("LOFIN_API_KEY", "secret-do-not-use")
    assert deep._authorized("seed") is False
    monkeypatch.setenv("LOFIN_API_KEY", "")
    monkeypatch.setenv("G2B_DATABASE_URL", "postgresql://example.com/live")
    assert deep._authorized("seed") is False


def test_4295_exact_deep_page_fixture_contract_and_digest():
    assert deep.PREVIOUS_PAGES == 423
    assert deep.LAST_PAGE == 477
    assert deep.SIZE == 1
    assert deep.SCOPE == "ci4295:2026-10-10"
    assert deep.CONTRACT.startswith("G2B_4295_")
    assert deep._key(deep._row(424)) == "CI4295-ROW-00424"
    assert deep._row(423)["amount"] != deep._row(424)["amount"]
    assert len(deep._fingerprint()) == 64
    assert set(deep.PHASES) == {"seed", "interrupt", "resume", "finish"}


def test_4295_source_free_pg_actual_resume_and_receipt_validation_are_wired():
    source = (ROOT / "scripts/g2b_256mib_budget_page424_resume_ci.py").read_text()
    canary = (ROOT / "scripts/g2b_256mib_cgroup_canary.sh").read_text()
    workflow = (ROOT / ".github/workflows/g2b-vnext.yml").read_text()
    assert "store.preserve_observation(" in source
    assert 'conn.execute(insert(tables["pages"]), pages)' in source
    assert 'conn.execute(insert(tables["items"]), items)' in source
    assert "collector.verified_checkpoint(cp)" in source
    assert 'status="RUNNING"' in source
    assert 'memory_guard.MemoryPressureError(' in source
    assert '_verify(424, "INCOMPLETE")' in source
    assert 'expect_calls=list(range(424, 477))' in source
    assert '_verify(478, "COMPLETE"' in source
    assert 'CI4295_COMPLETE_CHECKPOINT_MUST_SKIP_SOURCE' in source
    assert "CI4295_EXTERNAL_SOURCE_REQUEST_FORBIDDEN" in source
    assert "G2B_CI_DEEP_RESUME=1" in canary
    assert "G2B_4295_SYNTHETIC_424_OF_477_PG_CHECKPOINT_RESUME_OK" in canary
    assert "G2B_4295_DEEP_RESUME_CGROUP_PROFILE" in canary
    assert "G2B_4295_EXTERNAL_SOURCE_API_CALLS=0" in canary
    assert "startsWith(github.head_ref, 'fix/g2b-4.1.303-')" in workflow


def test_4295_parent_256mib_admission_still_fails_closed():
    assert safe_boot_vnext.CHILD_MIN_SOFT_MIB == 96.0
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64.0
    allowed, reason = safe_boot_vnext.child_admission(
        {"guard_ok": True, "cgroup_oom_group": 0,
         "cgroup_limit_mib": 256, "cgroup_effective_mib": 113.3},
        worker_soft_limit_mib=112,
    )
    assert not allowed
    assert reason == "CGRP_CHILD_HEADROOM_UNSAFE"


@pytest.mark.parametrize("name", [
    "scripts/g2b_256mib_budget_page424_resume_ci.py",
    "scripts/g2b_256mib_cgroup_canary.sh",
])
def test_4295_no_production_cleanup_or_online_procurement_api(name):
    s = (ROOT / name).read_text()
    assert "DROP TABLE" not in s
    assert "TRUNCATE " not in s
    assert "https://apis.data.go.kr" not in s
    assert "real-cafe24.example" not in s
