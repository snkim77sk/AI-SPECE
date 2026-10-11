"""G2B 4.1.271: no legacy flag can erase existing PostgreSQL or SQLite.

All tests are source-free and use an isolated in-memory PostgreSQL transcript.
No production database, API or checkpoint is touched.
"""
import ast
import inspect
import re

import pytest

import v41_fresh_start as fresh


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def first(self):
        return (self.value,) if self.value is not None else None

    def scalar(self):
        return self.value


class FakeConnection:
    def __init__(self, *, schemas=(), marker_table=False, marker_value=None):
        self.schemas = set(schemas)
        self.marker_table = bool(marker_table)
        self.marker_value = marker_value
        self.sql = []
        self.checkpoints = {"budget:2026": {"page": 41, "amount": 100}}
        self.business_rows = {"LED-2026": "preserve"}

    @property
    def dialect(self):
        class Dialect:
            class identifier_preparer:
                @staticmethod
                def quote_schema(value):
                    return '"' + str(value).replace('"', '""') + '"'

        return Dialect()

    def execute(self, statement, params=None):
        query = str(statement)
        self.sql.append(query)
        if "pg_advisory_xact_lock" in query:
            return FakeResult(True)
        if "FROM pg_namespace" in query:
            return FakeResult(1 if params["schema"] in self.schemas else None)
        if "FROM pg_class" in query:
            return FakeResult(1 if self.marker_table else None)
        if "SELECT value FROM" in query:
            return FakeResult(self.marker_value)
        if query.startswith("CREATE SCHEMA "):
            name = re.search(r'CREATE SCHEMA "([^"]+)"', query)
            assert name, query
            self.schemas.add(name.group(1))
            return FakeResult()
        if query.startswith("CREATE TABLE IF NOT EXISTS "):
            self.marker_table = True
            return FakeResult()
        if query.startswith("INSERT INTO "):
            if self.marker_value is None:
                self.marker_value = params["value"]
            return FakeResult()
        raise AssertionError("unexpected SQL in preservation gate: " + query)


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        connection = self.connection

        class Transaction:
            def __enter__(self):
                return connection

            def __exit__(self, exc_type, exc, tb):
                return False

        return Transaction()


def _setup(monkeypatch, connection, *, sqlite=()):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(fresh.g2b_database, "validate_schema_layout",
                        lambda: ("g2b_app", "g2b_budget"))
    monkeypatch.setattr(fresh.g2b_database, "engine", lambda: FakeEngine(connection))
    monkeypatch.setattr(fresh, "LEGACY_SQLITE_PATHS", tuple(str(x) for x in sqlite))


def _assert_preserved(connection, *, sql_count=None):
    assert connection.checkpoints == {"budget:2026": {"page": 41, "amount": 100}}
    assert connection.business_rows == {"LED-2026": "preserve"}
    if sql_count is not None:
        assert len(connection.sql) == sql_count
    forbidden = ("DROP ", "TRUNCATE ", "DELETE FROM", "ALTER TABLE", "UPDATE ")
    assert not any(
        any(part in sql.upper() for part in forbidden)
        for sql in connection.sql
    ), connection.sql


@pytest.mark.parametrize("fresh_flag,confirm_flag", [
    ("0", "0"), ("1", "0"), ("0", "1"), ("1", "1"),
])
@pytest.mark.parametrize("schemas", [
    ("g2b_app",), ("g2b_budget",), ("g2b_app", "g2b_budget"),
])
def test_existing_schema_missing_marker_refuses_all_old_reset_flags(
    monkeypatch, fresh_flag, confirm_flag, schemas,
):
    connection = FakeConnection(schemas=schemas)
    _setup(monkeypatch, connection)
    monkeypatch.setenv("G2B_V41_FRESH_START", fresh_flag)
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", confirm_flag)
    with pytest.raises(RuntimeError, match="G2B_V41_EXISTING_STORAGE_PROTECTED"):
        fresh.prepare_v41_storage()
    assert connection.schemas == set(schemas)
    assert "g2b_meta" not in connection.schemas
    assert not connection.marker_table
    _assert_preserved(connection)


