import pytest

import collection_monitor_vnext
import g2b_vnext_canary
import g2b_vnext_pipeline
import historical_vnext
import vnext_clean_app
import vnext_source_guard


FORBIDDEN_HTTP_ROUTES = {
    "/goods",
    "/service",
    "/award",
    "/contract",
    "/api/goods",
    "/api/service",
    "/api/award",
    "/api/contract",
}

EXPECTED_MONITOR_DATASETS = {
    "shopping_delivery",
    "budget",
    "budget_appropriation",
    "education_budget",
}

SHOPPING_PATH = (
    "/1230000/at/ShoppingMallPrdctInfoService/"
    "getDlvrReqDtlInfoList"
)


def test_v4_release_scope_has_no_no1_http_collection_routes():
    routes = {route.path for route in vnext_clean_app.app.routes}
    assert routes.isdisjoint(FORBIDDEN_HTTP_ROUTES)
    assert "/collect/shopping-recent" in routes
    assert "/budget" in routes
    assert "/shopping" in routes


def test_v4_historical_and_canary_scope_are_shopping_only():
    assert tuple(name for name, _runner in historical_vnext.STAGES) == (
        "shopping_delivery",
    )
    assert set(g2b_vnext_canary.CANARY_DATASETS) == {
        "shopping_delivery",
    }
    assert set(vnext_source_guard._G2B_SMALL_VALIDATION_PATHS) == {
        SHOPPING_PATH,
    }


def test_v4_collection_monitor_has_only_approved_source_domains():
    assert {
        str(stage["dataset"])
        for stage in collection_monitor_vnext.STAGES
    } == EXPECTED_MONITOR_DATASETS
    assert collection_monitor_vnext.STAGES[-1]["dataset"] == "education_budget"
    assert "HOLD" in collection_monitor_vnext.STAGES[-1]["live_gate"]


def test_v4_service_orchestration_is_a_fail_closed_compatibility_tombstone(
    monkeypatch,
):
    monkeypatch.setattr(
        g2b_vnext_pipeline.bid_vnext,
        "collect_all",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("removed service collector must not run")
        ),
    )
    with pytest.raises(
        RuntimeError,
        match="G2B_V4_SERVICE_COLLECTION_REMOVED",
    ):
        g2b_vnext_pipeline.collect_service_lifecycle(
            "2026-10-01",
            "2026-10-01",
            max_pages=1,
        )
