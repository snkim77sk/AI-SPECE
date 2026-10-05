import os

import runtime_identity


def test_build_commit_info_reports_source_and_preserves_precedence(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("G2B_BUILD_COMMIT", raising=False)
    monkeypatch.delenv("G2B_VNEXT_SOURCE_COMMIT_SHA", raising=False)

    empty = runtime_identity.build_commit_info()
    assert empty["build_commit"] == ""
    assert empty["build_commit_source"] == ""
    assert empty["build_commit_platform_authoritative"] is False

    fallback = "1234567890abcdef1234567890abcdef12345678"
    source = "abcdef0123456789abcdef0123456789abcdef01"
    platform = "fedcba9876543210fedcba9876543210fedcba98"

    monkeypatch.setenv("G2B_VNEXT_SOURCE_COMMIT_SHA", source)
    assert runtime_identity.build_commit_info()["build_commit_source"] == "G2B_VNEXT_SOURCE_COMMIT_SHA"

    monkeypatch.setenv("G2B_BUILD_COMMIT", fallback)
    info = runtime_identity.build_commit_info()
    assert info["build_commit"] == fallback
    assert info["build_commit_source"] == "G2B_BUILD_COMMIT"
    assert info["build_commit_platform_authoritative"] is False

    monkeypatch.setenv("GITHUB_SHA", platform)
    info = runtime_identity.build_commit_info()
    assert info["build_commit"] == platform
    assert info["build_commit_source"] == "GITHUB_SHA"
    assert info["build_commit_platform_authoritative"] is True


def test_source_fingerprint_is_complete_and_deterministic():
    runtime_identity.source_fingerprint_info.cache_clear()
    first = runtime_identity.source_fingerprint_info()
    second = runtime_identity.source_fingerprint_info()

    assert first == second
    assert first["source_fingerprint_manifest"] == "G2B_CORE_SOURCE_V1"
    assert first["source_fingerprint_complete"] is True
    assert first["source_fingerprint_missing"] == ()
    assert len(first["source_fingerprint"]) == 64
    int(first["source_fingerprint"], 16)
