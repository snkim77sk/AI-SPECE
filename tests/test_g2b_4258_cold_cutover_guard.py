"""4.1.258: source-free cold handoff gate cannot overstate old PID verification."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "g2b_cold_cutover_guard.py"
spec = importlib.util.spec_from_file_location("g2b_4258_cutover", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def safe_env(monkeypatch):
    for key in (
        "G2B_TEST_MODE", "G2B_V41_FRESH_START",
        "G2B_DESTRUCTIVE_RESET_CONFIRM",
    ):
        monkeypatch.setenv(key, "0")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")


def test_no_operator_proof_always_blocks_even_if_pg_has_no_locks(monkeypatch):
    safe_env(monkeypatch)
    monkeypatch.setattr(gate, "_probe_advisory_locks", lambda: [])
    r = gate.inspect_cutover()
    assert not r["cutover_ready"]
    assert r["pg_advisory_probe_ok"] is True
    assert r["old_process_exited_machine_verified"] is False
    assert "CONFIRM_OLD_WEB_AND_ALL_COLLECTOR_PIDS_EXITED" in r["required_actions"]


@pytest.mark.parametrize("lock", gate.LOCK_NAMES)
def test_any_live_legacy_or_new_pg_advisory_lock_blocks(monkeypatch, lock):
    safe_env(monkeypatch)
    monkeypatch.setattr(gate, "_probe_advisory_locks", lambda: [lock])
    r = gate.inspect_cutover(operator_confirmed_old_stopped=True)
    assert not r["cutover_ready"]
    assert lock in r["pg_advisory_probe_busy"]
    assert "STOP_OLD_OR_CONCURRENT_COLLECTION_AND_RECHECK_PG" in r["required_actions"]


def test_explicit_operator_attestation_plus_all_clear_is_required(monkeypatch):
    safe_env(monkeypatch)
    monkeypatch.setattr(gate, "_probe_advisory_locks", lambda: [])
    report = gate.inspect_cutover(operator_confirmed_old_stopped=True)
    assert report["cutover_ready"] is True
    assert report["old_process_exited_machine_verified"] is False
    assert report["schema_ddl_performed"] is False
    assert report["business_rows_modified"] == 0
    assert report["source_api_calls"] == 0
    assert report["required_actions"] == []


@pytest.mark.parametrize("flag", ["G2B_V41_FRESH_START", "G2B_DESTRUCTIVE_RESET_CONFIRM"])
def test_destructive_stale_environment_blocks_even_with_operator_proof(monkeypatch, flag):
    safe_env(monkeypatch)
    monkeypatch.setenv(flag, "1")
    monkeypatch.setattr(gate, "_probe_advisory_locks", lambda: [])
    r = gate.inspect_cutover(operator_confirmed_old_stopped=True)
    assert not r["cutover_ready"]
    assert not r["destructive_flags_disabled"]
    assert "DISABLE_ALL_DESTRUCTIVE_RESET_FLAGS" in r["required_actions"]


def test_wrong_role_and_test_mode_cannot_produce_production_approval(monkeypatch):
    safe_env(monkeypatch)
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "RESULT_SERVER")
    monkeypatch.setattr(gate, "_probe_advisory_locks", lambda: (_ for _ in ()).throw(
        AssertionError("test mode never probes production database")
    ))
    r = gate.inspect_cutover(operator_confirmed_old_stopped=True)
    assert not r["cutover_ready"]
    assert not r["pg_advisory_probe_ok"]
    assert "DISABLE_TEST_MODE" in r["required_actions"]
    assert "USE_UNIFIED_RUNTIME_ROLE" in r["required_actions"]


def test_pg_connection_failure_never_fails_open_or_reveals_dsn(monkeypatch):
    safe_env(monkeypatch)
    monkeypatch.setenv(
        "G2B_DATABASE_URL",
        "postgresql://private:password@pg.secret.invalid:5432/g2b"
    )
    monkeypatch.setattr(gate, "_probe_advisory_locks", lambda: (_ for _ in ()).throw(
        ConnectionError("password and secret DB address")
    ))
    r = gate.inspect_cutover(operator_confirmed_old_stopped=True)
    assert not r["cutover_ready"]
    assert r["pg_advisory_error_code"] == "ConnectionError"
    rendered=json.dumps(r,ensure_ascii=False)
    assert "password" not in rendered
    assert "pg.secret.invalid" not in rendered
    assert "VERIFY_POSTGRES_ADVISORY_CONNECTION" in r["required_actions"]


def test_all_three_advisory_lock_names_are_checked_and_released(monkeypatch):
    safe_env(monkeypatch)
    from contextlib import closing
    class Cursor:
        def __init__(self, value):
            self.value=value
        def fetchone(self):
            return (self.value,)
    class Conn:
        def __init__(self):
            self.closed=False
            self.queries=[]
        def execute(self, sql, params):
            self.queries.append((sql,params[0]))
            return Cursor(True)
        def close(self):
            self.closed=True
    c=Conn()
    monkeypatch.setattr(gate.g2b_database, "_operational_lease_connection", lambda:c)
    assert gate._probe_advisory_locks() == []
    assert c.closed
    assert [p for sql,p in c.queries[:3]]==list(gate.LOCK_NAMES)
    assert "pg_try_advisory_lock" in c.queries[0][0]
    assert [p for sql,p in c.queries[3:]]==list(reversed(gate.LOCK_NAMES))
    assert "pg_advisory_unlock" in c.queries[-1][0]


def test_failed_probe_closes_connection_and_does_not_leave_own_locks(monkeypatch):
    safe_env(monkeypatch)
    class Conn:
        def __init__(self):
            self.closed=False
            self.queries=[]
        def execute(self, sql, params):
            self.queries.append((sql,params[0]))
            if "try_advisory_lock" in sql and params[0] == gate.LOCK_NAMES[1]:
                raise OSError("connection closed mid probe")
            class Cursor:
                def fetchone(self):return (True,)
            return Cursor()
        def close(self):self.closed=True
    c=Conn()
    monkeypatch.setattr(gate.g2b_database,"_operational_lease_connection",lambda:c)
    with pytest.raises(OSError):
        gate._probe_advisory_locks()
    assert c.closed
    assert any("pg_advisory_unlock" in sql for sql,p in c.queries)


def test_cli_needs_explicit_confirmation_and_prints_only_safe_fields(monkeypatch,capsys):
    safe_env(monkeypatch)
    monkeypatch.setattr(gate,"_probe_advisory_locks",lambda:[])
    assert gate.main([]) == 2
    assert gate.main(["--old-processes-stopped"]) == 0
    reports=[json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    assert reports[0]["cutover_ready"] is False
    assert reports[1]["cutover_ready"] is True
    assert all(r["old_process_exited_machine_verified"] is False for r in reports)


def test_existing_bootstrap_resume_and_source_gates_untouched():
    import inspect
    import g2b_database
    import safe_boot_vnext
    import vnext_clean_app
    import db
    script=SCRIPT.read_text(encoding="utf-8")
    assert "init_db()" not in script
    assert "storage_ready()" not in script
    assert "CREATE SCHEMA" not in script
    assert "DROP SCHEMA" not in script
    assert "_operational_lease_connection()" in script
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in inspect.getsource(db.init_db)
    assert "budget_boot_ok = bool(_boot_budget_storage.storage_ready())" in inspect.getsource(vnext_clean_app.initialize_backend)
    assert safe_boot_vnext.BOOT_GRACE_SECONDS == 300
