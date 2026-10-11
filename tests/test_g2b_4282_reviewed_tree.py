"""4.1.282 audited full Git tree equality before any production PG probe."""
import os
from pathlib import Path
import subprocess

import pytest

from scripts import g2b_integrated_release_gate as gate

GOOD_SHA = "a" * 40
OTHER_SHA = "b" * 40


def _fixture(monkeypatch, *, commit=GOOD_SHA, tree=GOOD_SHA,
             clean=True, verified=True):
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_AUTO_SYNC_DISABLE", "0")
    monkeypatch.setenv("G2B_V41_FRESH_START", "0")
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", "0")
    monkeypatch.setattr(gate, "_release_manifest_matches", lambda: True)
    monkeypatch.setattr(gate, "build_commit_info", lambda: {
        "build_commit": GOOD_SHA,
        "build_commit_source": "GITHUB_SHA",
        "build_commit_authoritative": True,
        "git_checkout_commit": GOOD_SHA,
        "configured_build_commit": "",
        "build_commit_mismatch": False,
    })
    monkeypatch.setattr(gate, "_local_checkout_tree_info", lambda: {
        "checkout_commit": commit if verified else "",
        "checkout_tree": tree if verified else "",
        "checkout_clean": clean if verified else False,
        "git_checkout_verified": verified,
    })
    stages = []
    def no_postgres(*args, **kwargs):
        stages.append("PG")
        raise AssertionError("release-tree failure must reject before PG")
    monkeypatch.setattr(gate.cold, "inspect_cutover", no_postgres)
    monkeypatch.setattr(gate.checkpoint, "inspect_checkpoint_cutover", no_postgres)
    return stages


@pytest.mark.parametrize("tree,commit,clean,verified,expected", [
    ("", GOOD_SHA, True, True, "SPECIFY_REVIEWED_40_CHARACTER_TREE_SHA"),
    (OTHER_SHA, GOOD_SHA, True, True, "REVIEWED_RELEASE_TREE_MISMATCH"),
    (GOOD_SHA, OTHER_SHA, True, True, "GIT_HEAD_BUILD_COMMIT_MISMATCH"),
    (GOOD_SHA, GOOD_SHA, False, True, "RELEASE_WORKTREE_DIRTY"),
    (GOOD_SHA, GOOD_SHA, False, False, "VERIFY_LOCAL_GIT_RELEASE_CHECKOUT"),
])
def test_release_tree_gate_holds_before_database(monkeypatch,tree,commit,clean,verified,expected):
    stages = _fixture(monkeypatch,commit=commit,clean=clean,verified=verified)
    report = gate.inspect_integrated_release(
        reviewed_main_head=GOOD_SHA, reviewed_release_tree=tree,
        old_processes_stopped=True, merged_main_confirmed=True,
    )
    assert not stages
    assert report["cutover_evidence_ready"] is False
    assert report["deployment_authorized"] is False
    assert expected in report["required_actions"]
    assert report["stages_checked"] == []


def _git(repo,*args):
    proc = subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, timeout=5)
    return proc.stdout.strip()


def test_real_local_git_tree_and_dirty_untracked_are_rejected(monkeypatch,tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "CI")
    _git(tmp_path, "config", "user.email", "ci@example.invalid")
    (tmp_path / "fixture.py").write_text("VALUE = 1\n",encoding="utf-8")
    _git(tmp_path, "add", "fixture.py")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    monkeypatch.setattr(gate,"ROOT",tmp_path)
    expected=_git(tmp_path, "show", "-s", "--format=%H%n%T", "HEAD").splitlines()
    probe=gate._local_checkout_tree_info()
    assert probe["git_checkout_verified"] is True
    assert probe["checkout_commit"]==expected[0]
    assert probe["checkout_tree"]==expected[1]
    assert probe["checkout_clean"] is True
    (tmp_path / "fixture.py").write_text("VALUE = 2\n",encoding="utf-8")
    assert gate._local_checkout_tree_info()["checkout_clean"] is False
    _git(tmp_path, "checkout", "--", "fixture.py")
    (tmp_path / "unexpected.py").write_text("X=1\n",encoding="utf-8")
    assert gate._local_checkout_tree_info()["checkout_clean"] is False


def test_unrelated_git_worktree_env_is_ignored(monkeypatch,tmp_path):
    repo=tmp_path / "repo"
    wrong=tmp_path / "wrong"
    repo.mkdir()
    wrong.mkdir()
    for dir in (repo,wrong):
        _git(dir,"init","-q")
        _git(dir,"config","user.name","CI")
        _git(dir,"config","user.email","ci@example.invalid")
        (dir/"module.py").write_text("X=1\n",encoding="utf-8")
        _git(dir,"add","module.py")
        _git(dir,"commit","-q","-m","fixture")
    monkeypatch.setattr(gate,"ROOT",repo)
    monkeypatch.setenv("GIT_DIR",str(wrong/".git"))
    monkeypatch.setenv("GIT_WORK_TREE",str(wrong))
    probe=gate._local_checkout_tree_info()
    assert probe["git_checkout_verified"] is True
    assert probe["checkout_commit"]==_git(repo,"rev-parse","HEAD")
    assert probe["checkout_clean"] is True


def test_default_draft_release_stays_hold_even_with_clean_tree(monkeypatch):
    stages=_fixture(monkeypatch)
    report=gate.inspect_integrated_release(
        reviewed_main_head=GOOD_SHA,reviewed_release_tree=GOOD_SHA,
        old_processes_stopped=False,merged_main_confirmed=False,
        ci_simulation=True,
    )
    assert not stages
    assert report["deployment_authorized"] is False
    assert report["cutover_evidence_ready"] is False
    assert "MERGE_AND_REVIEW_CUMULATIVE_DRAFT_PRS_FIRST" in report["required_actions"]
    assert "CONFIRM_OLD_WEB_AND_ALL_CHILD_PIDS_EXITED" in report["required_actions"]
