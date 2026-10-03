import importlib.util
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "g2b_deployment_preflight.py"
)
SPEC = importlib.util.spec_from_file_location(
    "g2b_deployment_preflight_testmod", SCRIPT
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def _good(monkeypatch):
    monkeypatch.delenv("G2B_TEST_MODE", raising=False)
    monkeypatch.setattr(preflight, "runtime_role", lambda: preflight.UNIFIED)
    monkeypatch.setattr(preflight, "init_db", lambda: None)
    monkeypatch.setattr(preflight, "db_is_persistent", lambda: True)
    monkeypatch.setattr(
        preflight.budget_storage, "backend_name", lambda: "POSTGRESQL"
    )
    monkeypatch.setattr(
        preflight.budget_storage, "storage_configured", lambda: True
    )
    monkeypatch.setattr(
        preflight.budget_storage, "storage_ready", lambda: True
    )
    monkeypatch.setattr(
        preflight.budget_storage, "storage_error_code", lambda: ""
    )
    monkeypatch.setattr(
        preflight,
        "source_credential_configured",
        lambda name: name in {"g2b_service_key", "lofin_api_key"},
    )


def test_deployment_preflight_reports_ready_without_source_io(monkeypatch):
    _good(monkeypatch)
    monkeypatch.setenv("G2B_AUTO_SYNC", "1")

    report = preflight.run_preflight()

    assert report["source_io_performed"] is False
    assert report["runtime_role"] == "UNIFIED"
    assert report["control_storage_ready"] is True
    assert report["control_storage_persistent"] is True
    assert report["budget_backend"] == "POSTGRESQL"
    assert report["budget_postgres_configured"] is True
    assert report["budget_postgres_ready"] is True
    assert report["infrastructure_ready"] is True
    assert report["shopping_key_ready"] is True
    assert report["budget_key_ready"] is True
    assert report["shopping_collection_ready"] is True
    assert report["budget_collection_ready"] is True
    assert report["collection_keys_ready"] is True
    assert report["collection_ready"] is True
    assert report["education_live_transport_hold"] is True
    assert report["bulk_historical_hold"] is True
    assert report["required_actions"] == []


def test_deployment_preflight_defaults_auto_sync_off_when_unset(monkeypatch):
    _good(monkeypatch)
    monkeypatch.delenv("G2B_AUTO_SYNC", raising=False)

    report = preflight.run_preflight()

    assert report["infrastructure_ready"] is True
    assert report["auto_sync_enabled"] is False


def test_deployment_preflight_reports_shopping_and_budget_readiness_separately(
    monkeypatch,
):
    _good(monkeypatch)

    monkeypatch.setattr(
        preflight,
        "source_credential_configured",
        lambda name: name == "g2b_service_key",
    )
    shopping_only = preflight.run_preflight()
    assert shopping_only["shopping_key_ready"] is True
    assert shopping_only["budget_key_ready"] is False
    assert shopping_only["shopping_collection_ready"] is True
    assert shopping_only["budget_collection_ready"] is False
    assert shopping_only["collection_ready"] is False

    monkeypatch.setattr(
        preflight,
        "source_credential_configured",
        lambda name: name == "lofin_api_key",
    )
    budget_only = preflight.run_preflight()
    assert budget_only["shopping_key_ready"] is False
    assert budget_only["budget_key_ready"] is True
    assert budget_only["shopping_collection_ready"] is False
    assert budget_only["budget_collection_ready"] is True
    assert budget_only["collection_ready"] is False


def test_deployment_preflight_distinguishes_infrastructure_from_missing_keys(
    monkeypatch,
):
    _good(monkeypatch)
    monkeypatch.setattr(
        preflight,
        "source_credential_configured",
        lambda name: False,
    )

    report = preflight.run_preflight()

    assert report["infrastructure_ready"] is True
    assert report["collection_keys_ready"] is False
    assert report["collection_ready"] is False
    assert "SET_G2B_SERVICE_KEY" in report["required_actions"]
    assert "SET_LOFIN_API_KEY" in report["required_actions"]


def test_deployment_preflight_does_not_probe_unconfigured_postgres(monkeypatch):
    monkeypatch.setattr(preflight, "runtime_role", lambda: preflight.UNIFIED)
    monkeypatch.setattr(preflight, "init_db", lambda: None)
    monkeypatch.setattr(preflight, "db_is_persistent", lambda: True)
    monkeypatch.setattr(
        preflight.budget_storage, "backend_name", lambda: "POSTGRESQL"
    )
    monkeypatch.setattr(
        preflight.budget_storage, "storage_configured", lambda: False
    )
    monkeypatch.setattr(
        preflight.budget_storage,
        "storage_ready",
        lambda: (_ for _ in ()).throw(
            AssertionError("unconfigured postgres must not be probed")
        ),
    )
    monkeypatch.setattr(
        preflight,
        "source_credential_configured",
        lambda name: False,
    )

    report = preflight.run_preflight()

    assert report["budget_postgres_configured"] is False
    assert report["budget_postgres_ready"] is False
    assert (
        report["budget_postgres_error_code"]
        == "BUDGET_POSTGRES_NOT_CONFIGURED"
    )
    assert report["infrastructure_ready"] is False
    assert "SET_G2B_DATABASE_URL" in report["required_actions"]


def test_deployment_preflight_never_echoes_secret_values(monkeypatch):
    _good(monkeypatch)
    monkeypatch.setenv(
        "G2B_DATABASE_URL",
        "postgresql://secret-user:super-secret-password@db.invalid/private",
    )
    monkeypatch.setenv("G2B_SERVICE_KEY", "super-secret-g2b")
    monkeypatch.setenv("LOFIN_API_KEY", "super-secret-lofin")

    report = preflight.run_preflight()
    rendered = repr(report)

    assert "super-secret-password" not in rendered
    assert "super-secret-g2b" not in rendered
    assert "super-secret-lofin" not in rendered
    assert "db.invalid" not in rendered



def test_deployment_preflight_script_is_directly_executable(tmp_path):
    env = dict(os.environ)
    env.update({
        "G2B_TEST_MODE": "1",
        "G2B_AUTO_SYNC": "0",
        "G2B_BUDGET_STORAGE": "sqlite",
        "G2B_DB_PATH": str(tmp_path / "preflight.sqlite3"),
    })
    env.pop("G2B_BUDGET_DATABASE_URL", None)

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=str(SCRIPT.parents[1]),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "ModuleNotFoundError" not in result.stderr
    payload = __import__("json").loads(result.stdout)
    assert payload["source_io_performed"] is False
    assert payload["infrastructure_ready"] is False



def test_deployment_preflight_rejects_test_mode_in_production(monkeypatch):
    _good(monkeypatch)
    monkeypatch.setenv("G2B_TEST_MODE", "1")

    report = preflight.run_preflight()

    assert report["test_mode_enabled"] is True
    assert report["infrastructure_ready"] is False
    assert "UNSET_G2B_TEST_MODE" in report["required_actions"]



def test_preflight_require_keys_mode_fails_when_infrastructure_only(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        lambda: {
            "infrastructure_ready": True,
            "collection_ready": False,
        },
    )

    assert preflight.main(["--require-keys"]) == 2
    assert '"collection_ready": false' in capsys.readouterr().out.lower()


def test_preflight_require_keys_mode_passes_only_when_collection_ready(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        lambda: {
            "infrastructure_ready": True,
            "collection_ready": True,
        },
    )

    assert preflight.main(["--require-keys"]) == 0
    assert '"collection_ready": true' in capsys.readouterr().out.lower()


def test_preflight_default_mode_keeps_infrastructure_only_exit_contract(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight,
        "run_preflight",
        lambda: {
            "infrastructure_ready": True,
            "collection_ready": False,
        },
    )

    assert preflight.main([]) == 0
    capsys.readouterr()
