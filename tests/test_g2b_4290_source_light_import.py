"""V4.1.290: source-only import isolation without DB, API or web boot."""
import json
import os
import subprocess
import sys

import pytest

import g2b_heavy_worker as worker
import vnext_clean_app as app


@pytest.mark.parametrize(("mode", "limit", "expected"), [
    ("shopping", 256, True),
    ("budget", 256, True),
    ("shopping", 320, True),
    ("budget", 512, False),
    ("shopping", 0, False),
    ("match", 256, False),
])
def test_source_only_requires_known_small_cgroup(mode, limit, expected):
    assert worker._source_only_candidate(
        mode, {"cgroup_limit_mib": limit}
    ) is expected


def test_web_runtime_does_not_enter_source_only_mode():
    # This module was imported by pytest as the ordinary web runtime.
    assert not app._SOURCE_ONLY_RUNTIME
    assert app.app is not None
    assert worker._profile_source_imports("other") == 2


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_source_only_import_has_no_fastapi_or_api_activity(mode):
    env = os.environ.copy()
    env.update({
        "G2B_TEST_MODE": "1",
        "G2B_SOURCE_ONLY_RUNTIME": "0",
        "G2B_AUTO_SYNC_DISABLE": "1",
    })
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "g2b_heavy_worker", "--profile-imports", mode],
        cwd=os.getcwd(), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40,
    )
    assert proc.returncode == 0, proc.stdout[-1000:] + proc.stderr[-1000:]
    lines = [line for line in proc.stdout.splitlines()
             if line.startswith("G2B_LIGHT_SOURCE_IMPORT_PROFILE ")]
    assert len(lines) == 1
    data = json.loads(lines[0].split(" ", 1)[1])
    assert data["mode"] == mode
    assert data["source_only"] is True
    assert data["fastapi_loaded"] is False
    assert data["database_or_api_called"] is False
    assert 0 < data["rss_before_mib"] <= data["rss_peak_mib"] + 5
    assert 0 < data["rss_app_import_mib"] <= data["rss_peak_mib"] + 5


def test_unified_web_does_not_skip_backend_initialization():
    import inspect
    src = inspect.getsource(worker.main)
    assert "app.initialize_backend(force=True)" in src
    assert "app.initialize_isolated_source_backend_read_only(mode)" in src
    assert "memory_guard.wait_for_heavy_work_budget" in src


def test_child_read_only_preflight_fails_closed_in_web_role():
    # Never contact the database from an unqualified context.
    assert app.initialize_isolated_source_backend_read_only("shopping") is False
    assert app.initialize_isolated_source_backend_read_only("budget") is False
    assert app.initialize_isolated_source_backend_read_only("unknown") is False


def test_source_only_does_not_lower_admission_headroom():
    import safe_boot_vnext
    assert safe_boot_vnext.CHILD_MIN_SOFT_MIB == 96
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64
    state = {
        "guard_ok": True,
        "cgroup_oom_group": 0,
        "cgroup_limit_mib": 256,
        "cgroup_effective_mib": 113.3,
    }
    allowed, reason = safe_boot_vnext.child_admission(
        state, worker_soft_limit_mib=112
    )
    assert (allowed, reason) == (False, "CGRP_CHILD_HEADROOM_UNSAFE")
