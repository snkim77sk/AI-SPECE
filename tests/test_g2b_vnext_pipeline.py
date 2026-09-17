import g2b_vnext_pipeline


def _complete_collectors(monkeypatch, calls=None):
    calls = calls if calls is not None else []
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
    return calls


def test_service_lifecycle_runs_replay_gate_before_normalization(monkeypatch):
    calls = _complete_collectors(monkeypatch)
    monkeypatch.setattr(
        g2b_vnext_pipeline,
        "_verify_service_stability",
        lambda *args, **kwargs: (
            calls.append("source_stability") or {"all": {"stable": True, "fresh": True}},
            "",
        ),
    )

    def fake_normalize(dataset, **kwargs):
        if dataset == g2b_vnext_pipeline.award_projection.OPENING_DATASET:
            calls.append("first_rank")
        else:
            calls.append("final_award")
        return {"processed": 1}

    monkeypatch.setattr(g2b_vnext_pipeline.award_projection, "normalize_dataset", fake_normalize)
    monkeypatch.setattr(
        g2b_vnext_pipeline.contract_projection,
        "normalize_contracts",
        lambda **kwargs: calls.append("contract_link") or {"linked": 1},
    )

    def fake_classify_all(**kwargs):
        calls.append("classification")
        assert tuple(kwargs["datasets"]) == g2b_vnext_pipeline.SERVICE_CLASSIFICATION_DATASETS
        assert kwargs["batch_size"] == 250
        return {"classified": 4}

    monkeypatch.setattr(g2b_vnext_pipeline.classification_vnext, "classify_all", fake_classify_all)

    result = g2b_vnext_pipeline.collect_service_lifecycle(
        "2026-09-01", "2026-09-15", max_pages=1, classify_batch_size=250,
    )

    assert calls == [
        "notice_raw",
        "opening_raw",
        "award_raw",
        "contract_raw",
        "source_stability",
        "first_rank",
        "final_award",
        "contract_link",
        "classification",
    ]
    assert result["source_stability"]["all"]["fresh"] is True
    assert result["complete"] is True


def test_service_lifecycle_blocks_normalization_when_stability_is_not_fresh(monkeypatch):
    _complete_collectors(monkeypatch)
    monkeypatch.setattr(
        g2b_vnext_pipeline,
        "_verify_service_stability",
        lambda *a, **k: (
            {"opening_result_service": {"stable": False, "fresh": False}},
            "opening_result_service",
        ),
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.award_projection,
        "normalize_dataset",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("normalization must stay blocked")),
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.contract_projection,
        "normalize_contracts",
        lambda **k: (_ for _ in ()).throw(AssertionError("normalization must stay blocked")),
    )

    result = g2b_vnext_pipeline.collect_service_lifecycle(
        "2026-09-01", "2026-09-01", max_pages=1,
    )
    assert result["complete"] is False
    assert result["stopped_on"] == "opening_result_service_stability"
    assert "first_rank" not in result
    assert "final_award" not in result
    assert "contract_link" not in result


def test_service_stability_helper_requires_each_checkpoint_to_be_fresh(monkeypatch):
    verified = []
    checkpoints = {}

    def fake_verify(*, dataset, scope, fetch, identity, **kwargs):
        verified.append((dataset, scope))
        checkpoints[dataset] = {"dataset": dataset, "scope_key": scope}
        return {"stable": True, "reason": "VERIFIED", "replayed_pages": 1}

    monkeypatch.setattr(
        g2b_vnext_pipeline.vnext_stability,
        "verify_checkpoint_source",
        fake_verify,
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline,
        "get_checkpoint",
        lambda dataset, scope: checkpoints.get(dataset),
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.vnext_stability,
        "stability_fresh_checkpoint",
        lambda cp: cp is not None and cp["dataset"] != "award_result_service",
    )
    monkeypatch.setattr(
        g2b_vnext_pipeline.vnext_stability,
        "stability_verified_at",
        lambda cp: "2026-09-17T00:00:00+00:00" if cp else "",
    )

    records, failed = g2b_vnext_pipeline._verify_service_stability(
        "2026-09-16", "2026-09-16"
    )
    assert [dataset for dataset, _scope in verified] == [
        "bid_notice_service",
        "opening_result_service",
        "award_result_service",
    ]
    assert all(scope == "2026-09-16:2026-09-16" for _dataset, scope in verified)
    assert failed == "award_result_service"
    assert records["award_result_service"]["fresh"] is False
    assert "contract_service" not in records


def test_service_lifecycle_can_skip_classification_for_raw_only_probe(monkeypatch):
    monkeypatch.setattr(g2b_vnext_pipeline.bid_vnext, "collect_all", lambda *a, **k: {})
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, "collect_service_opening", lambda *a, **k: {})
    monkeypatch.setattr(g2b_vnext_pipeline.award_projection, "normalize_dataset", lambda *a, **k: {})
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, "collect_service_awards", lambda *a, **k: {})
    monkeypatch.setattr(g2b_vnext_pipeline.contract_vnext, "collect_all", lambda *a, **k: {})
    monkeypatch.setattr(g2b_vnext_pipeline.contract_projection, "normalize_contracts", lambda **k: {})
    monkeypatch.setattr(
        g2b_vnext_pipeline.classification_vnext, "classify_all",
        lambda **k: (_ for _ in ()).throw(AssertionError("classification should be skipped")),
    )

    result = g2b_vnext_pipeline.collect_service_lifecycle(
        "2026-09-01", "2026-09-01", max_pages=1, run_classification=False,
    )
    assert "classification" not in result