@pytest.mark.parametrize("fresh_flag,confirm_flag", [
    ("0", "0"), ("1", "0"), ("0", "1"), ("1", "1"),
])
def test_completed_marker_never_removes_legacy_file_even_with_both_flags(
    monkeypatch, tmp_path, fresh_flag, confirm_flag,
):
    legacy = tmp_path / "g2b.sqlite3"
    legacy.write_bytes(b"LEGACY_CHECKPOINT_PRESERVE")
    connection = FakeConnection(
        schemas=("g2b_meta", "g2b_app", "g2b_budget"),
        marker_table=True,
        marker_value=fresh.MARKER_VALUE,
    )
    _setup(monkeypatch, connection, sqlite=(legacy,))
    monkeypatch.setenv("G2B_V41_FRESH_START", fresh_flag)
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", confirm_flag)
    result = fresh.prepare_v41_storage()
    assert result["status"] == "SKIPPED"
    assert result["reset"] is False
    assert result["legacy_sqlite_removed"] == []
    assert legacy.read_bytes() == b"LEGACY_CHECKPOINT_PRESERVE"
    _assert_preserved(connection)
    assert not any("CREATE " in q.upper() for q in connection.sql)


@pytest.mark.parametrize("flag", [("0", "0"), ("1", "1")])
def test_unmarked_legacy_sqlite_alone_blocks_empty_postgres_boot(
    monkeypatch, tmp_path, flag,
):
    legacy = tmp_path / "g2b-vnext.sqlite3"
    legacy.write_bytes(b"LEGACY_UNMIGRATED_DATA")
    connection = FakeConnection()
    _setup(monkeypatch, connection, sqlite=(legacy,))
    monkeypatch.setenv("G2B_V41_FRESH_START", flag[0])
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", flag[1])
    with pytest.raises(RuntimeError, match="G2B_V41_EXISTING_STORAGE_PROTECTED"):
        fresh.prepare_v41_storage()
    assert connection.schemas == set()
    assert legacy.read_bytes() == b"LEGACY_UNMIGRATED_DATA"
    _assert_preserved(connection)


def test_clean_empty_pg_boot_only_adds_schemas_and_marker(monkeypatch):
    connection = FakeConnection()
    _setup(monkeypatch, connection)
    monkeypatch.setenv("G2B_V41_FRESH_START", "0")
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", "0")
    initial = fresh.prepare_v41_storage()
    assert initial["status"] == "COMPLETE"
    assert initial["reset"] is False
    assert initial["marker_value"] == fresh.MARKER_VALUE
    assert connection.schemas == {"g2b_app", "g2b_budget", "g2b_meta"}
    assert connection.marker_value == fresh.MARKER_VALUE
    assert connection.marker_table is True
    _assert_preserved(connection)
    next_result = fresh.prepare_v41_storage()
    assert next_result["status"] == "SKIPPED"
    assert next_result["reset"] is False
    _assert_preserved(connection)


def test_missing_marker_table_is_looked_up_without_sql_ddl(monkeypatch):
    connection = FakeConnection(schemas=("g2b_meta",), marker_table=False)
    _setup(monkeypatch, connection)
    assert fresh._marker(connection) is None
    assert all(sql.startswith("SELECT ") for sql in connection.sql)
    _assert_preserved(connection)


def test_unknown_marker_never_rewritten_or_schema_repaired(monkeypatch):
    connection = FakeConnection(
        schemas=("g2b_meta", "g2b_app", "g2b_budget"),
        marker_table=True, marker_value="FUTURE_UNKNOWN_CONTRACT",
    )
    _setup(monkeypatch, connection)
    monkeypatch.setenv("G2B_V41_FRESH_START", "1")
    monkeypatch.setenv("G2B_DESTRUCTIVE_RESET_CONFIRM", "1")
    with pytest.raises(RuntimeError, match="G2B_V41_FRESH_START_MARKER_MISMATCH"):
        fresh.prepare_v41_storage()
    assert connection.marker_value == "FUTURE_UNKNOWN_CONTRACT"
    _assert_preserved(connection)


def test_conflicting_marker_insert_fails_closed_without_overwrite(monkeypatch):
    connection = FakeConnection(schemas=("g2b_meta",), marker_table=True)
    _setup(monkeypatch, connection)
    connection.marker_value = "FOREIGN_VALUE"
    with pytest.raises(RuntimeError, match="G2B_V41_FRESH_START_MARKER_MISMATCH"):
        fresh._write_marker(connection, fresh.MARKER_VALUE)
    assert connection.marker_value == "FOREIGN_VALUE"
    assert any("ON CONFLICT(key) DO NOTHING" in q for q in connection.sql)


def test_bootstrap_source_has_no_native_destructive_operations():
    module_source = inspect.getsource(fresh)
    tree = ast.parse(module_source)
    forbidden = {"remove", "unlink", "rmdir", "removedirs", "rmtree"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden, node.func.attr
    assert "DROP SCHEMA" not in module_source.upper()
    assert "TRUNCATE " not in module_source.upper()
