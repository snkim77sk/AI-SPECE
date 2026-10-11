"""4.1.280: PR stack must not get an unsafe green deployment preflight.

Unit tests use fake read-only PG advisory/schema probes. No real PG session,
source HTTP, schema init, business-row mutations, or deployment actions.
"""
import ast
import inspect
import json

import pytest

from scripts import g2b_integrated_release_gate as gate

GOOD_SHA = "a" * 40
OLD_SHA = "b" * 40


def _env(monkeypatch):
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_AUTO_SYNC_DISABLE", "0")
    monkeypatch.setenv("G2B_V41_FRESH_START", "0")
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", "0")


def _identity(*, platform=GOOD_SHA, checkout=GOOD_SHA,
              manual="", source="GITHUB_SHA", authoritative=True):
    return {
        "build_commit": platform,
        "build_commit_source": source,
        "build_commit_authoritative": authoritative,
        "git_checkout_commit": checkout,
        "configured_build_commit": manual,
        "build_commit_mismatch": bool(checkout and manual and checkout != manual),
    }


def _cold(*, ready=True, ddl=False, modified=0, source_calls=0):
    return {
        "cutover_ready": ready,
        "read_only": True,
        "schema_ddl_performed": ddl,
        "business_rows_modified": modified,
        "source_api_calls": source_calls,
        "pg_advisory_probe_ok": ready,
        "pg_advisory_probe_busy": [] if ready else ["held-global"],
        "old_process_exited_machine_verified": False,
    }


def _checkpoint(*, ready=True, ddl=False, modified=0, source_calls=0):
    return {
        "schema_checkpoint_compatible": ready,
        "read_only": True,
        "schema_ddl_performed": ddl,
        "business_rows_modified": modified,
        "source_api_calls": source_calls,
        "checkpoint_tables_checked": 2 if ready else 1,
        "marker_ok": ready,
    }


def _prepare(monkeypatch, *, before=None, schema=None, after=None,
             identity=None, manifest=True):
    _env(monkeypatch)
    monkeypatch.setattr(gate, "_release_manifest_matches", lambda: manifest)
    monkeypatch.setattr(
        gate, "build_commit_info",
        lambda: identity if identity is not None else _identity(),
    )
    sequence = []
    cold_results = iter([before or _cold(), after or _cold()])

    def probe_cold(*, operator_confirmed_old_stopped):
        assert operator_confirmed_old_stopped is True
        sequence.append("PG_ADVISORY")
        return next(cold_results)

    def probe_schema():
        sequence.append("PG_CHECKPOINT_SCHEMA")
        return schema if schema is not None else _checkpoint()

    monkeypatch.setattr(gate.cold, "inspect_cutover", probe_cold)
    monkeypatch.setattr(gate.checkpoint, "inspect_checkpoint_cutover", probe_schema)
    return sequence


def _call(**kw):
    return gate.inspect_integrated_release(
        reviewed_main_head=kw.pop("reviewed_main_head", GOOD_SHA),
        old_processes_stopped=kw.pop("old_processes_stopped", True),
        merged_main_confirmed=kw.pop("merged_main_confirmed", True),
        ci_simulation=kw.pop("ci_simulation", False),
        **kw,
    )


def test_sequential_two_advisory_checks_surround_checkpoint_schema(monkeypatch):
    seq = _prepare(monkeypatch)
    result = _call()
    assert seq == ["PG_ADVISORY", "PG_CHECKPOINT_SCHEMA", "PG_ADVISORY"]
    assert result["stages_checked"] == [
        "PG_ADVISORY_BEFORE", "PG_CHECKPOINT_SCHEMA", "PG_ADVISORY_AFTER",
    ]
    assert result["cutover_evidence_ready"] is True
    assert result["required_actions"] == []
    assert result["candidate_head"] == GOOD_SHA
    assert result["reviewed_main_head"] == GOOD_SHA
    assert result["manifest_consistent"] is True
    assert result["old_process_exited_machine_verified"] is False
    assert result["operator_attested_old_processes_stopped"] is True
    assert result["separate_github_pr_review_verified"] is False
    assert result["deployment_authorized"] is False
    assert result["deployment_performed"] is False
    assert result["business_rows_modified"] == 0
    assert result["source_api_calls"] == 0
    assert result["schema_ddl_performed"] is False


