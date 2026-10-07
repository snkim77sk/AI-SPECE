import inspect

import db
import g2b_heavy_worker
import shopping_store_v41
import vnext_clean_app


def test_isolated_worker_persists_source_failure_detail(monkeypatch):
    seen = {}

    def fake_set_setting(key, value):
        seen["key"] = key
        seen["value"] = value

    monkeypatch.setattr(db, "set_setting", fake_set_setting)

    assert g2b_heavy_worker._persist_source_failure_detail(
        "shopping",
        "SHOPPING_RETENTION:OperationalError",
    ) is True
    assert seen == {
        "key": "shopping_recent_last_error",
        "value": "SHOPPING_RETENTION:OperationalError",
    }


def test_shopping_retention_reuses_run_scoped_preparation():
    signature = inspect.signature(shopping_store_v41.purge_history)
    assert "storage_prepared" in signature.parameters
    assert signature.parameters["storage_prepared"].default is False

    retention_source = inspect.getsource(shopping_store_v41.purge_history)
    assert "if not storage_prepared:" in retention_source

    runtime_source = inspect.getsource(
        vnext_clean_app._run_recent_collection_once_impl
    )
    assert 'storage_prepared=bool(outcomes.get("shopping"))' in runtime_source


def test_parent_keeps_specific_shopping_failure_prefix(monkeypatch):
    vnext_clean_app._RECENT_COLLECTION_STATE.update(
        shopping_run_state="RUNNING",
        shopping_last_status="RUNNING",
        shopping_last_error="",
        budget_run_state="IDLE",
        budget_last_status="IDLE",
        budget_last_error="",
    )
    monkeypatch.setattr(
        vnext_clean_app,
        "get_setting",
        lambda key, default="": (
            "SHOPPING_RETENTION:OperationalError"
            if key == "shopping_recent_last_error"
            else default
        ),
    )

    vnext_clean_app._isolated_worker_exit_state("shopping", 1)

    assert (
        vnext_clean_app._RECENT_COLLECTION_STATE["shopping_last_error"]
        == "SHOPPING_RETENTION:OperationalError"
    )
