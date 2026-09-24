import pytest

import vnext_provenance


def test_explicit_key_file_cannot_be_shadowed_by_raw_environment_key(monkeypatch, tmp_path):
    key_file = tmp_path / "provenance.key"
    key_file.write_text("f" * 64, encoding="utf-8")
    monkeypatch.setenv(vnext_provenance.KEY_FILE_ENV, str(key_file))
    monkeypatch.setenv(vnext_provenance.KEY_ENV, "r" * 64)

    payload = {"scope": "one-day", "complete": True}
    sealed = vnext_provenance.seal_evidence(payload, purpose="TEST_KEY_PRECEDENCE")
    assert vnext_provenance.verify_evidence(
        payload, sealed, purpose="TEST_KEY_PRECEDENCE"
    ) is True

    # Removing the authoritative file path exposes the different raw key. If the
    # file really had precedence, the previously sealed evidence must now fail.
    monkeypatch.delenv(vnext_provenance.KEY_FILE_ENV)
    with pytest.raises(
        vnext_provenance.VNextProvenanceError,
        match="PROVENANCE_SIGNATURE_MISMATCH",
    ):
        vnext_provenance.verify_evidence(
            payload, sealed, purpose="TEST_KEY_PRECEDENCE"
        )


def test_configured_key_file_fails_closed_instead_of_falling_back_to_raw_key(monkeypatch, tmp_path):
    missing = tmp_path / "missing.key"
    monkeypatch.setenv(vnext_provenance.KEY_FILE_ENV, str(missing))
    monkeypatch.setenv(vnext_provenance.KEY_ENV, "r" * 64)

    with pytest.raises(
        vnext_provenance.VNextProvenanceError,
        match="PROVENANCE_KEY_UNREADABLE",
    ):
        vnext_provenance.seal_evidence(
            {"scope": "one-day"}, purpose="TEST_KEY_FILE_FAIL_CLOSED"
        )
