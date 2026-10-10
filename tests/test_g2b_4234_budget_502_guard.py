"""Safe, read-only Cafe24 budget-screen guards for 4.1.234.

No production database, source API, or schema-reset access is required.
"""
import inspect

from fastapi.responses import HTMLResponse
from starlette.requests import Request

import budget_pg_store
import runtime_role
import vnext_clean_app


def _request(path="/budget"):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "query_string": b"", "headers": [],
    })


def test_budget_web_memory_guard_blocks_tight_256mb_cgroup():
    guard = vnext_clean_app._budget_web_pressure_hold
    assert guard({
        "guard_ok": True, "cgroup_limit_mib": 256,
        "cgroup_effective_mib": 210, "rss_mib": 100,
    })
    assert guard({
        "guard_ok": True, "cgroup_limit_mib": 256,
        "cgroup_effective_mib": 100, "rss_mib": 148,
    })
    assert not guard({
        "guard_ok": True, "cgroup_limit_mib": 256,
        "cgroup_effective_mib": 105, "rss_mib": 85,
    })


def test_budget_click_does_not_start_budget_queries_when_cgroup_unhealthy(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "require_user", lambda request: {"username": "operator"})
    monkeypatch.setattr(app.memory_guard, "low_memory_web_hold", lambda: True)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **_: {
        "guard_ok": True, "rss_mib": 147,
        "cgroup_limit_mib": 256, "cgroup_effective_mib": 200,
    })
    monkeypatch.setattr(
        app, "layout",
        lambda title, body, tab, user: HTMLResponse(content=body),
    )
    result = app.budget_page(_request())
    assert result.status_code == 200
    assert "예산 조회 잠시 대기".encode("utf-8") in result.body
    assert "다시 조회".encode("utf-8") in result.body


def test_budget_click_remains_authenticated_before_memory_guard(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "require_user", lambda request: None)
    monkeypatch.setattr(
        app.memory_guard, "low_memory_web_hold",
        lambda: (_ for _ in ()).throw(AssertionError("unauthenticated budget guard")),
    )
    result = app.budget_page(_request())
    assert result.status_code == 302


def test_read_only_pg_metadata_avoids_ddl_and_reuses_shared_engine(monkeypatch):
    store = budget_pg_store
    shared_engine = object()
    calls = []
    monkeypatch.setattr(store, "_flag", lambda name, default=False: False)
    monkeypatch.setattr(store.g2b_database, "engine", lambda: shared_engine)
    monkeypatch.setattr(store, "_safe_schema", lambda: "g2b_budget")
    monkeypatch.setattr(store, "_build_tables", lambda schema: calls.append(schema) or {"tables": 1})
    monkeypatch.setattr(store, "_READ_ONLY_TABLES", None)
    monkeypatch.setattr(store, "_READ_ONLY_SCHEMA", None)
    monkeypatch.setattr(
        store, "_engine_and_tables",
        lambda: (_ for _ in ()).throw(AssertionError("migration path invoked")),
    )
    first_engine, first_tables = store._read_only_engine_and_tables()
    second_engine, second_tables = store._read_only_engine_and_tables()
    assert first_engine is second_engine is shared_engine
    assert first_tables is second_tables
    assert calls == ["g2b_budget"]
    source = inspect.getsource(store._read_only_engine_and_tables)
    assert ".create_all(" not in source
    assert "_ensure_declared_indexes" not in source
    assert "_verify_table_contract" not in source


def test_pg_budget_query_has_transaction_local_timeout_and_workmem(monkeypatch):
    monkeypatch.setattr(budget_pg_store, "_flag", lambda name, default=False: False)
    monkeypatch.setattr(runtime_role, "runtime_role", lambda: "UNIFIED")
    calls = []

    class Conn:
        class dialect:
            name = "postgresql"

        def execute(self, query, params):
            calls.append((str(query), params))

    budget_pg_store._bound_budget_view_query(Conn(), 4600)
    assert len(calls) == 1
    assert "set_config('statement_timeout'" in calls[0][0]
    assert "set_config('work_mem'" in calls[0][0]
    assert calls[0][1]["ms"] == "4600ms"


def test_pg_budget_query_guard_skips_sqlite_and_collection_workers(monkeypatch):
    monkeypatch.setattr(budget_pg_store, "_flag", lambda name, default=False: False)
    calls = []

    class Conn:
        class dialect:
            name = "sqlite"

        def execute(self, *args, **kwargs):
            calls.append(True)

    budget_pg_store._bound_budget_view_query(Conn(), 4500)
    assert not calls

    Conn.dialect.name = "postgresql"
    monkeypatch.setattr(runtime_role, "runtime_role", lambda: "LOCAL_COLLECTOR")
    budget_pg_store._bound_budget_view_query(Conn(), 4500)
    assert not calls


def test_pg_budget_screen_select_paths_never_trigger_first_use_migration():
    for name in (
        "current_organization_names", "current_institution_names",
        "current_department_names", "current_project_record",
        "current_project_rows", "current_project_summary",
    ):
        source = inspect.getsource(getattr(budget_pg_store, name))
        assert "engine, t = _read_only_engine_and_tables()" in source
        assert "_bound_budget_view_query(conn," in source
