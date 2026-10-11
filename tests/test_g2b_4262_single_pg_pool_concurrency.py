"""4.1.262: one shared SQLAlchemy QueuePool during concurrent web menu calls."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

import g2b_database


class FakeEngine:
    def __init__(self):
        self.dispose_count = 0

    def dispose(self):
        self.dispose_count += 1


def _stub_configuration(monkeypatch, *, old=None):
    monkeypatch.setattr(g2b_database, "validate_schema_layout", lambda: ("app", "budget"))
    monkeypatch.setattr(
        g2b_database,
        "resolve_database_url",
        lambda: "postgresql+psycopg://web:masked@example.invalid/g2b",
    )
    monkeypatch.setattr(g2b_database, "_ENGINE", old)
    monkeypatch.setattr(g2b_database, "_ENGINE_URL", "old-dsn" if old else None)
    monkeypatch.setattr(g2b_database, "_ENGINE_CONFIG", None)


def test_cold_start_twelve_concurrent_menu_threads_create_exactly_one_engine(monkeypatch):
    _stub_configuration(monkeypatch)
    gate = threading.Barrier(12)
    created = []
    created_lock = threading.Lock()

    def create_engine(_url, **kw):
        assert kw["pool_size"] == 1
        assert kw["max_overflow"] == 1
        time.sleep(0.025)  # exposes races if the singleton lock is removed
        engine = FakeEngine()
        with created_lock:
            created.append(engine)
        return engine

    monkeypatch.setattr(g2b_database, "create_engine", create_engine)

    def visit(_):
        gate.wait(timeout=5)
        return g2b_database.engine()

    with ThreadPoolExecutor(max_workers=12) as workers:
        observed = list(workers.map(visit, range(12)))

    assert len(created) == 1, "several web requests created separate QueuePools"
    assert all(pool is created[0] for pool in observed)
    assert g2b_database._ENGINE is created[0]


def test_reconfigured_pool_replaces_and_disposes_old_pool_once(monkeypatch):
    prior = FakeEngine()
    _stub_configuration(monkeypatch, old=prior)
    created = []
    monkeypatch.setattr(
        g2b_database, "create_engine",
        lambda *_args, **_kw: created.append(FakeEngine()) or created[-1],
    )

    gate = threading.Barrier(10)

    def visit(_):
        gate.wait(timeout=5)
        return g2b_database.engine()

    with ThreadPoolExecutor(max_workers=10) as workers:
        observed = list(workers.map(visit, range(10)))

    assert len(created) == 1
    assert all(value is created[0] for value in observed)
    assert prior.dispose_count == 1
    assert created[0].dispose_count == 0


def test_reconfiguration_failure_does_not_dispose_previous_pool(monkeypatch):
    prior = FakeEngine()
    _stub_configuration(monkeypatch, old=prior)

    def unavailable(*_args, **_kw):
        raise RuntimeError("simulated create_engine failure")

    monkeypatch.setattr(g2b_database, "create_engine", unavailable)
    with pytest.raises(RuntimeError, match="simulated create_engine failure"):
        g2b_database.engine()
    assert g2b_database._ENGINE is prior
    assert prior.dispose_count == 0


def test_reset_waits_for_inflight_first_engine_build(monkeypatch):
    _stub_configuration(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    reset_finished = threading.Event()
    created = []

    def slow_create(*_args, **_kw):
        entered.set()
        assert release.wait(5)
        engine = FakeEngine()
        created.append(engine)
        return engine

    monkeypatch.setattr(g2b_database, "create_engine", slow_create)
    construction = threading.Thread(target=g2b_database.engine)
    construction.start()
    assert entered.wait(5)
    reset = threading.Thread(
        target=lambda: (g2b_database.reset_engine_cache(), reset_finished.set())
    )
    reset.start()
    try:
        assert not reset_finished.wait(0.05), "reset bypassed in-flight pool creation"
    finally:
        release.set()
        construction.join(timeout=5)
        reset.join(timeout=5)
    assert not construction.is_alive()
    assert not reset.is_alive()
    assert reset_finished.is_set()
    assert len(created) == 1
    assert created[0].dispose_count == 1
    assert g2b_database._ENGINE is None


def test_reset_disposes_only_cached_engine(monkeypatch):
    prior = FakeEngine()
    _stub_configuration(monkeypatch, old=prior)
    g2b_database.reset_engine_cache()
    g2b_database.reset_engine_cache()
    assert prior.dispose_count == 1
    assert g2b_database._ENGINE is None
    assert g2b_database._ENGINE_URL is None
    assert g2b_database._ENGINE_CONFIG is None
