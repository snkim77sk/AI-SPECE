import time

import runtime_identity


def test_build_commit_info_reports_source_and_preserves_precedence(monkeypatch, tmp_path):
    env = {}
    empty = runtime_identity.build_commit_info(env, root=tmp_path)
    assert empty["build_commit"] == ""
    assert empty["build_commit_source"] == ""

    fallback = "1234567890abcdef1234567890abcdef12345678"
    source = "abcdef0123456789abcdef0123456789abcdef01"
    platform = "fedcba9876543210fedcba9876543210fedcba98"

    env["G2B_VNEXT_SOURCE_COMMIT_SHA"] = source
    assert runtime_identity.build_commit_info(env, root=tmp_path)["build_commit_source"] == "G2B_VNEXT_SOURCE_COMMIT_SHA"

    env["G2B_BUILD_COMMIT"] = fallback
    info = runtime_identity.build_commit_info(env, root=tmp_path)
    assert info["build_commit"] == fallback
    assert info["build_commit_source"] == "G2B_BUILD_COMMIT"

    env["GITHUB_SHA"] = platform
    info = runtime_identity.build_commit_info(env, root=tmp_path)
    assert info["build_commit"] == platform
    assert info["build_commit_source"] == "GITHUB_SHA"
    assert info["build_commit_platform_authoritative"] is True
    assert info["build_commit_authoritative"] is True


def test_checkout_sha_beats_stale_manual_fallback(tmp_path):
    checkout = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    stale = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    git_dir = tmp_path / ".git"
    ref = git_dir / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    ref.write_text(checkout + "\n", encoding="utf-8")

    info = runtime_identity.build_commit_info(
        {"G2B_BUILD_COMMIT": stale},
        root=tmp_path,
    )

    assert info["build_commit"] == checkout
    assert info["build_commit_source"] == "GIT_CHECKOUT"
    assert info["git_checkout_commit"] == checkout
    assert info["configured_build_commit"] == stale
    assert info["build_commit_mismatch"] is True


def test_process_identity_is_stable_with_monotonic_uptime():
    first = runtime_identity.process_identity_info()
    time.sleep(0.01)
    second = runtime_identity.process_identity_info()

    assert len(first["process_instance_id"]) == 24
    int(first["process_instance_id"], 16)
    assert first["process_instance_id"] == second["process_instance_id"]
    assert first["process_started_at_utc"] == second["process_started_at_utc"]
    assert first["process_started_at_utc"].endswith("Z")
    assert second["process_uptime_seconds"] >= first["process_uptime_seconds"] >= 0


def _complete_identity():
    return {
        "build_commit": "a" * 40,
        "build_commit_source": "GIT_CHECKOUT",
        "source_fingerprint": "b" * 64,
        "source_fingerprint_complete": True,
        "source_fingerprint_missing": (),
        "process_instance_id": "c" * 24,
        "process_started_at_utc": "2026-10-05T14:00:00Z",
        "process_uptime_seconds": 12.0,
    }


def test_deployment_verdict_expected_target_match_and_stale():
    identity = _complete_identity()
    matched = runtime_identity.deployment_verdict_info(
        identity,
        phase="G2B_VNEXT_CLEAN",
        environ={
            "G2B_EXPECTED_BUILD_COMMIT": "a" * 40,
            "G2B_EXPECTED_SOURCE_FINGERPRINT": "b" * 64,
        },
    )
    assert matched["deployment_verdict"] == "ACTIVE"
    assert matched["deployment_freshness_verified"] is True

    stale = runtime_identity.deployment_verdict_info(
        identity,
        phase="G2B_VNEXT_CLEAN",
        environ={"G2B_EXPECTED_BUILD_COMMIT": "d" * 40},
    )
    assert stale["deployment_verdict"] == "STALE"
    assert "BUILD_COMMIT_MISMATCH" in stale["deployment_verdict_reasons"]


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
