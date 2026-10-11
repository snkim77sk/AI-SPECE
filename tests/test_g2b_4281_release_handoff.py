"""4.1.281 final integration handoff: metadata and default HOLD only.

Release documentation and versioned CI are inspected without PostgreSQL, a
remote Cafe24 connection, or any procurement/LOFIN API operation.
"""
from pathlib import Path
import tomllib

from app_version import APP_VERSION
from scripts import g2b_integrated_release_gate as gate

ROOT = Path(__file__).resolve().parents[1]
VERSION = "4.1.281"


def test_4281_release_manifest_identity():
    assert APP_VERSION == VERSION
    assert (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() == (
        "SINSUNG G2B VNEXT " + VERSION
    )
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == VERSION
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# SINSUNG G2B vNext " + VERSION + "\n")
    assert gate._release_manifest_matches() is True


def test_4281_cumulative_handoff_documents_verified_chain_and_holds():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "DEPLOYMENT_V41_RUNBOOK.md").read_text(encoding="utf-8")
    for excerpt in ("#301~#341", "64", "72", "021de332296e094d917371e8736f6b964ebe380c"):
        assert excerpt in readme
    assert "4.1.281 CUMULATIVE RELEASE HANDOFF" in runbook
    assert "사용자 승인" in runbook
    assert "기존 PG 데이터" in runbook
    assert "The 4.1 reset intentionally discards" not in runbook
    assert "Fresh-start\ndrop/create operations still require" not in runbook


def test_4281_ci_version_pins_are_updated():
    deploy = (ROOT / ".github/workflows/g2b-4.1.141-cafe24-deploy.yml").read_text(
        encoding="utf-8"
    )
    liveness = (ROOT / ".github/workflows/g2b-4.1.169-70s-liveness.yml").read_text(
        encoding="utf-8"
    )
    integration = (ROOT / ".github/workflows/g2b-vnext.yml").read_text(
        encoding="utf-8"
    )
    assert "g2b-4.1.281-cafe24-local-launcher-smoke" in deploy
    assert "does not connect to or deploy Cafe24" in deploy
    assert "4.1.280" not in deploy
    assert "4.1.280" not in liveness
    assert "4.1.281" in deploy and "4.1.281" in liveness
    assert "Cumulative 4.1.281 integrated predeploy gate rehearsal" in integration


def test_4281_default_gate_cannot_contact_pg_without_operator_prerequisites(monkeypatch):
    def _never(*args, **kwargs):
        raise AssertionError("HOLD gate must not open a PostgreSQL session")
    monkeypatch.setattr(gate.cold, "inspect_cutover", _never)
    monkeypatch.setattr(gate.checkpoint, "inspect_checkpoint_cutover", _never)
    result = gate.inspect_integrated_release()
    assert result["cutover_evidence_ready"] is False
    assert result["deployment_authorized"] is False
    assert result["stages_checked"] == []
    assert "MERGE_AND_REVIEW_CUMULATIVE_DRAFT_PRS_FIRST" in result["required_actions"]
    assert "CONFIRM_OLD_WEB_AND_ALL_CHILD_PIDS_EXITED" in result["required_actions"]
