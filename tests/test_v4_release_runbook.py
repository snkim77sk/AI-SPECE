from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v4_release_runbook_contains_required_deployment_gates():
    text = (ROOT / "DEPLOYMENT_V4_RUNBOOK.md").read_text(encoding="utf-8")

    required = [
        "G2B_TEST_MODE=0",
        "G2B_AUTO_SYNC=0",
        "G2B_AUTO_SYNC=1",
        "G2B_BUDGET_DATABASE_URL",
        "python scripts/g2b_deployment_preflight.py",
        "python scripts/g2b_budget_deployment_canary.py --allow-live",
        "collection_keys_ready=true",
        "page_no=2",
        "PostgreSQL advisory",
        "vnext-runtime-smoke",
        "g2b-vnext",
        "same-KST-day COMPLETE",
        "bulk historical",
        "education live transport",
    ]
    for value in required:
        assert value in text, value


def test_v4_release_runbook_does_not_reenable_no1_sources():
    text = (ROOT / "DEPLOYMENT_V4_RUNBOOK.md").read_text(encoding="utf-8")

    assert "service notices -> NO1" in text
    assert "opening results -> NO1" in text
    assert "awards -> NO1" in text
    assert "contracts -> NO1" in text
    assert "goods bid notices -> NO1" in text
