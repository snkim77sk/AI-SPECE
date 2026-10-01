import pytest

import g2b_vnext_pipeline


def test_service_lifecycle_pipeline_is_hard_disabled_before_any_collector(monkeypatch):
    calls = []

    monkeypatch.setattr(
        g2b_vnext_pipeline.bid_vnext,
        "collect_all",
        lambda *args, **kwargs: calls.append("notice") or {"complete": True},
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.award_vnext,
        "collect_service_opening",
        lambda *args, **kwargs: calls.append("opening") or {"complete": True},
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.award_vnext,
        "collect_service_awards",
        lambda *args, **kwargs: calls.append("award") or {"complete": True},
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.contract_vnext,
        "collect_all",
        lambda *args, **kwargs: calls.append("contract") or {"complete": True},
    )

    with pytest.raises(
        RuntimeError,
        match=g2b_vnext_pipeline.SERVICE_COLLECTION_REMOVED_CODE,
    ):
        g2b_vnext_pipeline.collect_service_lifecycle(
            "2026-09-01",
            "2026-09-01",
            max_pages=1,
        )

    assert calls == []


def test_service_stability_replay_is_hard_disabled_before_source_verification(
    monkeypatch,
):
    monkeypatch.setattr(
        g2b_vnext_pipeline.vnext_stability,
        "verify_checkpoint_source",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("removed service replay must never touch source")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match=g2b_vnext_pipeline.SERVICE_COLLECTION_REMOVED_CODE,
    ):
        g2b_vnext_pipeline._verify_service_stability(
            "2026-09-01",
            "2026-09-01",
        )


def test_service_pipeline_keeps_legacy_dataset_names_for_non_destructive_reads():
    assert g2b_vnext_pipeline.SERVICE_COLLECTION_REMOVED is True
    assert g2b_vnext_pipeline.SERVICE_CLASSIFICATION_DATASETS == (
        "bid_notice_service",
        "opening_result_service",
        "award_result_service",
        "contract_service",
    )
