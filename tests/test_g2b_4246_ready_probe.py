"""G2B 4.1.246: platform /ready must never migrate the budget schema."""
from contextlib import contextmanager

import budget_pg_store
import budget_storage
import vnext_clean_app


def test_read_only_pg_probe_uses_existing_tables_without_schema_migration(
    monkeypatch,
):
    tables = budget_pg_store._build_tables(None)
    calls = []

    class Result:
        def first(self):
            return (1,)

    class Connection:
        def execute(self, query):
            calls.append(str(query))
            return Result()

    class Engine:
        @contextmanager
        def connect(self):
            calls.append("connect")
            yield Connection()

    monkeypatch.setattr(budget_pg_store, "postgres_url_present", lambda: True)
    monkeypatch.setattr(
        budget_pg_store, "_read_only_engine_and_tables",
        lambda: (Engine(), tables),
    )
    monkeypatch.setattr(
        budget_pg_store, "_engine_and_tables",
        lambda: (_ for _ in ()).throw(
            AssertionError("platform readiness must not migrate")
        ),
    )
    monkeypatch.setattr(
        budget_pg_store, "_bound_budget_view_query",
        lambda _conn, timeout_ms=4500: calls.append(f"timeout={timeout_ms}"),
    )
    monkeypatch.setattr(
        budget_pg_store, "reset_engine_cache",
        lambda: (_ for _ in ()).throw(
            AssertionError("no shared pool dispose from /ready")
        ),
    )
    assert budget_pg_store.postgres_ready_read_only() is True
    assert budget_pg_store.postgres_last_error_code() == ""
    assert "connect" in calls
    assert "timeout=2000" in calls
    assert "SELECT 1" in calls
    assert any("budget_record_states" in x for x in calls)
    assert not any("CREATE" in x or "ALTER " in x for x in calls)


def test_read_only_pg_probe_failure_fails_closed_without_reset(monkeypatch):
    monkeypatch.setattr(budget_pg_store, "postgres_url_present", lambda: True)
    monkeypatch.setattr(
        budget_pg_store, "_read_only_engine_and_tables",
        lambda: (_ for _ in ()).throw(
            TimeoutError("secret=do-not-print")
        ),
    )
    monkeypatch.setattr(
        budget_pg_store, "reset_engine_cache",
        lambda: (_ for _ in ()).throw(
            AssertionError("probe must preserve the shared connection pool")
        ),
    )
    assert budget_pg_store.postgres_ready_read_only() is False
    assert budget_pg_store.postgres_last_error_code() == "TimeoutError"
    assert "secret" not in budget_pg_store.postgres_last_error_code()


def test_budget_storage_dispatch_keeps_boot_migrations_unchanged(monkeypatch):
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    calls = []
    monkeypatch.setattr(
        budget_pg_store,
        "postgres_ready",
        lambda: calls.append("migration-aware") or True,
    )
    monkeypatch.setattr(
        budget_pg_store,
        "postgres_ready_read_only",
        lambda: calls.append("read-only") or True,
    )
    assert budget_storage.storage_ready() is True
    assert budget_storage.storage_ready(read_only=True) is True
    assert calls == ["migration-aware", "read-only"]


def test_web_ready_probes_only_existing_tables(monkeypatch, capsys):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "db_is_persistent", lambda: True)
    monkeypatch.setattr(app, "backend_status", lambda: {
        "backend_ok": True, "initializing": False, "backend_error": "",
        "fresh_start_status": "SKIPPED",
    })
    monkeypatch.setattr(budget_storage, "storage_configured", lambda: True)
    monkeypatch.setattr(budget_storage, "storage_error_code", lambda: "")
    seen = []
    def fake_storage_ready(*, read_only=False):
        seen.append(read_only)
        assert read_only is True
        return True
    monkeypatch.setattr(budget_storage, "storage_ready", fake_storage_ready)
    response = app.ready()
    assert response.status_code == 200
    assert seen == [True]
    assert "G2B_READY_PROBE_MS" in capsys.readouterr().out


def test_platform_readiness_cannot_become_200_on_probe_failure(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "db_is_persistent", lambda: True)
    monkeypatch.setattr(app, "backend_status", lambda: {
        "backend_ok": True, "initializing": False, "backend_error": "",
    })
    monkeypatch.setattr(budget_storage, "storage_configured", lambda: True)
    monkeypatch.setattr(budget_storage, "storage_error_code", lambda: "TimeoutError")
    monkeypatch.setattr(
        budget_storage, "storage_ready",
        lambda *, read_only=False: False,
    )
    response = app.ready()
    assert response.status_code == 503
    assert b"TimeoutError" in response.body
    assert b"secret" not in response.body

