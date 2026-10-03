import datetime as dt
import importlib.util
import json
from contextlib import nullcontext
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "g2b_budget_deployment_canary.py"
)
SPEC = importlib.util.spec_from_file_location(
    "g2b_budget_deployment_canary_testmod", SCRIPT
)
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)


def _storage_ready(monkeypatch):
    monkeypatch.setenv("G2B_AUTO_SYNC", "0")
    monkeypatch.setattr(
        canary.budget_storage, "backend_name", lambda: "POSTGRESQL"
    )
    monkeypatch.setattr(
        canary.budget_storage, "storage_configured", lambda: True
    )
    monkeypatch.setattr(
        canary.budget_storage, "storage_ready", lambda: True
    )
    monkeypatch.setattr(
        canary.budget_storage, "storage_error_code", lambda: ""
    )
    monkeypatch.setattr(
        canary.lofin_vnext_http, "get_lofin_key", lambda: "configured"
    )


def test_budget_deployment_canary_is_live_locked_before_storage(monkeypatch):
    monkeypatch.setattr(
        canary.budget_storage,
        "storage_ready",
        lambda: (_ for _ in ()).throw(
            AssertionError("storage must not be touched while locked")
        ),
    )

    with pytest.raises(
        RuntimeError, match="DEPLOYMENT_BUDGET_CANARY_LIVE_LOCKED"
    ):
        canary.run_canary(allow_live=False)


def test_budget_deployment_canary_requires_external_auto_sync_off(monkeypatch):
    _storage_ready(monkeypatch)
    monkeypatch.setenv("G2B_AUTO_SYNC", "1")
    monkeypatch.setattr(
        canary, "_today_kst", lambda: dt.date(2026, 10, 1)
    )
    monkeypatch.setattr(
        canary.budget_vnext,
        "collect_full_budget",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("collector must not run while auto sync is enabled")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="DEPLOYMENT_BUDGET_CANARY_AUTO_SYNC_MUST_BE_DISABLED",
    ):
        canary.run_canary(
            allow_live=True,
            snapshot_date="2026-09-30",
        )


def test_budget_deployment_canary_accepts_only_source_safe_d_minus_one(monkeypatch):
    monkeypatch.setattr(
        canary, "_today_kst", lambda: dt.date(2026, 10, 1)
    )

    assert canary._snapshot_day("") == dt.date(2026, 9, 30)
    assert canary._snapshot_day("2026-09-30") == dt.date(2026, 9, 30)
    with pytest.raises(
        RuntimeError,
        match="DEPLOYMENT_BUDGET_CANARY_D_MINUS_ONE_DATE_REQUIRED",
    ):
        canary._snapshot_day("2026-10-01")


def test_budget_deployment_canary_is_hard_capped_to_one_page(
    monkeypatch,
):
    _storage_ready(monkeypatch)
    monkeypatch.setattr(
        canary, "_today_kst", lambda: dt.date(2026, 10, 1)
    )
    monkeypatch.setattr(
        canary.vnext_source_guard,
        "operational_budget_source_context",
        lambda **kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        canary.vnext_source_guard,
        "current_source_request_context",
        lambda: {
            "requests_used": 1,
            "transport_successes_used": 1,
        },
    )

    calls = []
    monkeypatch.setattr(
        canary.budget_vnext,
        "collect_full_budget",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {
            "status": "RUNNING",
            "complete": False,
            "resumed": False,
            "fetched": 1000,
            "saved": 1000,
            "source_total": 2500,
            "completion_reason": "",
        },
    )
    monkeypatch.setattr(
        canary.budget_pg_store,
        "get_checkpoint",
        lambda dataset, scope: {
            "status": "RUNNING",
            "page_no": 2,
            "page_size": 1000,
            "fetched_count": 1000,
            "saved_count": 1000,
            "source_total": 2500,
        },
    )

    report = canary.run_canary(
        allow_live=True,
        snapshot_date="2026-09-30",
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (2026, "2026-09-30")
    assert kwargs["page_size"] == 1000
    assert kwargs["max_pages"] == 1
    assert kwargs["resume"] is True
    assert kwargs["refresh_date"] == "2026-10-01"
    assert report["canary_scope"] == "QWGJK_D_MINUS_ONE_ONE_LOGICAL_PAGE_MAX"
    assert report["snapshot_date_kst"] == "2026-09-30"
    assert report["max_pages"] == 1
    assert report["source_io_performed"] is True
    assert report["source_requests_used"] == 1
    assert report["transport_successes"] == 1
    assert report["collector"]["fetched"] == 1000
    assert report["checkpoint"]["page_no"] == 2
    assert report["resume_expected"] is True
    assert report["source_collection_completeness_verified"] is False
    assert report["other_sources_touched"] is False


def test_budget_deployment_canary_report_never_contains_secrets(monkeypatch):
    _storage_ready(monkeypatch)
    monkeypatch.setattr(
        canary, "_today_kst", lambda: dt.date(2026, 10, 1)
    )
    monkeypatch.setattr(
        canary.vnext_source_guard,
        "operational_budget_source_context",
        lambda **kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        canary.vnext_source_guard,
        "current_source_request_context",
        lambda: {"requests_used": 0, "transport_successes_used": 0},
    )
    monkeypatch.setattr(
        canary.budget_vnext,
        "collect_full_budget",
        lambda *args, **kwargs: {
            "status": "COMPLETE",
            "complete": True,
            "resumed": True,
            "fetched": 0,
            "saved": 0,
            "source_total": 0,
            "completion_reason": "TOTAL_REACHED",
        },
    )
    monkeypatch.setattr(
        canary.budget_pg_store,
        "get_checkpoint",
        lambda dataset, scope: {
            "status": "COMPLETE",
            "page_no": 2,
            "page_size": 1000,
            "fetched_count": 0,
            "saved_count": 0,
            "source_total": 0,
            "cursor_value": "super-secret-cursor",
        },
    )

    report = canary.run_canary(allow_live=True)
    rendered = json.dumps(report, ensure_ascii=False)

    assert "configured" not in rendered
    assert "super-secret-cursor" not in rendered
    assert report["checkpoint"]["source_total"] == 0
    assert report["resume_expected"] is False


def test_budget_deployment_canary_cli_fails_closed_without_unlock(capsys):
    code = canary.main([])
    output = json.loads(capsys.readouterr().out)

    assert code == 2
    assert output["status"] == "FAILED"
    assert (
        output["error_code"]
        == "DEPLOYMENT_BUDGET_CANARY_LIVE_LOCKED"
    )
