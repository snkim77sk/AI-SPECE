import pytest

import bid_vnext
import g2b_vnext_pipeline
import vnext_http
import vnext_source_guard


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
        "first_rank",
        "final_award",
        "contract_link",
        "classification",
    ]
    assert [k for k in result if k not in ("start_date", "end_date", "complete")] == calls
    assert result["complete"] is True


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


def test_service_lifecycle_direct_source_call_is_blocked_before_network(monkeypatch):
    # Even a caller that bypasses the approved historical entrypoint and invokes the
    # high-level lifecycle helper directly cannot reach source traffic.
    monkeypatch.setattr(bid_vnext, "_service_key", lambda: "synthetic-key")
    monkeypatch.setattr(
        vnext_http.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")),
    )
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_REQUIRED"):
        g2b_vnext_pipeline.collect_service_lifecycle(
            "2026-09-01", "2026-09-01", max_pages=1, run_classification=False,
        )
