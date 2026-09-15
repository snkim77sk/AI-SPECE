import g2b_vnext_pipeline


def test_service_lifecycle_runs_in_required_order(monkeypatch):
    calls = []

    monkeypatch.setattr(
        g2b_vnext_pipeline.bid_vnext,
        "collect_all",
        lambda *args, **kwargs: calls.append("notice_raw") or {"complete": True},
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.award_vnext,
        "collect_service_opening",
        lambda *args, **kwargs: calls.append("opening_raw") or {"complete": True},
    )

    def fake_normalize(dataset, **kwargs):
        if dataset == g2b_vnext_pipeline.award_projection.OPENING_DATASET:
            calls.append("first_rank")
        else:
            calls.append("final_award")
        return {"processed": 1}

    monkeypatch.setattr(g2b_vnext_pipeline.award_projection, "normalize_dataset", fake_normalize)
    monkeypatch.setattr(
        g2b_vnext_pipeline.award_vnext,
        "collect_service_awards",
        lambda *args, **kwargs: calls.append("award_raw") or {"complete": True},
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.contract_vnext,
        "collect_all",
        lambda *args, **kwargs: calls.append("contract_raw") or {"complete": True},
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.contract_projection,
        "normalize_contracts",
        lambda **kwargs: calls.append("contract_link") or {"linked": 1},
    )

    result = g2b_vnext_pipeline.collect_service_lifecycle(
        "2026-09-01", "2026-09-15", max_pages=1,
    )

    assert calls == [
        "notice_raw",
        "opening_raw",
        "first_rank",
        "award_raw",
        "final_award",
        "contract_raw",
        "contract_link",
    ]
    assert list(result) == calls
