"""G2B 4.1.273: old checkpoint ABI read-only cold-cutover diagnostic."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("g2b_4273_checkpoint_guard",
                               ROOT / "scripts" / "g2b_checkpoint_cutover_guard.py")
guard = module_from_spec(spec)
spec.loader.exec_module(guard)


class Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Conn:
    def __init__(self):
        self.closed = False
        self.commands = []
        self.marker = guard.EXPECTED_MARKER
        self.cols = {
            ("g2b_meta", "release_bootstrap"): {"key", "value", "applied_at"},
            ("g2b_app", "collection_checkpoints"): set(guard.CHECKPOINT_COLUMNS),
            ("g2b_budget", "budget_collection_checkpoints"): set(guard.CHECKPOINT_COLUMNS),
        }
        self.pk = {
            ("g2b_app", "collection_checkpoints"): guard.EXPECTED_PK,
            ("g2b_budget", "budget_collection_checkpoints"): guard.EXPECTED_PK,
        }

    def execute(self, statement, params=None):
        assert isinstance(statement, str), "sample SQL must be monkeypatched"
        self.commands.append((statement, params))
        if statement.startswith("SET "):
            return Result([])
        if "FROM information_schema.columns" in statement:
            return Result([(col,) for col in sorted(self.cols.get(tuple(params), ()))])
        if "FROM information_schema.table_constraints" in statement:
            return Result([(col,) for col in self.pk.get(tuple(params), ())])
        if 'SELECT value FROM "g2b_meta"' in statement:
            return Result([(self.marker,)] if self.marker else [])
        raise AssertionError("unapproved SQL " + statement)

    def close(self):
        self.closed = True


def configured(monkeypatch, conn=None):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_V41_FRESH_START", "0")
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", "0")
    connection = conn or Conn()
    monkeypatch.setattr(guard, "_open_read_only_connection", lambda: connection)
    monkeypatch.setattr(guard.g2b_database, "validate_schema_layout",
                        lambda: ("g2b_app", "g2b_budget"))
    seen = []
    monkeypatch.setattr(
        guard, "_one_checkpoint_sample",
        lambda _conn, schema, table: seen.append((schema, table)) or True,
    )
    return connection, seen


def test_complete_old_checkpoint_schema_is_read_only_and_two_samples(monkeypatch):
    conn, seen = configured(monkeypatch)
    result = guard.inspect_checkpoint_cutover()
    assert result["schema_checkpoint_compatible"] is True
    assert result["marker_ok"] is True
    assert result["checkpoint_tables_checked"] == 2
    assert result["checkpoint_sample_rows"] == 2
    assert result["read_only"] is True
    assert result["business_rows_modified"] == 0
    assert result["source_api_calls"] == 0
    assert seen == [
        ("g2b_app", "collection_checkpoints"),
        ("g2b_budget", "budget_collection_checkpoints"),
    ]
    assert conn.closed
    assert len(conn.commands) <= 10
    assert all(sql.startswith(("SELECT ", "SET ")) for sql, _ in conn.commands)


@pytest.mark.parametrize("label,schema,table", [
    ("APP", "g2b_app", "collection_checkpoints"),
    ("BUDGET", "g2b_budget", "budget_collection_checkpoints"),
])
def test_missing_checkpoint_column_fails_closed(monkeypatch, label, schema, table):
    conn, seen = configured(monkeypatch)
    conn.cols[(schema, table)].remove("last_page_fingerprint")
    result = guard.inspect_checkpoint_cutover()
    assert result["schema_checkpoint_compatible"] is False
    assert f"{label}_CHECKPOINT_COLUMNS_INCOMPATIBLE" in result["issues"]
    assert len(seen) == 1
    assert conn.closed


@pytest.mark.parametrize("label,schema,table", [
    ("APP", "g2b_app", "collection_checkpoints"),
    ("BUDGET", "g2b_budget", "budget_collection_checkpoints"),
])
def test_wrong_primary_key_must_block_resume(monkeypatch, label, schema, table):
    conn, _ = configured(monkeypatch)
    conn.pk[(schema, table)] = ("dataset",)
    result = guard.inspect_checkpoint_cutover()
    assert f"{label}_CHECKPOINT_PK_INCOMPATIBLE" in result["issues"]
    assert result["schema_checkpoint_compatible"] is False


def test_missing_marker_fails_closed_without_creating_one(monkeypatch):
    conn, _ = configured(monkeypatch)
    conn.marker = ""
    result = guard.inspect_checkpoint_cutover()
    assert "RELEASE_MARKER_MISMATCH_OR_MISSING" in result["issues"]
    assert result["schema_checkpoint_compatible"] is False
    assert all(sql.startswith(("SELECT ", "SET ")) for sql, _ in conn.commands)


def test_missing_schema_table_fails_closed(monkeypatch):
    conn, _ = configured(monkeypatch)
    conn.cols.pop(("g2b_budget", "budget_collection_checkpoints"))
    report = guard.inspect_checkpoint_cutover()
    assert not report["schema_checkpoint_compatible"]
    assert "BUDGET_CHECKPOINT_TABLE_MISSING" in report["issues"]


@pytest.mark.parametrize("bad_flag", [
    "G2B_TEST_MODE", "G2B_V41_FRESH_START",
    "G2B_DESTRUCTIVE_RESET_CONFIRM",
])
def test_old_test_or_destructive_flags_prevent_pg_access(monkeypatch, bad_flag):
    conn, _ = configured(monkeypatch)
    monkeypatch.setenv(bad_flag, "1")
    report = guard.inspect_checkpoint_cutover()
    assert report["schema_checkpoint_compatible"] is False
    assert not conn.commands
    assert not conn.closed


def test_connection_failure_masks_sensitive_pg_credentials(monkeypatch):
    configured(monkeypatch)
    monkeypatch.setattr(
        guard, "_open_read_only_connection",
        lambda: (_ for _ in ()).throw(
            RuntimeError("password=private host=pg.internal.invalid")
        ),
    )
    report = guard.inspect_checkpoint_cutover()
    rendered = json.dumps(report)
    assert report["schema_checkpoint_compatible"] is False
    assert "POSTGRES_CHECK_FAILED_RuntimeError" in report["issues"]
    assert "password" not in rendered
    assert "pg.internal.invalid" not in rendered


def test_invalid_checkpoint_sample_cannot_admit_cutover(monkeypatch):
    conn, _ = configured(monkeypatch)
    monkeypatch.setattr(
        guard, "_one_checkpoint_sample",
        lambda *_args: (_ for _ in ()).throw(
            ValueError("G2B_CHECKPOINT_SAMPLE_INVALID")
        ),
    )
    report = guard.inspect_checkpoint_cutover()
    assert not report["schema_checkpoint_compatible"]
    assert "POSTGRES_CHECK_FAILED_ValueError" in report["issues"]
    assert conn.closed
