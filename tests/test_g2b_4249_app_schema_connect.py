"""4.1.249: one PostgreSQL SET search_path per checkout; DDL only at boot."""

from contextlib import contextmanager
import inspect
from types import SimpleNamespace

import pytest

import db
import g2b_database
import vnext_clean_db


class FakeConnection:
    def __init__(self, calls):
        self.calls = calls
        self.dialect = SimpleNamespace(
            identifier_preparer=SimpleNamespace(
                quote_schema=lambda value: f'"{value}"'
            ),
        )

    def exec_driver_sql(self, sql, params=()):
        self.calls.append((str(sql), tuple(params)))
        if "pg_namespace" in str(sql) or "CREATE SCHEMA" in str(sql):
            raise AssertionError("web connection must never inspect/create schema")
        return self

    def commit(self):
        self.calls.append(("commit", ()))

    def rollback(self):
        self.calls.append(("rollback", ()))

    def close(self):
        self.calls.append(("close", ()))


def test_pg_connect_uses_only_search_path_without_namespace_or_ddl(monkeypatch):
    calls = []
    conn = FakeConnection(calls)
    monkeypatch.setattr(db, "_use_sqlite", lambda: False)
    monkeypatch.setattr(g2b_database, "app_schema", lambda: "g2b_app")
    monkeypatch.setattr(
        g2b_database, "engine",
        lambda: SimpleNamespace(connect=lambda: conn),
    )

    # The same connection can be recycled by the production pool; apply only
    # SET search_path per checkout, with no schema existence query or DDL.
    for _ in range(3):
        with db.connect() as wrapped:
            assert isinstance(wrapped, db._PgCompatConnection)

    assert calls == [
        ("SET search_path TO \"g2b_app\", public", ()), ("commit", ()), ("close", ()),
        ("SET search_path TO \"g2b_app\", public", ()), ("commit", ()), ("close", ()),
        ("SET search_path TO \"g2b_app\", public", ()), ("commit", ()), ("close", ()),
    ]


def test_pg_init_db_explicit_schema_bootstrap_before_app_tables(monkeypatch):
    events = []
    monkeypatch.setattr(db, "_use_sqlite", lambda: False)
    monkeypatch.setattr(g2b_database, "app_schema", lambda: "g2b_app")
    monkeypatch.setattr(
        g2b_database,
        "ensure_schema",
        lambda name: events.append(("ensure_schema", name)),
    )

    class FakeCompat:
        def executescript(self, ddl):
            assert "CREATE TABLE IF NOT EXISTS app_settings" in ddl
            assert "CREATE TABLE IF NOT EXISTS vnext_source_credentials" in ddl
            events.append(("create_app_tables", True))

    @contextmanager
    def fake_connect():
        events.append(("connect", True))
        yield FakeCompat()

    monkeypatch.setattr(db, "connect", fake_connect)
    db.init_db()
    assert events == [
        ("ensure_schema", "g2b_app"),
        ("connect", True),
        ("create_app_tables", True),
    ]


def test_pg_init_db_schema_failure_prevents_undefined_search_path(monkeypatch):
    monkeypatch.setattr(db, "_use_sqlite", lambda: False)
    monkeypatch.setattr(g2b_database, "app_schema", lambda: "g2b_app")
    monkeypatch.setattr(
        g2b_database, "ensure_schema",
        lambda _schema: (_ for _ in ()).throw(
            RuntimeError("G2B_APP_SCHEMA_CREATE_FAILED")
        ),
    )
    monkeypatch.setattr(
        db, "connect", lambda: (_ for _ in ()).throw(
            AssertionError("must not query missing schema")
        ),
    )
    with pytest.raises(RuntimeError, match="G2B_APP_SCHEMA_CREATE_FAILED"):
        db.init_db()


def test_test_sqlite_init_does_not_touch_postgres_schema(monkeypatch):
    events = []
    monkeypatch.setattr(db, "_use_sqlite", lambda: True)
    monkeypatch.setattr(db, "_flag", lambda *_: False)
    monkeypatch.setattr(
        g2b_database, "ensure_schema",
        lambda _schema: (_ for _ in ()).throw(
            AssertionError("SQLite fixture must not connect to PostgreSQL")
        ),
    )

    class FakeCompat:
        def execute(self, statement):
            events.append(str(statement))

        def executescript(self, script):
            events.append("CREATE TABLE IF NOT EXISTS app_settings" in script)

    @contextmanager
    def fake_connect():
        yield FakeCompat()

    monkeypatch.setattr(db, "connect", fake_connect)
    db.init_db()
    assert events == ["PRAGMA synchronous=NORMAL", True]


def test_clean_backend_boot_has_required_explicit_app_schema_installer():
    source = inspect.getsource(vnext_clean_db.ensure_clean_schema)
    assert source.index("db.init_db()") < source.index("ensure_foundation()")
    installer = inspect.getsource(db.init_db)
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in installer
    reader = inspect.getsource(db._ensure_app_schema_on_connection)
    assert "pg_namespace" not in reader.replace(
        "avoid pg_namespace lookup", ""
    )
    assert "CREATE SCHEMA" not in reader.replace(
        "and CREATE SCHEMA", ""
    )
    assert "SET search_path TO" in reader
