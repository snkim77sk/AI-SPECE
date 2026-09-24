import json
import urllib.error

import pytest

import vnext_http
import vnext_live_gate
import vnext_source_guard
import vnext_stability
from vnext_collection import collect_pages, verified_checkpoint
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint


def _collect(dataset, fetch):
    return collect_pages(
        dataset=dataset,
        scope="scope",
        range_start="2026-09-16",
        range_end="2026-09-16",
        page_size=2,
        max_pages=3,
        resume=True,
        fetch=fetch,
        identity=lambda row: row["id"],
        source_system="TEST",
        source_operation="TEST_LIST",
        source_date=lambda row: "2026-09-16",
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
    )


def _offline_receipt(dataset):
    pages = {1: [{"id": "A"}], 2: []}
    result = _collect(dataset, lambda page, size: (list(pages.get(page, [])), None))
    assert result["complete"] is True
    return pages


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def _success_payload(row_id="A", total=1):
    return json.dumps({
        "response": {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {"items": {"item": [{"id": row_id}]}, "totalCount": total},
        }
    }).encode("utf-8")


def _prepare_live(monkeypatch, sha_char="d"):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: sha_char * 40)
    monkeypatch.setattr(vnext_http, "_quota_take", lambda *a, **k: (1, 99))


def test_live_collection_rejects_pure_synthetic_fetch(monkeypatch):
    _prepare_live(monkeypatch)

    with vnext_source_guard.bounded_canary_source_context(max_requests=3):
        with pytest.raises(
            vnext_source_guard.VNextSourceTransportAttestationError,
            match="SOURCE_COLLECTION_TRANSPORT_NOT_ATTESTED",
        ):
            _collect("live_synthetic_collection", lambda page, size: ([{"id": "A"}], 1))
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 0
        assert context["requests_used"] == 0
        assert context["transport_successes_used"] == 0

    cp = get_checkpoint("live_synthetic_collection", "scope")
    assert cp["status"] == "FAILED"
    assert verified_checkpoint(cp) is False


def test_failed_official_request_then_synthetic_replay_is_rejected(monkeypatch):
    pages = _offline_receipt("failed_then_synthetic")
    _prepare_live(monkeypatch, "e")

    def fail(request, timeout=0):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fail)

    def fetch(page, size):
        try:
            vnext_http.request(
                f"https://example.invalid/source?page={page}",
                "attested_replay",
                retries=1,
            )
        except vnext_http.VNextApiError:
            pass
        return list(pages.get(page, [])), None

    with vnext_source_guard.bounded_canary_source_context(max_requests=4):
        checked = vnext_stability.verify_checkpoint_source(
            dataset="failed_then_synthetic",
            scope="scope",
            fetch=fetch,
            identity=lambda row: row["id"],
        )
        assert checked["stable"] is False
        assert checked["reason"] == "SOURCE_REPLAY_TRANSPORT_NOT_ATTESTED"
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 1
        assert context["transport_successes_used"] == 0


def test_successful_official_request_cannot_attest_substituted_result(monkeypatch):
    pages = _offline_receipt("substituted_replay")
    _prepare_live(monkeypatch, "f")
    monkeypatch.setattr(
        vnext_http.urllib.request,
        "urlopen",
        lambda request, timeout=0: _Response(_success_payload("DIFFERENT", 1)),
    )

    def substituted_fetch(page, size):
        vnext_http.request(
            f"https://example.invalid/source?page={page}",
            "attested_replay",
            retries=1,
        )
        return list(pages.get(page, [])), None

    with vnext_source_guard.bounded_canary_source_context(max_requests=4):
        checked = vnext_stability.verify_checkpoint_source(
            dataset="substituted_replay",
            scope="scope",
            fetch=substituted_fetch,
            identity=lambda row: row["id"],
        )
        assert checked["stable"] is False
        assert checked["reason"] == "SOURCE_REPLAY_TRANSPORT_NOT_ATTESTED"
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 1
        assert context["transport_successes_used"] == 1

    cp = get_checkpoint("substituted_replay", "scope")
    assert vnext_stability.stability_verified_checkpoint(cp) is False
