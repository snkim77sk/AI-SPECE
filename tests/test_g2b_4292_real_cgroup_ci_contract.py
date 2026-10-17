"""V4.1.301 real 256MiB experiment is CI-only and never weakens release gates."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_4292_disposable_cgroup_job_is_pull_request_only():
    workflow = (ROOT / ".github/workflows/g2b-vnext.yml").read_text()
    assert "  cgroup256:" in workflow
    assert "startsWith(github.head_ref, 'fix/g2b-4.1.303-')" in workflow
    assert "docker build -f scripts/Dockerfile.g2b-256-ci" in workflow
    assert "bash scripts/g2b_256mib_cgroup_canary.sh" in workflow
    assert "POSTGRES_DB: g2b_vnext_test" in workflow


def test_4292_source_free_real_cgroup_canary_has_strict_limit_and_cold_database():
    script = (ROOT / "scripts/g2b_256mib_cgroup_canary.sh").read_text()
    assert "268435456" in script
    assert "--memory=256m --memory-swap=256m" in script
    assert "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test" in script
    assert "G2B_4292_REJECT_NON_CI_DATABASE" in script
    assert "G2B_4292_REJECT_SOURCE_CREDENTIALS" in script
    assert "G2B_AUTO_SYNC_DISABLE=1" in script
    assert 'LOFIN_API_KEY=' in script and 'G2B_SERVICE_KEY=' in script
    assert 'G2B_HEAVY_WORKER_SOURCE_STATE $source WAITING_KEYS 72' in script
    assert 'G2B_SOURCE_WORKER_MEMORY' in script
    assert '"$after_kills" != "$before_kills"' in script
    assert "api.data.go.kr" not in script
    assert "Cafe24" in script


def test_4292_docker_image_does_not_embed_company_data_or_source_keys():
    image = (ROOT / "scripts/Dockerfile.g2b-256-ci").read_text()
    assert "FROM python:3.12-slim-bookworm" in image
    assert "COPY requirements.txt" in image
    assert "COPY ." not in image
    assert "G2B_SERVICE_KEY" not in image
    assert "LOFIN_API_KEY" not in image