def test_default_run_stays_hold_and_opens_no_pg_even_with_valid_checkout(monkeypatch):
    seq = _prepare(monkeypatch)
    report = gate.inspect_integrated_release()
    assert report["cutover_evidence_ready"] is False
    assert report["stages_checked"] == []
    assert seq == []
    assert "SPECIFY_REVIEWED_40_CHARACTER_MAIN_SHA" in report["required_actions"]
    assert "MERGE_AND_REVIEW_CUMULATIVE_DRAFT_PRS_FIRST" in report["required_actions"]
    assert "CONFIRM_OLD_WEB_AND_ALL_CHILD_PIDS_EXITED" in report["required_actions"]


@pytest.mark.parametrize("reviewed", ["", GOOD_SHA[:12], "z"*40, OLD_SHA])
def test_invalid_or_wrong_release_sha_never_opens_pg(monkeypatch, reviewed):
    seq = _prepare(monkeypatch)
    result = _call(reviewed_main_head=reviewed)
    assert result["cutover_evidence_ready"] is False
    assert seq == []
    assert any(
        k in result["required_actions"]
        for k in (
            "SPECIFY_REVIEWED_40_CHARACTER_MAIN_SHA",
            "CANDIDATE_HEAD_MISMATCH",
        )
    )


@pytest.mark.parametrize("identity,error", [
    (_identity(platform="",checkout="",source="",authoritative=False),
     "OBSERVE_AUTHORITATIVE_BUILD_HEAD"),
    (_identity(platform=GOOD_SHA,checkout=GOOD_SHA,manual=OLD_SHA),
     "STALE_MANUAL_BUILD_COMMIT"),
    (_identity(platform=GOOD_SHA,checkout=OLD_SHA),
     "CHECKOUT_PLATFORM_HEAD_MISMATCH"),
    (_identity(platform=GOOD_SHA,checkout=GOOD_SHA,source="G2B_BUILD_COMMIT",
               authoritative=False), "VERIFY_DEPLOYABLE_CHECKOUT_OR_PLATFORM_SHA"),
])
def test_commit_provenance_is_fail_closed(monkeypatch, identity, error):
    seq = _prepare(monkeypatch, identity=identity)
    report = _call()
    assert report["cutover_evidence_ready"] is False
    assert error in report["required_actions"]
    assert seq == []


@pytest.mark.parametrize("env_name,value,error", [
    ("G2B_TEST_MODE", "1", "DISABLE_TEST_MODE"),
    ("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR", "USE_UNIFIED_RUNTIME_ROLE"),
    ("G2B_AUTO_SYNC_DISABLE", "1", "ENABLE_UNIFIED_AUTO_COLLECTION"),
    ("G2B_V41_FRESH_START", "1", "DISABLE_HISTORICAL_RESET_FLAGS"),
    ("G2B_DESTRUCTIVE_RESET_CONFIRM", "1", "DISABLE_HISTORICAL_RESET_FLAGS"),
])
def test_unsafe_rollout_environment_blocks_pg_before_probe(
    monkeypatch, env_name, value, error,
):
    seq = _prepare(monkeypatch)
    monkeypatch.setenv(env_name, value)
    report = _call()
    assert report["cutover_evidence_ready"] is False
    assert error in report["required_actions"]
    assert seq == []


def test_missing_main_merge_attestation_stays_hold(monkeypatch):
    seq = _prepare(monkeypatch)
    report = _call(merged_main_confirmed=False)
    assert "MERGE_AND_REVIEW_CUMULATIVE_DRAFT_PRS_FIRST" in report["required_actions"]
    assert not seq


def test_missing_old_web_pid_attestation_stays_hold(monkeypatch):
    seq = _prepare(monkeypatch)
    report = _call(old_processes_stopped=False)
    assert "CONFIRM_OLD_WEB_AND_ALL_CHILD_PIDS_EXITED" in report["required_actions"]
    assert not seq


