import asyncio
import importlib
from types import SimpleNamespace

from fastapi import FastAPI

import main


def _reload_main(monkeypatch, *, emergency_only="0"):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_FULL_RUNTIME_ENABLE", "0")
    monkeypatch.setenv("G2B_BACKEND_INIT_ENABLE", "0")
    monkeypatch.setenv("G2B_EMERGENCY_ONLY", emergency_only)
    monkeypatch.delenv("G2B_FULL_RUNTIME_DISABLE", raising=False)
    return importlib.reload(main)


def test_normal_boot_ignores_legacy_zero_enable_flags(monkeypatch):
    loaded = _reload_main(monkeypatch)

    assert loaded.full_runtime_enabled() is True
    assert loaded.emergency_only_enabled() is False
    gate = loaded._recovery_gate_snapshot()
    assert gate["backend_init_enable"] is True
    assert gate["legacy_backend_init_enable"] is False
    assert isinstance(loaded.app, FastAPI)
    assert isinstance(loaded.app, loaded.ProgressiveASGIApp)
    assert loaded.app.runtime_loaded is False


def test_emergency_only_kill_switch_keeps_recovery_shell(monkeypatch):
    loaded = _reload_main(monkeypatch, emergency_only="1")

    assert loaded.full_runtime_enabled() is False
    assert loaded.emergency_only_enabled() is True
    assert isinstance(loaded.app, FastAPI)
    assert isinstance(loaded.app, loaded.ProgressiveASGIApp)
    assert loaded.app.start_runtime_load() is False
    assert loaded.app.runtime_loaded is False


def test_progressive_loader_attaches_runtime_and_schedules_backend(monkeypatch):
    loaded = _reload_main(monkeypatch)
    scheduled = []
    fake_app = object()
    fake_runtime = SimpleNamespace(
        app=fake_app,
        schedule_backend_init=lambda: scheduled.append("backend"),
    )

    monkeypatch.setattr(
        loaded.importlib,
        "import_module",
        lambda name: fake_runtime
        if name == "vnext_clean_app"
        else (_ for _ in ()).throw(AssertionError(name)),
    )
    monkeypatch.setattr(
        loaded,
        "bridge_cafe24_budget_database_url",
        lambda environ=None: "DB_*",
    )

    progressive = loaded.ProgressiveASGIApp()
    progressive._load_runtime()

    assert progressive.runtime_loaded is True
    assert progressive._runtime_app is fake_app
    assert progressive._runtime_module is fake_runtime
    assert progressive.runtime_error == ""
    assert scheduled == ["backend"]
    assert loaded.BUDGET_DB_BRIDGE_SOURCE == "DB_*"
    assert loaded.BOOTSTRAP_IMPORT_ERROR == ""


def test_progressive_lifespan_does_not_start_runtime_before_bind(monkeypatch):
    loaded = _reload_main(monkeypatch)
    progressive = loaded.ProgressiveASGIApp()
    events = []
    messages = iter([
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ])

    monkeypatch.setattr(
        progressive,
        "start_runtime_load",
        lambda: events.append("loader"),
    )

    async def receive():
        return next(messages)

    async def send(message):
        events.append(message["type"])

    asyncio.run(progressive({"type": "lifespan"}, receive, send))

    assert events == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]


def test_first_http_probe_finishes_before_runtime_loader(monkeypatch):
    loaded = _reload_main(monkeypatch)
    progressive = loaded.ProgressiveASGIApp()
    events = []

    monkeypatch.setattr(
        progressive,
        "start_runtime_load",
        lambda: events.append("loader"),
    )

    async def receive():
        raise AssertionError("recovery response must not read request body")

    async def send(message):
        if message["type"] == "http.response.start":
            events.append("response_start")
        elif message["type"] == "http.response.body":
            events.append("response_body")

    asyncio.run(
        progressive(
            {"type": "http", "path": "/live", "method": "GET"},
            receive,
            send,
        )
    )

    assert events == ["response_start", "response_body", "loader"]


def test_launcher_explicitly_disables_uvicorn_lifespan():
    run_source = __import__("pathlib").Path("run.py").read_text(encoding="utf-8")
    main_source = __import__("pathlib").Path("main.py").read_text(encoding="utf-8")

    assert 'lifespan="off"' in run_source
    assert 'lifespan="off"' in main_source
