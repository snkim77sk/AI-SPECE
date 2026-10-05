import importlib
from types import SimpleNamespace

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
    assert isinstance(loaded.app, loaded.ProgressiveASGIApp)
    assert loaded.app.runtime_loaded is False


def test_emergency_only_kill_switch_keeps_recovery_shell(monkeypatch):
    loaded = _reload_main(monkeypatch, emergency_only="1")

    assert loaded.full_runtime_enabled() is False
    assert loaded.emergency_only_enabled() is True
    assert isinstance(loaded.app, loaded.RecoveryASGIApp)


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
