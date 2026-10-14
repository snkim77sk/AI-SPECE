"""V4.1.297: honest CI-only 113.3MiB baseline and guarded page stress.

No source API, production database, stored checkpoint reset or release action.
"""
from pathlib import Path

import pytest

from scripts import g2b_256mib_baseline_ballast_ci as ballast
import safe_boot_vnext

ROOT = Path(__file__).resolve().parents[1]


def _allow_ci(monkeypatch):
    monkeypatch.setenv("G2B_CI_BASELINE_BALLAST", "1")
    monkeypatch.setenv("G2B_DATABASE_URL", ballast.EXPECTED_CI_DATABASE)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_AUTO_SYNC_DISABLE", "1")
    monkeypatch.setenv("G2B_APP_SCHEMA", "g2b_app_4292_ci")
    monkeypatch.setenv("G2B_BUDGET_SCHEMA", "g2b_budget_4292_ci")
    for name in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"):
        monkeypatch.setenv(name, "")


def test_4294_baseline_helper_never_authorized_without_exact_ephemeral_contract(monkeypatch):
    assert ballast.authorized() is False
    _allow_ci(monkeypatch)
    assert ballast.authorized() is True
    monkeypatch.setenv("G2B_DATABASE_URL", "postgresql://example.invalid/live")
    assert ballast.authorized() is False
    _allow_ci(monkeypatch)
    monkeypatch.setenv("G2B_SERVICE_KEY", "must-refuse")
    assert ballast.authorized() is False
    _allow_ci(monkeypatch)
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    assert ballast.authorized() is False


def test_4294_actual_256mib_ballast_stays_anonymous_and_reproduces_measured_effective():
    helper = (ROOT / "scripts/g2b_256mib_baseline_ballast_ci.py").read_text()
    script = (ROOT / "scripts/g2b_256mib_cgroup_canary.sh").read_text()
    assert "TARGET_MIB = 113.3" in helper
    assert "chunks.append(bytearray(size))" in helper
    assert "container_budget_snapshot()" in helper
    assert "g2b-4294-baseline-ready.json" in helper
    assert "G2B_4294_CAFE24_BASELINE_REPRODUCED" in helper
    assert 'docker exec -d -e G2B_CI_BASELINE_BALLAST=1 "$name"' in script
    assert "G2B_4294_CAFE24_PRELAUNCH_EFFECTIVE_REPRODUCED" in script
    assert "abs(effective-113.3)<=2.0" in script
    assert "G2B_4294_REAL_CAFE24_BASELINE_CGROUP_OK" in script
    assert "G2B_4294_SOURCE_API_CALLS=0" in script


def test_4294_source_stress_uses_different_record_scope_and_full_reserve():
    source = (ROOT / "scripts/g2b_256mib_synthetic_page_ci.py").read_text()
    script = (ROOT / "scripts/g2b_256mib_cgroup_canary.sh").read_text()
    workflow = (ROOT / ".github/workflows/g2b-vnext.yml").read_text()
    assert 'run_id not in {"4293", "4294"}' in source
    assert '"2026-10-09" if run_id == "4294"' in source
    assert 'f"CI{run_id}-S-{n:05d}"' in source
    assert 'f"CI{run_id}-B-{n:05d}"' in source
    assert 'f"ci{run_id}:{day}"' in source
    assert "note_multiplier = 192 if run_id" in source
    assert 'G2B_CI_SYNTHETIC_RUN_ID=4294' in script
    assert "assert observed+64.0 < 256.0" in script
    assert 'final_kills" != "$baseline_kills"' not in script  # uses separate post-ballast count
    assert "after_ballast_kills" in script
    assert "startsWith(github.head_ref, 'audit/g2b-4.1.297-')" in workflow
    assert "G2B_CI_SYNTHETIC_PAGES" in source


def test_4294_legacy_parent_worker_admission_remains_fail_closed():
    assert safe_boot_vnext.CHILD_MIN_SOFT_MIB == 96.0
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64.0
    assert safe_boot_vnext.child_admission({
        "guard_ok": True, "cgroup_oom_group": 0,
        "cgroup_limit_mib": 256, "cgroup_effective_mib": 113.3,
    }, worker_soft_limit_mib=112) == (False, "CGRP_CHILD_HEADROOM_UNSAFE")


@pytest.mark.parametrize("name", [
    "scripts/g2b_256mib_baseline_ballast_ci.py",
    "scripts/g2b_256mib_synthetic_page_ci.py",
])
def test_4294_synthetic_helpers_have_no_main_runtime_or_data_delete(name):
    s = (ROOT / name).read_text()
    assert "DROP TABLE" not in s
    assert "TRUNCATE " not in s
    assert "subprocess.Popen(" not in s
    assert "https://apis.data.go.kr" not in s
