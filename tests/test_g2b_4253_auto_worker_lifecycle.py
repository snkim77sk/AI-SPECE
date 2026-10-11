"""4.1.253: auto workers queue under pressure, preserve the web, and reap correctly."""

import threading

import pytest

import vnext_clean_app as app
import safe_boot_vnext


class Process:
    def __init__(self, code, pid):
        self.code = int(code)
        self.pid = int(pid)

    def wait(self, timeout=None):
        # subprocess.Popen.wait(timeout=...) is used by the 512MiB parent
        # supervisor as well; this fake represents an already-exited child.
        return self.code

    def poll(self):
        return None


def test_budget_queues_while_shopping_worker_consumes_cgroup_headroom(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(
        app.memory_guard, "snapshot",
        lambda **_: {
            "guard_ok": True,
            "cgroup_oom_group": 0,
            "cgroup_limit_mib": 256,
            "cgroup_effective_mib": 155,
        },
    )
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: None)
    monkeypatch.setattr(
        app, "_launch_isolated_heavy_worker_locked",
        lambda _: (_ for _ in ()).throw(AssertionError("queued budget must not launch")),
    )
    assert safe_boot_vnext.child_admission({
        "guard_ok":True,"cgroup_limit_mib":256,"cgroup_effective_mib":155,
    })[0] is False

    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", Process(0, 1001))
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    assert app._request_isolated_source_worker("budget") == "QUEUED"
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]
    assert app._request_isolated_source_worker("budget") == "ALREADY"
    assert app._ISOLATED_HEAVY_PENDING == ["budget"]


def test_no_active_worker_still_holds_unsafe_256mib_launch(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(
        app.memory_guard,"snapshot",
        lambda **_:{
            "guard_ok":True,"cgroup_oom_group":0,
            "cgroup_limit_mib":256,"cgroup_effective_mib":155,
        },
    )
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_launch_isolated_heavy_worker_locked", lambda _:
        (_ for _ in ()).throw(AssertionError("unsafe child started"))
    )
    assert app._request_isolated_source_worker("shopping") == "HOLD"
    assert app._ISOLATED_HEAVY_PROCESS is None


def test_automatic_scheduler_waits_for_supervisor_status_publication(monkeypatch):
    status = iter([
        {"running":False,"pending":[],"supervisor_running":True},
        {"running":False,"pending":[],"supervisor_running":False},
    ])
    sleeps = []
    monkeypatch.setattr(app, "_auto_sync_enabled", lambda: True)
    monkeypatch.setattr(app, "schedule_manual_collection", lambda _source: True)
    monkeypatch.setattr(app, "_isolated_heavy_worker_status", lambda: next(status))
    monkeypatch.setattr(app.time, "sleep", lambda seconds: sleeps.append(seconds))
    outcome = app._run_low_memory_automatic_cycle()
    assert outcome["scheduled"] == {"shopping":True,"budget":True}
    assert sleeps == [1.0]


def test_supervisor_defers_new_child_until_previous_terminal_status(monkeypatch):
    first = Process(0, 1001)
    second = Process(72, 1002)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", first)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: True)
    monkeypatch.setattr(app, "_ensure_isolated_heavy_supervisor_locked", lambda: None)
    seen = []

    def launch(mode):
        assert mode == "budget"
        assert seen == [("shopping", 0)]
        app._ISOLATED_HEAVY_PROCESS = second
        app._ISOLATED_HEAVY_KIND = "budget"
        return second

    def publish_exit(kind, code, **_kwargs):
        seen.append((kind, code))
        if kind == "shopping":
            # A browser click during completion cannot start before old
            # COMPLETE is published. Other source is serialized in queue.
            assert app._ISOLATED_HEAVY_PROCESS is first
            assert app._request_isolated_source_worker("shopping") == "ALREADY"
            assert app._request_isolated_source_worker("budget") == "QUEUED"
            assert app._request_isolated_source_worker("budget") == "ALREADY"
            assert app._ISOLATED_HEAVY_PENDING == ["budget"]

    monkeypatch.setattr(app, "_launch_isolated_heavy_worker_locked", launch)
    monkeypatch.setattr(app, "_isolated_worker_exit_state", publish_exit)
    app._isolated_heavy_supervisor_worker()
    assert seen == [("shopping", 0), ("budget", 72)]
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._ISOLATED_HEAVY_SUPERVISOR is None


def test_denied_next_child_finishes_memory_reporting_outside_supervisor_lock(monkeypatch):
    # The existing match worker may have two legitimate source requests queued.
    # Publishing WAITING_MEMORY can consult PostgreSQL: never do that inside
    # the isolated-worker status mutex that /collection-monitor also acquires.
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", Process(0, 1001))
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "match")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", ["shopping","budget"])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    monkeypatch.setattr(app, "_isolated_worker_admission_ok", lambda _mode: False)
    seen = []

    def publish_exit(kind, code, **kwargs):
        # Non-blocking acquisition must succeed, even while reporting
        # queued children from a failed memory-admission decision.
        acquired=app._ISOLATED_HEAVY_LOCK.acquire(blocking=False)
        assert acquired, "worker status lock held during DB-backed state update"
        app._ISOLATED_HEAVY_LOCK.release()
        seen.append((kind,code))

    monkeypatch.setattr(app, "_isolated_worker_exit_state", publish_exit)
    app._isolated_heavy_supervisor_worker()
    assert seen == [("match",0),("shopping",75),("budget",75)]
    assert app._ISOLATED_HEAVY_PENDING == []
    assert app._ISOLATED_HEAVY_SUPERVISOR is None


def test_supervisor_reports_finalization_to_automatic_scheduler(monkeypatch):
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", threading.current_thread())
    status = app._isolated_heavy_worker_status()
    assert status["running"] is False
    assert status["supervisor_running"] is True
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", None)
    assert app._isolated_heavy_worker_status()["supervisor_running"] is False


def test_auto_worker_policy_and_first_budget_schema_install_preserved():
    import inspect
    import db
    assert "G2B_AUTO_SYNC_DISABLE" in inspect.getsource(__import__("runtime_role").automatic_collection_enabled)
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in inspect.getsource(db.init_db)
    assert "budget_boot_ok = bool(_boot_budget_storage.storage_ready())" in inspect.getsource(app.initialize_backend)
