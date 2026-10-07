import memory_guard
import shopping_recent_vnext
import shopping_store_v41
import vnext_clean_app
import vnext_collection


def test_memory_pressure_error_label_keeps_guard_reason():
    exc = memory_guard.MemoryPressureError("PROCESS_RSS_HOLD:113.4:112")
    assert vnext_collection._safe_error_label(exc) == (
        "MemoryPressureError:PROCESS_RSS_HOLD:113.4:112"
    )


def test_shopping_inflight_memory_pressure_becomes_wait_not_failure(monkeypatch):
    clean = vnext_clean_app
    clean._RECENT_COLLECTION_STATE.update(
        state="IDLE",
        last_error="",
        shopping_status="IDLE",
        shopping_run_state="IDLE",
        shopping_last_status="IDLE",
        shopping_last_error="",
        budget_run_state="IDLE",
        budget_last_status="IDLE",
        budget_last_error="",
    )
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "get_service_key", lambda default="": "G2B")
    monkeypatch.setattr(
        shopping_recent_vnext,
        "collect_forward",
        lambda **kwargs: (_ for _ in ()).throw(
            memory_guard.MemoryPressureError(
                "CGROUP_WAIT_TIMEOUT:211.2:208.0"
            )
        ),
    )
    monkeypatch.setattr(
        shopping_store_v41,
        "purge_history",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("retention must not run after memory hold")
        ),
    )

    result = clean._run_recent_collection_once_impl(source="shopping")
    status = clean.recent_collection_status()

    assert result["shopping"] is None
    assert status["shopping_run_state"] == "WAITING_MEMORY"
    assert status["shopping_last_status"] == "WAITING_MEMORY"
    assert status["shopping_last_error"] == (
        "MEMORY_PRESSURE:CGROUP_WAIT_TIMEOUT:211.2:208.0"
    )
    assert status["state"] == "WAITING_MEMORY"


def test_parent_uses_persisted_memory_pressure_detail(monkeypatch):
    clean = vnext_clean_app
    clean._RECENT_COLLECTION_STATE.update(
        shopping_run_state="RUNNING",
        shopping_last_status="RUNNING",
        shopping_last_error="",
        budget_run_state="IDLE",
        budget_last_status="IDLE",
        budget_last_error="",
    )
    monkeypatch.setattr(
        clean,
        "get_setting",
        lambda key, default="": (
            "MEMORY_PRESSURE:PROCESS_RSS_HOLD:113.4:112"
            if key == "shopping_recent_last_error"
            else default
        ),
    )

    clean._isolated_worker_exit_state("shopping", 75)
    status = clean.recent_collection_status()

    assert status["shopping_run_state"] == "WAITING_MEMORY"
    assert status["shopping_last_error"] == (
        "MEMORY_PRESSURE:PROCESS_RSS_HOLD:113.4:112"
    )


def test_component_run_state_preserves_waiting_memory():
    assert vnext_clean_app._component_run_state("WAITING_MEMORY") == "WAITING_MEMORY"


def test_memory_pressure_checkpoint_status_is_resumable():
    exc = memory_guard.MemoryPressureError("PROCESS_RSS_HOLD:113.4:112")
    assert vnext_collection._failure_checkpoint_status(exc) == "INCOMPLETE"
    assert vnext_collection._failure_checkpoint_status(RuntimeError("boom")) == "FAILED"


def test_runtime_memory_wait_overrides_incomplete_stage():
    snapshot = {
        "stages": [
            {
                "dataset": "shopping_delivery",
                "state": "INCOMPLETE",
                "state_label": "중단",
                "message": "MemoryPressureError",
                "last_error": "MemoryPressureError",
            }
        ],
        "summary": {
            "running": 0,
            "complete": 0,
            "errors": 1,
            "not_started": 0,
        },
    }
    runtime_sources = {
        "shopping_run_state": "WAITING_MEMORY",
        "budget_run_state": "IDLE",
    }
    updated = vnext_clean_app._apply_runtime_wait_states(
        snapshot,
        runtime_sources,
        {"shopping": {}, "budget": {}},
    )
    stage = updated["stages"][0]
    assert stage["state"] == "WAITING_MEMORY"
    assert updated["summary"]["errors"] == 0