def test_ci_simulation_flag_does_not_override_missing_merge_or_pid(monkeypatch):
    seq = _prepare(monkeypatch)
    report = _call(
        ci_simulation=True, merged_main_confirmed=False,
        old_processes_stopped=False,
    )
    assert report["ci_simulation"] is True
    assert report["cutover_evidence_ready"] is False
    assert report["deployment_authorized"] is False
    assert not seq


def test_manifest_version_mismatch_blocks_even_with_reviewed_sha(monkeypatch):
    seq = _prepare(monkeypatch, manifest=False)
    report = _call()
    assert report["cutover_evidence_ready"] is False
    assert "FIX_RELEASE_VERSION_MANIFEST" in report["required_actions"]
    assert seq == []


def test_pre_advisory_failure_skips_schema_and_never_claims_ready(monkeypatch):
    seq = _prepare(monkeypatch, before=_cold(ready=False))
    report = _call()
    assert not report["cutover_evidence_ready"]
    assert report["required_actions"] == ["COLD_CUTOVER_PRECHECK_HOLD"]
    assert seq == ["PG_ADVISORY"]


def test_schema_failure_skips_final_advisory_check(monkeypatch):
    seq = _prepare(monkeypatch, schema=_checkpoint(ready=False))
    report = _call()
    assert not report["cutover_evidence_ready"]
    assert report["required_actions"] == ["CHECKPOINT_SCHEMA_OR_MARKER_HOLD"]
    assert seq == ["PG_ADVISORY", "PG_CHECKPOINT_SCHEMA"]


def test_newly_busy_lease_after_schema_check_fails_final_gate(monkeypatch):
    seq = _prepare(monkeypatch, after=_cold(ready=False))
    report = _call()
    assert not report["cutover_evidence_ready"]
    assert report["required_actions"] == ["COLD_CUTOVER_POSTCHECK_HOLD"]
    assert seq == ["PG_ADVISORY", "PG_CHECKPOINT_SCHEMA", "PG_ADVISORY"]


@pytest.mark.parametrize("step,override", [
    ("before", _cold(ddl=True)),
    ("before", _cold(modified=1)),
    ("schema", _checkpoint(source_calls=1)),
    ("schema", _checkpoint(ddl=True)),
    ("after", _cold(modified=1)),
])
def test_subgates_cannot_mark_mutating_operation_read_only(monkeypatch, step, override):
    kwargs = {step: override}
    seq = _prepare(monkeypatch, **kwargs)
    report = _call()
    assert not report["cutover_evidence_ready"]
    assert report["required_actions"]
    assert len(seq) >= 1


def test_exception_messages_do_not_leak_database_host_keys_or_secrets(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        gate.checkpoint, "inspect_checkpoint_cutover",
        lambda: (_ for _ in ()).throw(
            RuntimeError("postgresql://private:key@secret-host?apiKey=SECRET")
        ),
    )
    report = _call()
    rendered = json.dumps(report)
    assert not report["cutover_evidence_ready"]
    assert report["required_actions"] == ["INTEGRATED_CUTOVER_CHECK_FAILED_RuntimeError"]
    assert "secret-host" not in rendered
    assert "SECRET" not in rendered


def test_existing_preflight_may_init_schema_but_new_gate_never_imports_it():
    import scripts.g2b_deployment_preflight as preflight
    source = inspect.getsource(preflight.run_preflight)
    guarded = inspect.getsource(gate.inspect_integrated_release)
    assert "init_db()" in source
    assert "g2b_deployment_preflight" not in guarded
    # Inspect executable calls, not documentation/comments that mention
    # init_db() only to warn operators about the old mutable preflight.
    calls = [
        node.func.id if isinstance(node.func, ast.Name)
        else node.func.attr if isinstance(node.func, ast.Attribute)
        else ""
        for node in ast.walk(ast.parse(guarded))
        if isinstance(node, ast.Call)
    ]
    assert not set(calls) & {
        "init_db", "ensure_schema", "create_all", "drop_all",
        "execute", "executemany", "executescript", "run_preflight",
    }


def test_cli_produces_sanitized_json_and_distinct_hold_exit_code(monkeypatch, capsys):
    _prepare(monkeypatch)
    assert gate.main([]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["cutover_evidence_ready"] is False
    assert report["source_api_calls"] == 0
    assert report["business_rows_modified"] == 0
    assert report["deployment_performed"] is False
