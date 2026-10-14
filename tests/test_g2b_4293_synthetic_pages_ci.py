"""V4.1.298 deterministic synthetic 999/1000 source pages must stay CI-only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_synthetic_source_pages_use_real_transaction_paths_but_no_live_http():
    source = (ROOT / "scripts/g2b_256mib_synthetic_page_ci.py").read_text()
    assert "G2B_CI_SYNTHETIC_PAGES" in source
    assert "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test" in source
    assert "G2B_4293_SYNTHETIC_CANARY_AUTH_HOLD" in source
    assert "G2B_SERVICE_KEY" in source and "LOFIN_API_KEY" in source
    assert "shopping_vnext.collect_all(" in source
    assert "budget_pg_collection.collect_pages(" in source
    assert "for n in range(999)" in source
    assert "for n in range(1000)" in source
    assert "EXTERNAL_SOURCE_HTTP_FORBIDDEN_IN_CI" in source
    assert "G2B_4293_SYNTHETIC_PAGE_COMPLETE" in source
    assert "G2B_4293_SYNTHETIC_PAGE_MEMORY" in source
    assert "request_source_api(" not in source


def test_synthetic_run_is_constrained_by_same_256mib_web_cgroup():
    script = (ROOT / "scripts/g2b_256mib_cgroup_canary.sh").read_text()
    workflow = (ROOT / ".github/workflows/g2b-vnext.yml").read_text()
    assert "--memory=256m --memory-swap=256m" in script
    assert "docker exec -e G2B_CI_SYNTHETIC_PAGES=1" in script
    assert "g2b_256mib_synthetic_page_ci.py" in script
    assert "G2B_4293_REAL_256MIB_SYNTHETIC_PAGE_PROFILE" in script
    assert 'final_kills" != "$before_kills"' in script
    assert "G2B_4293_256MIB_SYNTHETIC_PAGE_CGROUP_OK" in script
    assert "fix/g2b-4.1.298-" in workflow
