"""4.1.257: cross-process advisory session survival, safe child-only fail-closed."""

import threading
import time

import pytest

import g2b_database
import vnext_clean_app


class ProbeConnection:
    def __init__(self, *, broken=False):
        self.broken = bool(broken)
        self.calls = 0

    def execute(self, sql):
        assert str(sql) == "SELECT 1"
        self.calls += 1
        if self.broken:
            raise ConnectionError("fake PG backend terminated")
        return self

    def fetchone(self):
        return (1,)


def test_watchdog_never_runs_on_web_or_tests(monkeypatch):
    monkeypatch.setenv("G2B_ISOLATED_HEAVY_WORKER", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    assert g2b_database._isolated_lease_watchdog_enabled() is False
    conn = ProbeConnection()
    assert g2b_database._start_operational_lease_watchdog(conn, "shopping") == (
        None, None
    )
    assert conn.calls == 0

    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    assert g2b_database._isolated_lease_watchdog_enabled() is False
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_ISOLATED_HEAVY_WORKER", "0")
    assert g2b_database._isolated_lease_watchdog_enabled() is False


def test_healthy_source_lease_remains_alive_then_stops_cleanly(monkeypatch):
    monkeypatch.setenv("G2B_ISOLATED_HEAVY_WORKER", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    conn = ProbeConnection()
    stopped, thread = g2b_database._start_operational_lease_watchdog(
        conn, "shopping", interval_seconds=0.01
    )
    try:
        deadline=time.monotonic()+1
        while conn.calls < 3 and time.monotonic()<deadline:
            time.sleep(0.005)
        assert conn.calls >= 3
        assert thread.is_alive()
    finally:
        g2b_database._stop_operational_lease_watchdog(stopped, thread)
    assert not thread.is_alive()
    calls=conn.calls
    time.sleep(0.04)
    assert conn.calls == calls


def test_lost_session_terminates_disposable_child_as_retryable_lease(monkeypatch, capsys):
    monkeypatch.setenv("G2B_ISOLATED_HEAVY_WORKER", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    exit_codes=[]
    fired=threading.Event()
    def fake_exit(code):
        exit_codes.append(code)
        fired.set()
    monkeypatch.setattr(g2b_database.os, "_exit", fake_exit)
    conn=ProbeConnection(broken=True)
    stopped, thread=g2b_database._start_operational_lease_watchdog(
        conn, "budget", interval_seconds=0.01
    )
    try:
        assert fired.wait(1)
    finally:
        g2b_database._stop_operational_lease_watchdog(stopped, thread)
    assert exit_codes == [77]
    assert not thread.is_alive()
    out=capsys.readouterr().out
    assert "G2B_ISOLATED_LEASE_SESSION_LOST budget ConnectionError" in out
    assert "fake PG backend terminated" not in out


def test_lease_watchdog_preserves_autocommit_and_existing_lock_order():
    import inspect
    code=inspect.getsource(g2b_database.operational_source_cycle_lease)
    assert code.index("pg_try_advisory_lock_shared") < code.index(
        "pg_try_advisory_lock(hashtext"
    )
    assert "_start_operational_lease_watchdog(" in code
    assert "_stop_operational_lease_watchdog(" in code
    assert "conn.close()" in code
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in inspect.getsource(
        __import__("db").init_db
    )
    assert "budget_boot_ok = bool(_boot_budget_storage.storage_ready())" in inspect.getsource(
        vnext_clean_app.initialize_backend
    )


def test_true_source_lease_conflict_never_starts_watchdog(monkeypatch):
    class Cursor:
        def __init__(self, acquired):
            self.acquired=acquired
        def fetchone(self):
            return (self.acquired,)
    class Fake:
        def __init__(self):
            self.next=[True, False, True]
            self.closed=False
            self.statements=[]
        def execute(self, query, params=()):
            self.statements.append(str(query))
            return Cursor(self.next.pop(0))
        def close(self):
            self.closed=True
    conn=Fake()
    monkeypatch.setattr(g2b_database, "_operational_lease_connection",lambda:conn)
    started=[]
    monkeypatch.setattr(g2b_database,"_start_operational_lease_watchdog",
                        lambda *_: started.append(1) or (None,None))
    with g2b_database.operational_source_cycle_lease("shopping") as acquired:
        assert acquired is False
    assert started == []
    assert conn.closed is True
