import bid_vnext
import budget_notice_links_vnext
import collection_monitor_vnext
import g2b_vnext_canary
import historical_vnext
import vnext_clean_app
import vnext_source_guard


def test_goods_bid_feature_is_out_of_scope_and_owned_by_no1():
    routes = {route.path for route in vnext_clean_app.app.routes}
    assert "/goods" not in routes
    assert "/api/goods" not in routes

    assert "goods" not in bid_vnext.BUSINESS_TYPES
    assert "bid_notice_goods" not in {name for name, _runner in historical_vnext.STAGES}
    assert "bid_notice_goods" not in set(g2b_vnext_canary.CANARY_DATASETS.values())
    assert "bid_notice_goods" not in {stage["dataset"] for stage in collection_monitor_vnext.STAGES}
    assert "bid_notice_goods" not in set(budget_notice_links_vnext.NOTICE_DATASETS)

    goods_path = "/1230000/ad/BidPublicInfoService/getBidPblancListInfoThng"
    assert goods_path not in vnext_source_guard._G2B_SMALL_VALIDATION_PATHS


def test_legacy_goods_raw_can_remain_without_reenabling_collection():
    # Existing deployments may already contain old RAW. Keeping the classifier
    # mapping avoids destructive migration, but collection/query routes remain off.
    import classification_vnext
    assert "bid_notice_goods" in classification_vnext.TEXT_FIELDS
    assert "goods" not in bid_vnext.BUSINESS_TYPES
