import json

import vnext_http
import vnext_live_gate
import vnext_source_guard
import vnext_stability
from vnext_collection import collect_pages
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint


def _receipt_complete(dataset="attested_replay", scope="scope"):
    pages = {1: [{"id": "A"}], 2: []}
    result = collect_pages(
        dataset=dataset,
        scope=scope,
        range_start="2026-09-16",
        range_end="2026-09-16",
        page_size=2,
        max_pages=3,
        resume=True,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
        source_system="TEST",
        source_operation="TEST_LIST",
        source_date=lambda row: "2026-09-16",
        preserve=preserve_raw,
        checkpoint=save_checkpoint,
        lookup=get_checkpoint,
    )
    assert result["complete"] is True
    return pages


def test_live_context_synthetic_replay_cannot_mint_stability_proof(monkeypatch):
    pages = _receipt_complete()
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "a" * 40)

    with vnext_source_guard.bounded_canary_source_context(max_requests=4):
        checked = vnext_stability.verify_checkpoint_source(
            dataset="attested_replay",
            scope="scope",
            fetch=lambda page, size: (list(pages.get(page, [])), None),
            identity=lambda row: row["id"],
        )
        assert checked["stable"] is False
        assert checked["reason"] == "SOURCE_REPLAY_TRANSPORT_NOT_ATTESTED"
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 0
        assert context["permits_used"] == 0

    cp = get_checkpoint("attested_replay", "scope")
    assert vnext_stability.stability_verified_checkpoint(cp) is False
    assert vnext_stability.stability_fresh_checkpoint(cp) is False


def test_live_context_direct_permit_spoof_still_cannot_mint_stability_proof(monkeypatch):
    pages = _receipt_complete("permit_spoof", "scope")
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "c" * 40)

    def spoofed_fetch(page, size):
        # Consume a valid bounded permit directly, but never enter either official
        # low-level transport. This used to satisfy the requests-used replay check.
        vnext_source_guard.require_source_request_context()
        return list(pages.get(page, [])), None

    with vnext_source_guard.bounded_canary_source_context(max_requests=4):
        checked = vnext_stability.verify_checkpoint_source(
            dataset="permit_spoof",
            scope="scope",
            fetch=spoofed_fetch,
            identity=lambda row: row["id"],
        )
        assert checked["stable"] is False
        assert checked["reason"] == "SOURCE_REPLAY_TRANSPORT_NOT_ATTESTED"
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 1
        assert context["requests_used"] == 0

    cp = get_checkpoint("permit_spoof", "scope")
    assert vnext_stability.stability_verified_checkpoint(cp) is False
    assert vnext_stability.stability_fresh_checkpoint(cp) is False


def test_live_context_official_transport_replay_can_mint_stability_proof(monkeypatch):
    _receipt_complete("official_replay", "scope")
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "b" * 40)
    monkeypatch.setattr(vnext_http, "_quota_take", lambda *a, **k: (1, 99))

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.payload

    def fake_urlopen(request, timeout=0):
        page = 2 if "page=2" in request.full_url else 1
        items = {"item": [{"id": "A"}]} if page == 1 else {}
        payload = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "OK"},
                "body": {"items": items},
            }
        }
        return Response(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fake_urlopen)

    def official_fetch(page, size):
        return vnext_http.request(
            f"https://example.invalid/source?page={page}",
            "attested_replay",
            retries=1,
        )

    with vnext_source_guard.bounded_canary_source_context(max_requests=4):
        checked = vnext_stability.verify_checkpoint_source(
            dataset="official_replay",
            scope="scope",
            fetch=official_fetch,
            identity=lambda row: row["id"],
        )
        assert checked["stable"] is True
        assert checked["reason"] == "VERIFIED"
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 2
        assert context["permits_used"] == 2

    cp = get_checkpoint("official_replay", "scope")
    assert vnext_stability.stability_verified_checkpoint(cp) is True
    assert vnext_stability.stability_fresh_checkpoint(cp) is True
