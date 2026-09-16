import pytest
import g2b_vnext_pipeline


def test_service_lifecycle_runs_in_required_order(monkeypatch):
    calls = []
    monkeypatch.setattr(g2b_vnext_pipeline.bid_vnext, "collect_all",
                        lambda *a, **k: calls.append("notice_raw") or {"complete": True})
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, "collect_service_opening",
                        lambda *a, **k: calls.append("opening_raw") or {"complete": True})
    def fake_normalize(dataset, **kwargs):
        calls.append("first_rank" if dataset == g2b_vnext_pipeline.award_projection.OPENING_DATASET else "final_award")
        return {"processed": 1}
    monkeypatch.setattr(g2b_vnext_pipeline.award_projection, "normalize_dataset", fake_normalize)
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, "collect_service_awards",
                        lambda *a, **k: calls.append("award_raw") or {"complete": True})
    monkeypatch.setattr(g2b_vnext_pipeline.contract_vnext, "collect_all",
                        lambda *a, **k: calls.append("contract_raw") or {"complete": True})
    monkeypatch.setattr(g2b_vnext_pipeline.contract_projection, "normalize_contracts",
                        lambda **k: calls.append("contract_link") or {"linked": 1})
    monkeypatch.setattr(g2b_vnext_pipeline.classification_vnext, "classify_all",
                        lambda **k: calls.append("classification") or {"classified": 4})
    result = g2b_vnext_pipeline.collect_service_lifecycle("2026-09-01", "2026-09-15")
    assert calls == ["notice_raw","opening_raw","first_rank","award_raw","final_award",
                     "contract_raw","contract_link","classification"]
    assert result["status"] == "COMPLETE"


def test_service_lifecycle_can_skip_classification_for_raw_only_probe(monkeypatch):
    monkeypatch.setattr(g2b_vnext_pipeline.bid_vnext, "collect_all", lambda *a, **k: {"complete": True})
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, "collect_service_opening", lambda *a, **k: {"complete": True})
    monkeypatch.setattr(g2b_vnext_pipeline.award_projection, "normalize_dataset", lambda *a, **k: {})
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, "collect_service_awards", lambda *a, **k: {"complete": True})
    monkeypatch.setattr(g2b_vnext_pipeline.contract_vnext, "collect_all", lambda *a, **k: {"complete": True})
    monkeypatch.setattr(g2b_vnext_pipeline.contract_projection, "normalize_contracts", lambda **k: {})
    result = g2b_vnext_pipeline.collect_service_lifecycle("2026-09-01", "2026-09-01", run_classification=False)
    assert "classification" not in result
    assert result["status"] == "COMPLETE"


def test_pipeline_blocks_normalization_when_raw_is_partial(monkeypatch):
    called = {"opening": False, "normalize": False}
    monkeypatch.setattr(g2b_vnext_pipeline.bid_vnext, "collect_all", lambda *a, **k: {"complete": False})
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, "collect_service_opening",
                        lambda *a, **k: called.update(opening=True))
    monkeypatch.setattr(g2b_vnext_pipeline.award_projection, "normalize_dataset",
                        lambda *a, **k: called.update(normalize=True))
    with pytest.raises(RuntimeError, match="incomplete"):
        g2b_vnext_pipeline.collect_service_lifecycle("2026-09-01", "2026-09-01", max_pages=1)
    assert called == {"opening": False, "normalize": False}
