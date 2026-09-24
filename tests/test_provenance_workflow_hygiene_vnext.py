from pathlib import Path


WORKFLOW = Path(".github/workflows/g2b-vnext-small-validation.yml")


def _workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def test_ephemeral_provenance_key_is_removed_before_artifact_upload():
    text = _workflow_text()
    cleanup_name = "Remove ephemeral validation provenance key before artifact upload"
    upload_marker = "- uses: actions/upload-artifact@v4"

    cleanup_at = text.index(cleanup_name)
    upload_at = text.index(upload_marker)
    cleanup_block = text[cleanup_at:upload_at]

    assert cleanup_at < upload_at
    assert "if: always()" in cleanup_block
    assert 'rm -f -- "$G2B_VNEXT_PROVENANCE_KEY_FILE"' in cleanup_block
    assert '[ -e "$G2B_VNEXT_PROVENANCE_KEY_FILE" ]' in cleanup_block


def test_artifact_allowlist_does_not_include_key_or_runner_temp():
    text = _workflow_text()
    artifact_block = text[text.index("- uses: actions/upload-artifact@v4"):]

    assert "g2b-vnext-provenance.key" not in artifact_block
    assert "G2B_VNEXT_PROVENANCE_KEY" not in artifact_block
    assert "RUNNER_TEMP" not in artifact_block
    assert "verification/small_backfill.sqlite3" not in artifact_block
