import json

import pytest

import vnext_http
import vnext_live_gate
import vnext_source_guard
import vnext_stability
from vnext_collection import collect_pages
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint


def test_active_source_context_rejects_runtime_sha_drift_before_quota_or_network(monkeypatch):
    state = {"sha": "a" * 40}
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: state["sha"])
    monkeypatch.setattr(
        vnext_http,
        "_quota_take",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("quota must not be touched")),
    )
    monkeypatch.setattr(
        vnext_http.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")),
    )

    with vnext_source_guard.bounded_canary_source_context(max_requests=1):
        state["sha"] = "b" * 40
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="RUNTIME_SHA_MISMATCH",
        ):
            vnext_http.request("https://example.invalid", "synthetic", retries=1)
        assert vnext_source_guard.current_source_request_context()["requests_used"] == 0


def _collect_receipt_complete(dataset="proof_forge", scope="scope"):
    pages = {1: [{"id": "A"}], 2: []}
    result = collect_pages(
        dataset=dataset,
        scope=scope,
        range_start="2026-09-01",
        range_end="2026-09-01",
        page_size=2,
        max_pages=3,
        resume=True,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
        source_system="TEST",
        source_operation="TEST_LIST",
        source_date=lambda row: "2026-09-01",
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
    )
    assert result["complete"] is True
    return pages


def test_direct_checkpoint_stability_metadata_cannot_forge_complete(monkeypatch):
    pages = _collect_receipt_complete()
    cp = get_checkpoint("proof_forge", "scope")
    meta = json.loads(cp["cursor_value"])
    generation = meta["generation"]
    meta["stability_recollect_required"] = False
    meta["stability"] = {
        "status": "VERIFIED",
        "generation": generation,
        "page_count": 2,
        "digest": "0" * 64,
        "verified_at_utc": "2026-09-17T12:00:00+00:00",
        "source_commit_sha": "synthetic-sha",
    }
    save_checkpoint(
        "proof_forge",
        "scope",
        cursor_value=json.dumps(meta, sort_keys=True),
        range_start=cp["range_start"],
        range_end=cp["range_end"],
        page_no=cp["page_no"],
        page_size=cp["page_size"],
        last_page_fingerprint=cp["last_page_fingerprint"],
        source_total=cp["source_total"],
        fetched_count=cp["fetched_count"],
        saved_count=cp["saved_count"],
        status="COMPLETE",
        last_error="",
    )
    forged = get_checkpoint("proof_forge", "scope")
    assert vnext_stability.stability_verified_checkpoint(forged) is False
    assert vnext_stability.stability_fresh_checkpoint(forged) is False

    checked = vnext_stability.verify_checkpoint_source(
        dataset="proof_forge",
        scope="scope",
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
    )
    assert checked["stable"] is True
    verified = get_checkpoint("proof_forge", "scope")
    assert vnext_stability.stability_verified_checkpoint(verified) is True
    assert vnext_stability.stability_fresh_checkpoint(verified) is True


def test_signed_stability_timestamp_tamper_invalidates_entire_proof():
    pages = _collect_receipt_complete("proof_timestamp", "scope")
    checked = vnext_stability.verify_checkpoint_source(
        dataset="proof_timestamp",
        scope="scope",
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
    )
    assert checked["stable"] is True

    cp = get_checkpoint("proof_timestamp", "scope")
    meta = json.loads(cp["cursor_value"])
    meta["stability"]["verified_at_utc"] = "2099-01-01T00:00:00+00:00"
    save_checkpoint(
        "proof_timestamp",
        "scope",
        cursor_value=json.dumps(meta, sort_keys=True),
        range_start=cp["range_start"],
        range_end=cp["range_end"],
        page_no=cp["page_no"],
        page_size=cp["page_size"],
        last_page_fingerprint=cp["last_page_fingerprint"],
        source_total=cp["source_total"],
        fetched_count=cp["fetched_count"],
        saved_count=cp["saved_count"],
        status=cp["status"],
        last_error=cp["last_error"],
    )
    tampered = get_checkpoint("proof_timestamp", "scope")
    assert vnext_stability.stability_verified_checkpoint(tampered) is False
    assert vnext_stability.stability_verified_at(tampered) == ""
