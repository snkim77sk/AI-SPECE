from contextvars import copy_context
import urllib.error

import pytest

import db
import lofin_vnext_http
import vnext_http
import vnext_live_gate
import vnext_source_guard


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "source-guard.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    return path


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


def test_copied_contexts_share_one_attempt_budget(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "e" * 40)
    with vnext_source_guard.bounded_canary_source_context(max_requests=2):
        first = copy_context()
        second = copy_context()
        assert first.run(vnext_source_guard.require_source_request_context) == vnext_source_guard.BOUNDED_CANARY
        assert second.run(vnext_source_guard.require_source_request_context) == vnext_source_guard.BOUNDED_CANARY
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="BUDGET_EXHAUSTED"):
            vnext_source_guard.require_source_request_context()


def test_copied_context_is_closed_when_owner_scope_exits(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "f" * 40)
    inherited = None
    with vnext_source_guard.bounded_canary_source_context(max_requests=2):
        inherited = copy_context()
        assert inherited.run(vnext_source_guard.require_source_request_context) == vnext_source_guard.BOUNDED_CANARY
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_CLOSED"):
        inherited.run(vnext_source_guard.require_source_request_context)


def test_context_resets_after_exception(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "1" * 40)
    with pytest.raises(ValueError, match="synthetic"):
        with vnext_source_guard.bounded_canary_source_context(max_requests=1):
            raise ValueError("synthetic")
    assert vnext_source_guard.current_source_request_context() is None


def test_nested_context_is_rejected_without_corrupting_outer(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "2" * 40)
    with vnext_source_guard.bounded_canary_source_context(max_requests=1):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_NESTED"):
            with vnext_source_guard.bounded_canary_source_context(max_requests=1):
                pass
        assert vnext_source_guard.require_source_request_context() == vnext_source_guard.BOUNDED_CANARY


def test_each_retry_consumes_shared_context_permit(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "3" * 40)
    monkeypatch.setattr(vnext_http.time, "sleep", lambda *a, **k: None)
    calls = []

    def fail(req, timeout=45):
        calls.append(1)
        raise urllib.error.URLError("synthetic")

    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fail)
    with vnext_source_guard.bounded_canary_source_context(max_requests=1):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="BUDGET_EXHAUSTED"):
            vnext_http.request("https://example.invalid", "bid_notice", retries=2)
    assert len(calls) == 1


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
