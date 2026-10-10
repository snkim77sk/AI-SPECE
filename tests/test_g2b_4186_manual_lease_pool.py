"""Source advisory gates must use one isolated session, not the 1+1 data pool."""

import pytest

import g2b_database


class _Cursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class _FakeConn:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.closed = False
        self.autocommit = True

    def execute(self, statement, params=()):
        self.statements.append((str(statement), tuple(params or ())))
        value = self.results.pop(0) if self.results else True
        if isinstance(value, Exception):
            raise value
        return _Cursor(value)

    def close(self):
        self.closed = True


def _stub_isolated(monkeypatch, conn):
    calls = []
    monkeypatch.setattr(
        g2b_database,
        "_operational_lease_connection",
        lambda: calls.append("open") or conn,
    )
    monkeypatch.setattr(
        g2b_database,
        "engine",
        lambda: (_ for _ in ()).throw(
            AssertionError("advisory gate must not checkout the web pool")
        ),
    )
    return calls


def test_manual_source_lease_holds_two_advisory_locks_on_one_connection(monkeypatch):
    conn = _FakeConn([True, True, True, True])
    calls = _stub_isolated(monkeypatch, conn)

    with g2b_database.operational_source_cycle_lease("shopping") as acquired:
        assert acquired is True
        assert calls == ["open"]
        assert conn.autocommit is True

    sql = [statement for statement, _params in conn.statements]
    names = [params[0] for _statement, params in conn.statements]
    assert conn.closed is True
    assert "pg_try_advisory_lock_shared" in sql[0]
    assert names[0] == "g2b_v41_operational_cycle"
    assert "pg_try_advisory_lock(" in sql[1]
    assert names[1] == "g2b_v41_manual_shopping"
    assert "pg_advisory_unlock(" in sql[2]
    assert names[2] == "g2b_v41_manual_shopping"
    assert "pg_advisory_unlock_shared" in sql[3]
    assert names[3] == "g2b_v41_operational_cycle"


def test_manual_source_lease_releases_global_gate_when_source_gate_busy(monkeypatch):
    conn = _FakeConn([True, False, True])
    calls = _stub_isolated(monkeypatch, conn)
    with g2b_database.operational_source_cycle_lease("budget") as acquired:
        assert acquired is False
    assert calls == ["open"]
    assert conn.closed is True
    assert [p[0] for _s, p in conn.statements] == [
        "g2b_v41_operational_cycle",
        "g2b_v41_manual_budget",
        "g2b_v41_operational_cycle",
    ]


def test_busy_global_gate_never_attempts_source_gate(monkeypatch):
    conn = _FakeConn([False])
    _stub_isolated(monkeypatch, conn)
    with g2b_database.operational_source_cycle_lease("shopping") as acquired:
        assert acquired is False
    assert conn.closed
    assert len(conn.statements) == 1


def test_cycle_lease_releases_when_worker_raises(monkeypatch):
    conn = _FakeConn([True, True])
    _stub_isolated(monkeypatch, conn)
    with pytest.raises(ValueError, match="worker failure"):
        with g2b_database.operational_cycle_lease() as acquired:
            assert acquired
            raise ValueError("worker failure")
    assert conn.closed
    assert len(conn.statements) == 2
    assert "pg_advisory_unlock" in conn.statements[1][0]


def test_source_lease_physically_closes_on_unlock_failure(monkeypatch):
    conn = _FakeConn([True, True, RuntimeError("PG disconnected"), True])
    _stub_isolated(monkeypatch, conn)
    with g2b_database.operational_source_cycle_lease("budget") as acquired:
        assert acquired
    assert conn.closed
    assert "pg_advisory_unlock_shared" in conn.statements[-1][0]


def test_source_lease_closes_on_lock_acquire_failure(monkeypatch):
    conn = _FakeConn([RuntimeError("PG unreachable")])
    _stub_isolated(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="PG unreachable"):
        with g2b_database.operational_source_cycle_lease("shopping"):
            pass
    assert conn.closed


def test_single_global_exclusive_cycle_uses_one_isolated_session(monkeypatch):
    conn = _FakeConn([True, True])
    calls = _stub_isolated(monkeypatch, conn)
    with g2b_database.operational_cycle_lease() as acquired:
        assert acquired
    assert conn.closed
    assert calls == ["open"]
    assert "pg_try_advisory_lock(" in conn.statements[0][0]


def test_reject_unsupported_source_before_opening_db(monkeypatch):
    monkeypatch.setattr(
        g2b_database, "_operational_lease_connection",
        lambda: (_ for _ in ()).throw(AssertionError("must not connect"))
    )
    with pytest.raises(ValueError, match="UNSUPPORTED_OPERATIONAL_SOURCE_LEASE"):
        with g2b_database.operational_source_cycle_lease("all"):
            pass
