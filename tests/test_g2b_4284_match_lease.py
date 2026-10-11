"""4.1.284: cross-host Match Worker must hold a dedicated PG advisory lease."""
from contextlib import contextmanager
import inspect

import g2b_database
import vnext_clean_app as app
import g2b_heavy_worker
from scripts import g2b_cold_cutover_guard as cold


def _setup(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    recorded = []
    monkeypatch.setattr(app, "_set_match_backfill_state",
                        lambda **kw: recorded.append(dict(kw)))
    return recorded


def test_match_summary_rebuild_is_inside_cross_host_lease(monkeypatch):
    _setup(monkeypatch)
    running = []
    @contextmanager
    def lease(name, *, shared=False):
        assert name == "g2b_v41_match_rollover" and not shared
        running.append(True)
        try:
            yield True
        finally:
            running.pop()
    monkeypatch.setattr(g2b_database, "operational_cycle_lease", lease)
    def rebuilt(flag):
        assert running == [True]
        assert flag is False
        return "MATCH_COMPLETE"
    monkeypatch.setattr(app, "_match_backfill_worker_unleased", rebuilt)
    assert app._match_backfill_worker(False) == "MATCH_COMPLETE"
    assert running == []


def test_busy_match_lease_cannot_rewrite_or_call_source(monkeypatch):
    records = _setup(monkeypatch)
    @contextmanager
    def lease(name, *, shared=False):
        assert name == "g2b_v41_match_rollover"
        yield False
    monkeypatch.setattr(g2b_database, "operational_cycle_lease", lease)
    def forbidden(*args,**kwargs):
        raise AssertionError("cannot write match results without a lease")
    monkeypatch.setattr(app, "_match_backfill_worker_unleased", forbidden)
    assert app._match_backfill_worker(False) == "LEASE_HELD"
    assert records[-1]["state"] == "LEASE_HELD"
    assert records[-1]["last_error"] == ""


def test_match_postgres_error_is_sanitized_waiting_storage(monkeypatch,capsys):
    records = _setup(monkeypatch)
    @contextmanager
    def lease(name, *, shared=False):
        raise ConnectionError("postgresql://user:secret@internal/")
        yield True
    monkeypatch.setattr(g2b_database, "operational_cycle_lease", lease)
    monkeypatch.setattr(app, "_match_backfill_worker_unleased",
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError("must not compute before PG admission")))
    assert app._match_backfill_worker(False) == "WAITING_STORAGE"
    assert records[-1]["state"] == "WAITING_STORAGE"
    assert records[-1]["last_error"] == "MATCH_LEASE:ConnectionError"
    assert "secret" not in capsys.readouterr().out


def test_test_mode_keeps_existing_source_free_fixtures(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(g2b_database, "operational_cycle_lease",
                        lambda *a,**k: (_ for _ in ()).throw(
                            AssertionError("test must not touch PG")))
    monkeypatch.setattr(app, "_match_backfill_worker_unleased",
                        lambda flag: "TEST_OK" if flag else "TEST_NO")
    assert app._match_backfill_worker(True) == "TEST_OK"


def test_match_lock_in_nonmutating_cold_cutover_probe():
    assert cold.LOCK_NAMES == (
        "g2b_v41_operational_cycle",
        "g2b_v41_manual_shopping",
        "g2b_v41_manual_budget",
        "g2b_v41_match_rollover",
    )


def test_match_child_lease_denial_cannot_report_success():
    source = inspect.getsource(g2b_heavy_worker.main)
    assert 'match_outcome == "LEASE_HELD"' in source
    assert 'match_outcome == "WAITING_STORAGE"' in source
    assert "return 77" in source and "return 74" in source


def test_match_parent_exit_reconciles_lease_storage_and_memory(monkeypatch):
    records = []
    monkeypatch.setattr(app, "_set_match_backfill_state",
                        lambda **kw: records.append(dict(kw)))
    expected = {
        77: "LEASE_HELD",
        74: "WAITING_STORAGE",
        75: "WAITING_MEMORY",
        1: "FAILED",
    }
    for exit_code, state in expected.items():
        app._isolated_match_worker_exit_state("match", exit_code)
        assert records[-1]["state"] == state
        assert records[-1]["last_result_status"] == state
        assert records[-1]["last_finished_at"]
    assert "secret" not in str(records)


def test_match_parent_exit_zero_defers_to_durable_status(monkeypatch):
    seen = []
    monkeypatch.setattr(app, "_set_match_backfill_state",
                        lambda **kw: seen.append(dict(kw)))
    app._isolated_match_worker_exit_state("match-legacy", 0)
    assert seen[-1]["state"] == "IDLE"
    assert seen[-1]["last_result_status"] == "IDLE"
    assert not seen[-1]["last_error"]


def test_match_parent_memory_preemption_retains_resumable_hold(monkeypatch):
    seen = []
    monkeypatch.setattr(app, "_set_match_backfill_state",
                        lambda **kw: seen.append(dict(kw)))
    app._isolated_match_worker_exit_state(
        "match", 0, parent_memory_abort=True
    )
    assert seen[-1]["state"] == "WAITING_MEMORY"
    assert seen[-1]["last_error"] == "PARENT_CGROUP_MEMORY_HOLD"


def test_reaper_dispatches_match_exit_to_match_specific_state():
    code = inspect.getsource(app._isolated_heavy_supervisor_worker)
    assert "_isolated_match_worker_exit_state(" in code
    assert 'kind in {"match", "match-legacy"}' in code
