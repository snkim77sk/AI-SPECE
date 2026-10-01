import importlib.util
import os
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
    assert report["collection_keys_ready"] is True
    assert report["collection_ready"] is True
    assert report["education_live_transport_hold"] is True
    assert report["bulk_historical_hold"] is True
    assert report["required_actions"] == []


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
    assert "SET_G2B_BUDGET_DATABASE_URL" in report["required_actions"]


def test_deployment_preflight_never_echoes_secret_values(monkeypatch):
    _good(monkeypatch)
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
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
