"""G2B 4.1.242: total web budget read deadline and safe instrumentation."""
import inspect

import pytest

import budget_pg_store
import runtime_role
import vnext_clean_app


class FakePostgresConnection:
    class dialect:
        name = "postgresql"

    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))


def test_budget_deadline_caps_cumulative_postgres_statements(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(budget_pg_store.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(budget_pg_store, "_flag", lambda *args: False)
    monkeypatch.setattr(runtime_role, "runtime_role", lambda: "UNIFIED")
    conn = FakePostgresConnection()

    with budget_pg_store.budget_web_read_deadline(seconds=9.0):
        budget_pg_store._bound_budget_view_query(conn, 5500)
        assert conn.calls[-1][1]["ms"] == "5500ms"
        clock[0] = 106.5
        budget_pg_store._bound_budget_view_query(conn, 5500)
        assert conn.calls[-1][1]["ms"] == "2500ms"
        clock[0] = 108.6
        with pytest.raises(
            TimeoutError, match="G2B_BUDGET_WEB_READ_DEADLINE_EXCEEDED"
        ):
            budget_pg_store._bound_budget_view_query(conn, 5500)

    # Explicit context resets: other authenticated routes and source workers
    # retain their prior per-query timeout and cannot inherit this deadline.
    clock[0] = 120.0
    budget_pg_store._bound_budget_view_query(conn, 5500)
    assert conn.calls[-1][1]["ms"] == "5500ms"


def test_budget_deadline_nested_context_cannot_extend_outer(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(budget_pg_store.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(budget_pg_store, "_flag", lambda *args: False)
    monkeypatch.setattr(runtime_role, "runtime_role", lambda: "UNIFIED")
    conn = FakePostgresConnection()

    with budget_pg_store.budget_web_read_deadline(seconds=8.0):
        clock[0] = 107.0
        with budget_pg_store.budget_web_read_deadline(seconds=9.0):
            budget_pg_store._bound_budget_view_query(conn, 5500)
            assert conn.calls[-1][1]["ms"] == "1000ms"


def test_budget_deadline_does_not_touch_collector_or_sqlite(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(budget_pg_store.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(budget_pg_store, "_flag", lambda *args: False)
    monkeypatch.setattr(runtime_role, "runtime_role", lambda: "LOCAL_COLLECTOR")
    conn = FakePostgresConnection()
    with budget_pg_store.budget_web_read_deadline(seconds=1.0):
        clock[0] = 103.0
        budget_pg_store._bound_budget_view_query(conn, 5500)
    assert conn.calls == []

    monkeypatch.setattr(runtime_role, "runtime_role", lambda: "UNIFIED")
    conn.dialect.name = "sqlite"
    with budget_pg_store.budget_web_read_deadline(seconds=1.0):
        budget_pg_store._bound_budget_view_query(conn, 5500)
    assert conn.calls == []


def test_budget_page_total_time_guard_is_after_auth_and_memory_protection():
    source = inspect.getsource(vnext_clean_app.budget_page)
    assert source.index("user = require_user(request)") < source.index(
        "budget_web_read_deadline"
    )
    assert source.index("memory_guard.low_memory_web_hold()") < source.index(
        "budget_web_read_deadline"
    )
    assert "G2B_BUDGET_READ_MS" in source
    assert "G2B_BUDGET_RENDER_MS" in source
    assert "seconds=9.0 if not TEST_MODE and is_unified() else None" in source

