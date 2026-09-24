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


def test_service_lifecycle_runs_replay_and_raw_coverage_gates_before_normalization(monkeypatch):
    calls = _complete_collectors(monkeypatch)
    monkeypatch.setattr(
        g2b_vnext_pipeline,
        "_verify_service_stability",
        lambda *args, **kwargs: (
            calls.append("source_stability") or {"all": {"stable": True, "fresh": True}},
            "",
        ),
    )
    coverage = {"all_current_raw_covered_by_plan": True, "current_raw_rows": 4}
    monkeypatch.setattr(
        g2b_vnext_pipeline,
        "_require_service_raw_coverage",
        lambda *args, **kwargs: calls.append("raw_coverage") or coverage,
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
        "raw_coverage",
        "first_rank",
        "final_award",
        "contract_link",
        "classification",
    ]
    assert result["source_stability"]["all"]["fresh"] is True
    assert result["trusted_raw_coverage"] is coverage
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
    assert "trusted_raw_coverage" not in result
    assert "first_rank" not in result
    assert "final_award" not in result
    assert "contract_link" not in result


def test_service_lifecycle_blocks_normalization_when_current_service_raw_is_outside_scope(monkeypatch):
    _complete_collectors(monkeypatch)
    monkeypatch.setattr(
        g2b_vnext_pipeline,
        "_verify_service_stability",
        lambda *a, **k: ({"all": {"stable": True, "fresh": True}}, ""),
    )

    def reject(*args, **kwargs):
        raise g2b_vnext_pipeline.FinalizeCoverageError(
            "FINALIZE_CURRENT_RAW_OUTSIDE_PLAN:bid_notice_service:OTHER"
        )

    monkeypatch.setattr(g2b_vnext_pipeline, "_require_service_raw_coverage", reject)
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
    monkeypatch.setattr(
        g2b_vnext_pipeline.classification_vnext,
        "classify_all",
        lambda **k: (_ for _ in ()).throw(AssertionError("classification must stay blocked")),
    )

    result = g2b_vnext_pipeline.collect_service_lifecycle(
        "2026-09-01", "2026-09-01", max_pages=1,
    )
    assert result["complete"] is False
    assert result["stopped_on"] == "service_raw_coverage"
    assert result["raw_coverage_error"].startswith("FINALIZE_CURRENT_RAW_OUTSIDE_PLAN")
    assert "first_rank" not in result
    assert "final_award" not in result
    assert "contract_link" not in result
    assert "classification" not in result


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
