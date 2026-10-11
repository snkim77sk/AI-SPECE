"""4.1.265: isolate admission/fork errors and keep automatic supervisor resumable.

No production PostgreSQL, external source APIs, checkpoint writes or schema changes.
"""

import subprocess
import threading

import pytest

import safe_boot_vnext
import vnext_clean_app as app


def _prepare(monkeypatch, *, previous="shopping", queued=("budget",)):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_LAST", {"kind": "", "exit_code": None})
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", previous)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", list(queued))
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {"shopping": 0, "budget": 0})
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "shopping_run_state": "RUNNING",
        "shopping_last_error": "",
        "budget_run_state": "QUEUED",
        "budget_last_error": "",
    })
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("parent launch hold must not query PostgreSQL")
        ),
    )

    class Finished:
        pid = 4265

    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", Finished())
    monkeypatch.setattr(
        app, "_wait_for_isolated_heavy_child",
        lambda _process, _kind: (0, False),
    )


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_corrupted_memory_probe_fails_closed_without_logging_secret(monkeypatch, capsys, mode):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(
        app.memory_guard, "snapshot",
        lambda **_kw: (_ for _ in ()).throw(OSError("SECRET_CONN_STRING")),
    )
    assert app._isolated_worker_admission_ok(mode) is False
    output = capsys.readouterr().out
    assert "G2B_ISOLATED_CHILD_ADMISSION_PROBE_HOLD" in output
    assert "OSError" in output
    assert "SECRET_CONN_STRING" not in output


def test_queued_budget_probe_failure_reports_waiting_memory_and_clears_supervisor(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        app.memory_guard, "snapshot",
        lambda **_kw: (_ for _ in ()).throw(OSError("unreadable cgroup")),
    )
    app._isolated_heavy_supervisor_worker()
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._ISOLATED_HEAVY_SUPERVISOR is None
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_STATE["budget_last_error"] == "PARENT_WORKER_LAUNCH_HOLD"
    assert app._PARENT_MEMORY_ABORT_STREAK["budget"] == 1
    assert app._automatic_cycle_wait_seconds({"operational_cycle_lease": "ISOLATED_AUTOMATIC"}) == 60


def test_queued_budget_fork_failure_reports_waiting_memory_without_orphan(monkeypatch, capsys):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: True)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *_args, **_kw: (_ for _ in ()).throw(OSError(12, "SECRET_SPAWN_DETAILS")),
    )
    app._isolated_heavy_supervisor_worker()
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._ISOLATED_HEAVY_SUPERVISOR is None
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"
    assert app._RECENT_COLLECTION_STATE["budget_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_STATE["budget_last_error"] == "PARENT_WORKER_LAUNCH_HOLD"
    assert app._PARENT_MEMORY_ABORT_STREAK["budget"] == 1
    log = capsys.readouterr().out
    assert "G2B_ISOLATED_CHILD_SPAWN_HOLD budget OSError" in log
    assert "SECRET_SPAWN_DETAILS" not in log


@pytest.mark.parametrize("mode", ["shopping", "budget"])
def test_first_launch_fork_failure_is_memory_hold_not_http_500(monkeypatch, mode):
    _prepare(monkeypatch, queued=())
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "can_collect_sources", lambda: True)
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: True)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *_args, **_kw: (_ for _ in ()).throw(OSError(11, "resource unavailable")),
    )
    assert app.schedule_manual_collection(mode) is False
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_SUPERVISOR is threading.current_thread()
    assert app._RECENT_COLLECTION_STATE[f"{mode}_run_state"] == "WAITING_MEMORY"


def test_match_heavy_worker_fork_failure_fails_safely_without_web_crash(monkeypatch):
    _prepare(monkeypatch, queued=())
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: True)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *_args, **_kw: (_ for _ in ()).throw(OSError(12, "cannot fork")),
    )
    assert app._spawn_isolated_heavy_worker("match") is False
    assert app._ISOLATED_HEAVY_PROCESS is None


def test_next_healthy_retry_can_start_after_queued_fork_failure(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: True)

    def no_fork(*_args, **_kw):
        raise OSError(12, "ENOMEM")
    monkeypatch.setattr(subprocess, "Popen", no_fork)
    app._isolated_heavy_supervisor_worker()
    assert app._ISOLATED_HEAVY_SUPERVISOR is None

    class NewChild:
        pid = 426501
        def poll(self):
            return None
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kw: NewChild())
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: None)
    assert app._request_isolated_source_worker("budget") == "STARTED"
    assert app._ISOLATED_HEAVY_KIND == "budget"
    assert app._ISOLATED_HEAVY_PROCESS.pid == 426501


def test_original_memory_budget_and_schema_policies_remain_identical():
    import inspect
    import db
    assert safe_boot_vnext.CHILD_HEADROOM_RESERVE_MIB == 64.0
    assert safe_boot_vnext.child_admission({
        "guard_ok": True, "cgroup_oom_group": 0,
        "cgroup_limit_mib": 512, "cgroup_effective_mib": 336,
    })[0] is False
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in inspect.getsource(db.init_db)

def test_repeated_parent_launch_holds_backoff_exponentially_without_pg_reads(monkeypatch):
    _prepare(monkeypatch, previous="shopping", queued=())
    app._RECENT_COLLECTION_STATE.update(
        shopping_run_state="COMPLETE", budget_run_state="WAITING_MEMORY",
    )
    for attempt, expected_delay in enumerate((60, 120, 240, 480, 900, 900), 1):
        app._isolated_worker_exit_state(
            "budget", 75, parent_launch_hold=True
        )
        assert app._RECENT_COLLECTION_STATE["budget_last_error"] == "PARENT_WORKER_LAUNCH_HOLD"
        assert app._PARENT_MEMORY_ABORT_STREAK["budget"] == attempt
        assert app._automatic_cycle_wait_seconds({
            "operational_cycle_lease": "ISOLATED_AUTOMATIC"
        }) == expected_delay
    app._isolated_worker_exit_state("budget", 0)
    assert app._PARENT_MEMORY_ABORT_STREAK["budget"] == 0
