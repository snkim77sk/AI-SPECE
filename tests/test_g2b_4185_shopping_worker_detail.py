import db
import g2b_heavy_worker
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


def test_parent_keeps_specific_shopping_failure_prefix(monkeypatch):
    attempt_id = "c" * 32
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
            f"G2B_WORKER_FAILURE_V1:{attempt_id}:SHOPPING_RETENTION:OperationalError"
            if key == "shopping_recent_last_error"
            else default
        ),
    )

    vnext_clean_app._isolated_worker_exit_state(
        "shopping", 1, attempt_id=attempt_id
    )

    assert (
        vnext_clean_app._RECENT_COLLECTION_STATE["shopping_last_error"]
        == "SHOPPING_RETENTION:OperationalError"
    )
