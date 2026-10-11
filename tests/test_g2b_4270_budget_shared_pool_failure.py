"""4.1.270 integration: budget bootstrap errors must not recycle the shared PG pool.

Source-free, no database connection, no API call and no business-data mutation.
"""
from types import SimpleNamespace

import pytest

import budget_pg_store as budget
import g2b_database


URL = "postgresql+psycopg://ci:masked@example.invalid/g2b"
SCHEMA = "g2b_budget"


class FakeEngine:
    def __init__(self):
        self.dialect = SimpleNamespace(name="postgresql")
        self.dispose_count = 0

    def dispose(self):
        self.dispose_count += 1


def _setup(monkeypatch, engine):
    monkeypatch.setattr(budget, "resolve_database_url", lambda: URL)
    monkeypatch.setattr(budget, "_safe_schema", lambda: SCHEMA)
    monkeypatch.setattr(g2b_database, "engine", lambda: engine)
    monkeypatch.setattr(budget, "_ENGINE", None)
    monkeypatch.setattr(budget, "_TABLES", None)
    monkeypatch.setattr(budget, "_ENGINE_URL", None)
    monkeypatch.setattr(budget, "_ENGINE_CONFIG", None)
    monkeypatch.setattr(budget, "_LAST_ERROR_CODE", "")


def test_budget_schema_failure_does_not_dispose_shared_postgresql_pool(monkeypatch):
    engine = FakeEngine()
    _setup(monkeypatch, engine)

    def deny_schema(*_args):
        raise RuntimeError("BUDGET_POSTGRES_SCHEMA_CREATE_FAILED")

    monkeypatch.setattr(budget, "_ensure_database_schema", deny_schema)
    with pytest.raises(RuntimeError, match="BUDGET_POSTGRES_SCHEMA_CREATE_FAILED"):
        budget._engine_and_tables()

    assert engine.dispose_count == 0, "budget bootstrap killed the shared web pool"
    assert budget._ENGINE is None
    assert budget.postgres_last_error_code() == "BUDGET_POSTGRES_SCHEMA_CREATE_FAILED"


def test_failed_budget_readiness_invalidates_metadata_but_not_web_pool(monkeypatch):
    engine = FakeEngine()
    _setup(monkeypatch, engine)
    monkeypatch.setattr(budget, "_ENGINE", engine)
    monkeypatch.setattr(budget, "_TABLES", {"stale": True})
    monkeypatch.setattr(budget, "_ENGINE_URL", URL)
    monkeypatch.setattr(budget, "_ENGINE_CONFIG", (URL, SCHEMA))
    monkeypatch.setattr(budget, "postgres_url_present", lambda: True)

    def fail_budget_probe():
        raise OSError("simulated budget-only outage")

    def forbidden_canonical_reset():
        raise AssertionError("readiness must not reset g2b_database.engine")

    monkeypatch.setattr(budget, "_engine_and_tables", fail_budget_probe)
    monkeypatch.setattr(g2b_database, "reset_engine_cache", forbidden_canonical_reset)

    assert budget.postgres_ready() is False
    assert budget.postgres_last_error_code() == "OSError"
    assert engine.dispose_count == 0
    assert budget._ENGINE is None
    assert budget._TABLES is None
    assert budget._ENGINE_URL is None
    assert budget._ENGINE_CONFIG is None


def test_budget_cache_does_not_keep_stale_engine_after_pool_rotation(monkeypatch):
    old_engine = FakeEngine()
    new_engine = FakeEngine()
    _setup(monkeypatch, new_engine)
    monkeypatch.setattr(budget, "_ENGINE", old_engine)
    monkeypatch.setattr(budget, "_ENGINE_CONFIG", (URL, SCHEMA))
    monkeypatch.setattr(budget, "_TABLES", {"old": True})

    creations = []

    class Metadata:
        def create_all(self, engine):
            creations.append(engine)

    metadata = Metadata()
    monkeypatch.setattr(budget, "_ensure_database_schema", lambda *_: None)
    monkeypatch.setattr(budget, "_build_tables", lambda schema: {"metadata": metadata})
    monkeypatch.setattr(budget, "_verify_table_contract", lambda *_: None)
    monkeypatch.setattr(budget, "_ensure_declared_indexes", lambda *_: None)

    first_engine, tables = budget._engine_and_tables()
    again_engine, again_tables = budget._engine_and_tables()

    assert first_engine is new_engine
    assert again_engine is new_engine
    assert tables is again_tables
    assert budget._ENGINE is new_engine
    assert creations == [new_engine], "schema bootstrap repeated for same pool"
    assert old_engine.dispose_count == 0
    assert new_engine.dispose_count == 0
