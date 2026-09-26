from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_manual_validation_workflows_target_current_main_branch():
    canary = _text(".github/workflows/g2b-vnext-canary.yml")
    small = _text(".github/workflows/g2b-vnext-small-validation.yml")
    regression = _text(".github/workflows/g2b-vnext.yml")

    assert "refs/heads/main" in canary
    assert "refs/heads/main" in small
    assert "feature/g2b-vnext-foundation" not in canary
    assert "feature/g2b-vnext-foundation" not in small
    assert "branches: [main]" in regression
    assert "feature/g2b-vnext-foundation" not in regression


def test_bounded_canary_metadata_matches_main_active_runtime():
    text = _text("scripts/g2b_bounded_canary.py")
    assert '"main_merge_hold": False' in text
    assert '"deployment_state": "MAIN_ACTIVE"' in text
    assert '"main_merge_hold": True' not in text


def test_regression_runner_clears_every_supported_source_credential():
    text = _text("scripts/g2b_verify.py")
    for name in ("G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"):
        assert f"{name}=''" in text


def test_current_docs_do_not_describe_removed_2x_test_login_as_operational():
    readme = _text("README.md")
    summary = _text("CHANGE_SUMMARY.md")
    assert "SINSUNG G2B vNext 3.1.7" in readme
    assert "비밀키는 SQLite에 저장하지 않고 환경변수에서만 읽습니다." not in readme
    assert "테스트 로그인 `admin / 1234`" not in summary
    assert "과거 2.x TEST" in summary
