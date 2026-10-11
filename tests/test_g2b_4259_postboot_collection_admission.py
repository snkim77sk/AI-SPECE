"""4.1.259: cumulative rollout must not report source readiness from HTTP alone."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "g2b_postboot_collection_admission.py"
spec = importlib.util.spec_from_file_location("g2b_4259_admission", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def state(*, limit=256, effective=95, rss=93.7, ok=True, oom_group=0):
    return {
        "guard_ok": bool(ok), "heavy_work_ok": bool(ok),
        "rss_mib": rss,
        "cgroup_limit_mib": limit,
        "cgroup_effective_mib": effective,
        "cgroup_oom_group": oom_group,
    }


def assess(s, **kwargs):
    return gate.assess_postboot_admission(
        s, role=kwargs.pop("role", "UNIFIED"),
        auto_sync_enabled=kwargs.pop("auto_sync_enabled", True),
        web_grace_operator_confirmed=kwargs.pop(
            "web_grace_operator_confirmed", True
        ),
        **kwargs,
    )


def test_256mib_sample_at_95mib_cannot_start_default_112mib_worker():
    result = assess(state())
    assert result["collection_execution_mode"] == "ISOLATED"
    assert result["current_admission_reason"] == "CGRP_CHILD_HEADROOM_UNSAFE"
    assert result["current_admission_allowed"] is False
    assert result["automatic_collection_deployment_ready"] is False
    assert result["worker_soft_limit_mib"] == 112
    assert result["worker_headroom_reserve_mib"] == 64
    assert result["max_prelaunch_effective_mib_exclusive"] == 80
    assert "AUTO_COLLECTION_WORKER_MEMORY_ADMISSION_HOLD" in result["required_actions"]


@pytest.mark.parametrize("effective,allowed", [(79.99,True),(80,False),(93.7,False),(155,False)])
def test_256mib_default_admission_exact_strict_threshold(effective, allowed):
    result = assess(state(effective=effective))
    assert result["current_admission_allowed"] is allowed
    assert result["automatic_collection_deployment_ready"] is allowed


def test_larger_known_cgroup_uses_isolated_worker_guard():
    allowed = assess(state(limit=512, effective=240))
    assert allowed["automatic_collection_deployment_ready"]
    assert allowed["collection_execution_mode"] == "ISOLATED"
    blocked = assess(state(limit=512, effective=450, ok=False))
    assert not blocked["current_admission_allowed"]
    assert blocked["current_admission_reason"] == "MEMORY_GUARD_HELD"


def test_unknown_cgroup_limit_fails_closed_without_fetching_sources():
    result = assess(state(limit=0, effective=0))
    assert result["current_admission_reason"] == "CGROUP_LIMIT_UNAVAILABLE"
    assert not result["automatic_collection_deployment_ready"]


def test_guard_or_oom_group_fails_closed_even_with_available_memory():
    assert not assess(state(effective=30, ok=False))["current_admission_allowed"]
    assert not assess(state(effective=30, oom_group=1))["current_admission_allowed"]


def test_no_grace_attestation_never_proves_worker_ready():
    result = assess(state(effective=60), web_grace_operator_confirmed=False)
    assert result["current_admission_allowed"] is True
    assert not result["automatic_collection_deployment_ready"]
    assert result["web_grace_elapsed_machine_verified"] is False
    assert "CONFIRM_WEB_BOOT_300_SECOND_GRACE_ELAPSED" in result["required_actions"]


@pytest.mark.parametrize("role,auto,test", [
    ("RESULT_SERVER",True,False),
    ("LOCAL_COLLECTOR",True,False),
    ("UNIFIED",False,False),
    ("UNIFIED",True,True),
])
def test_policy_disabled_or_wrong_runtime_never_reports_ready(role,auto,test):
    result = assess(
        state(effective=60), role=role,
        auto_sync_enabled=auto, test_mode=test,
    )
    assert not result["automatic_collection_deployment_ready"]


def test_invalid_worker_env_is_bounded_as_production_code(monkeypatch):
    monkeypatch.setenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB","99999")
    assert gate._worker_soft_limit() == 160
    monkeypatch.setenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB","malformed")
    assert gate._worker_soft_limit() == 112
    monkeypatch.setenv("G2B_ISOLATED_WORKER_SOFT_LIMIT_MB","-10")
    assert gate._worker_soft_limit() == 96


def test_memory_probe_failure_returns_sanitized_hold(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.delenv("G2B_AUTO_SYNC_DISABLE",raising=False)
    monkeypatch.setattr(gate.memory_guard, "snapshot",lambda **kw: (
        _ for _ in ()
    ).throw(RuntimeError("sensitive db.example local secret")))
    report = gate.inspect_postboot(web_grace_operator_confirmed=True)
    assert not report["automatic_collection_deployment_ready"]
    assert report["memory_probe_error_code"] == "RuntimeError"
    assert "sensitive" not in repr(report)
    assert "FIX_READ_ONLY_CGROUP_MEMORY_PROBE" in report["required_actions"]


def test_cli_only_passes_real_headroom_and_explicit_grace(monkeypatch,capsys):
    monkeypatch.setattr(gate, "inspect_postboot",lambda **kwargs: {
        "automatic_collection_deployment_ready":bool(
            kwargs["web_grace_operator_confirmed"]
        )
    })
    assert gate.main([]) == 2
    assert gate.main(["--web-grace-elapsed"]) == 0
    assert len(capsys.readouterr().out.splitlines())==2


def test_no_source_api_db_or_reduced_memory_reserve():
    script=SCRIPT.read_text(encoding="utf-8")
    assert "import budget_storage" not in script
    assert "init_db(" not in script
    assert "DROP SCHEMA" not in script
    assert "CREATE SCHEMA" not in script
    assert "urllib.request" not in script
    import safe_boot_vnext
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64
    assert safe_boot_vnext.child_admission(
        state(effective=95),worker_soft_limit_mib=112
    ) == (False,"CGRP_CHILD_HEADROOM_UNSAFE")
