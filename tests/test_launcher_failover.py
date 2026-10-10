import inspect
import os

import run


def test_launcher_module_is_stdlib_only_before_port_bind():
    source = inspect.getsource(run)
    for forbidden in (
        "import memory_guard",
        "from memory_guard",
        "import runtime_identity",
        "from runtime_identity",
        "import app_version",
        "from app_version",
        "import fastapi",
        "import uvicorn",
        "import sqlalchemy",
        "import psycopg",
    ):
        assert forbidden not in source


def test_launcher_version_is_loaded_without_project_import():
    assert run.VERSION
    assert run.VERSION == "4.1.227"


def test_recovery_snapshot_keeps_external_port_semantics_source_free(monkeypatch):
    monkeypatch.setattr(
        run,
        "_child_state",
        lambda: {
            "child_running": False,
            "child_pid": 0,
            "child_port": 19001,
            "child_exit_code": 137,
        },
    )
    snapshot = run._recovery_snapshot()

    assert snapshot["status"] == "ok"
    assert snapshot["process_alive"] is True
    assert snapshot["runtime"] == "G2B_PORT_GUARD_RECOVERY"
    assert snapshot["port_guard"] is True
    assert snapshot["database_touched"] is False
    assert snapshot["source_io_performed"] is False
    assert snapshot["operational_ready"] is False
    assert snapshot["child_exit_code"] == 137


def test_internal_port_never_reuses_external_port(monkeypatch):
    monkeypatch.delenv("G2B_INTERNAL_PORT", raising=False)
    value = run._pick_internal_port(8000)
    assert 1 <= value <= 65535
    assert value != 8000


def test_explicit_internal_port_is_respected_when_safe(monkeypatch):
    monkeypatch.setenv("G2B_INTERNAL_PORT", "19001")
    assert run._pick_internal_port(8000) == 19001


def test_process_tuning_is_applied_without_overriding_operator(monkeypatch):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "7")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.delenv(name, raising=False)

    state = run._apply_process_tuning_env()

    assert state["MALLOC_ARENA_MAX"] == "7"
    assert state["OMP_NUM_THREADS"] == "1"
    assert state["OPENBLAS_NUM_THREADS"] == "1"
    assert state["MKL_NUM_THREADS"] == "1"


def test_main_binds_external_server_before_starting_child(monkeypatch):
    calls = []

    class FakeServer:
        def serve_forever(self, poll_interval=0.25):
            calls.append(("serve", poll_interval))
            raise KeyboardInterrupt

        def server_close(self):
            calls.append(("close",))

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            calls.append(("thread_init", name, daemon))
            self.target = target
            self.args = args

        def start(self):
            calls.append(("thread_start",))

        def join(self, timeout=None):
            calls.append(("thread_join", timeout))

    monkeypatch.setattr(run, "resolve_port", lambda value=None: 8000)
    monkeypatch.setattr(run, "_pick_internal_port", lambda port: 19001)
    monkeypatch.setattr(
        run,
        "_bind_server",
        lambda port: calls.append(("bind", port)) or FakeServer(),
    )
    monkeypatch.setattr(run.threading, "Thread", FakeThread)
    monkeypatch.setattr(run.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(run, "_stop_child", lambda: calls.append(("stop_child",)))
    run._STOP_EVENT.clear()

    run.main()

    assert calls[0] == ("bind", 8000)
    assert calls.index(("bind", 8000)) < calls.index(("thread_start",))
    assert ("serve", 0.25) in calls
    assert ("stop_child",) in calls
    assert ("close",) in calls


def test_child_command_defers_uvicorn_import_to_subprocess(monkeypatch):
    captured = {}

    class FakeProc:
        pid = 1234

        def poll(self):
            return None

    def fake_popen(command, env):
        captured["command"] = list(command)
        captured["env"] = dict(env)
        return FakeProc()

    monkeypatch.setattr(run.subprocess, "Popen", fake_popen)
    proc = run._spawn_runtime(19001)

    assert proc.pid == 1234
    assert captured["command"][:3] == [run.sys.executable, "-m", "uvicorn"]
    assert "main:app" in captured["command"]
    assert "19001" in captured["command"]
    assert captured["env"]["G2B_SUPERVISED_CHILD"] == "1"
    assert captured["env"]["G2B_INTERNAL_PORT"] == "19001"


def test_web_child_memory_admission_holds_small_cgroup_without_headroom(monkeypatch):
    monkeypatch.setattr(
        run,
        "_cgroup_memory_values",
        lambda: ("cgroup-v2", 150 * run.MIB, 256 * run.MIB),
    )
    monkeypatch.delenv("G2B_WEB_CHILD_MIN_HEADROOM_MB", raising=False)
    state = run._web_child_memory_admission()
    assert state["allowed"] is False
    assert state["reason"] == "STARTUP_HEADROOM_HOLD"
    assert state["headroom_mib"] == 106.0
    assert state["required_headroom_mib"] == 120


def test_web_child_memory_admission_allows_after_old_process_drains(monkeypatch):
    monkeypatch.setattr(
        run,
        "_cgroup_memory_values",
        lambda: ("cgroup-v2", 110 * run.MIB, 256 * run.MIB),
    )
    state = run._web_child_memory_admission()
    assert state["allowed"] is True
    assert state["headroom_mib"] == 146.0


def test_web_child_memory_admission_does_not_block_without_cgroup(monkeypatch):
    monkeypatch.setattr(run, "_cgroup_memory_values", lambda: ("", 0, 0))
    state = run._web_child_memory_admission()
    assert state["allowed"] is True
    assert state["reason"] == "CGROUP_UNAVAILABLE"
