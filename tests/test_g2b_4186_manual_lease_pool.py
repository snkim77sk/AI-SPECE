from contextlib import nullcontext

import g2b_database


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.closed = False

    def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        value = self.results.pop(0) if self.results else True
        return _ScalarResult(value)

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, conn):
        self.conn = conn
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        return self.conn


def test_manual_source_lease_holds_two_advisory_locks_on_one_connection(monkeypatch):
    conn = _FakeConn([True, True, True, True])
    eng = _FakeEngine(conn)
    monkeypatch.setattr(g2b_database, "engine", lambda: eng)

    with g2b_database.operational_source_cycle_lease("shopping") as acquired:
        assert acquired is True
        assert eng.connect_calls == 1

    sql = [statement for statement, _params in conn.statements]
    names = [params.get("name") for _statement, params in conn.statements]

    assert eng.connect_calls == 1
    assert conn.closed is True
    assert "pg_try_advisory_lock_shared" in sql[0]
    assert names[0] == "g2b_v41_operational_cycle"
    assert "pg_try_advisory_lock(" in sql[1]
    assert names[1] == "g2b_v41_manual_shopping"
    assert "pg_advisory_unlock(" in sql[2]
    assert names[2] == "g2b_v41_manual_shopping"
    assert "pg_advisory_unlock_shared" in sql[3]
    assert names[3] == "g2b_v41_operational_cycle"


def test_manual_source_lease_releases_global_gate_when_source_gate_is_busy(monkeypatch):
    conn = _FakeConn([True, False, True])
    eng = _FakeEngine(conn)
    monkeypatch.setattr(g2b_database, "engine", lambda: eng)

    with g2b_database.operational_source_cycle_lease("budget") as acquired:
        assert acquired is False

    sql = [statement for statement, _params in conn.statements]
    names = [params.get("name") for _statement, params in conn.statements]

    assert eng.connect_calls == 1
    assert conn.closed is True
    assert "pg_try_advisory_lock_shared" in sql[0]
    assert "pg_try_advisory_lock(" in sql[1]
    assert "pg_advisory_unlock_shared" in sql[2]
    assert names[2] == "g2b_v41_operational_cycle"
