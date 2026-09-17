import pytest

import lofin_vnext_http
import vnext_http
import vnext_live_gate
import vnext_source_guard


def test_g2b_direct_request_is_blocked_before_quota_or_network(monkeypatch):
    monkeypatch.setattr(vnext_http, "_quota_take", lambda *a, **k: (_ for _ in ()).throw(AssertionError("quota must not be touched")))
    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")))
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_REQUIRED"):
        vnext_http.request("https://example.invalid", "bid_notice", retries=1)


def test_lofin_direct_request_is_blocked_before_quota_or_network(monkeypatch):
    monkeypatch.setattr(lofin_vnext_http, "_quota_take", lambda: (_ for _ in ()).throw(AssertionError("quota must not be touched")))
    monkeypatch.setattr(lofin_vnext_http.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")))
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_REQUIRED"):
        lofin_vnext_http._request({"Key": "redacted"}, retries=1)


def test_bounded_canary_context_has_hard_attempt_budget_and_resets(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "a" * 40)
    with vnext_source_guard.bounded_canary_source_context(max_requests=2):
        assert vnext_source_guard.require_source_request_context() == vnext_source_guard.BOUNDED_CANARY
        assert vnext_source_guard.require_source_request_context() == vnext_source_guard.BOUNDED_CANARY
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="BUDGET_EXHAUSTED"):
            vnext_source_guard.require_source_request_context()
    assert vnext_source_guard.current_source_request_context() is None
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_REQUIRED"):
        vnext_source_guard.require_source_request_context()


def test_small_validation_context_requires_same_commit_canary(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "b" * 40)
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda path: {"source_commit_sha": "b" * 40},
    )
    with vnext_source_guard.small_validation_source_context("approval.json", max_requests=1):
        assert vnext_source_guard.require_source_request_context() == vnext_source_guard.SMALL_VALIDATION


def test_small_validation_context_rejects_commit_mismatch(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "c" * 40)
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda path: {"source_commit_sha": "d" * 40},
    )
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="SOURCE_SHA_MISMATCH"):
        with vnext_source_guard.small_validation_source_context("approval.json", max_requests=1):
            pass
