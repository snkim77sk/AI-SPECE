"""Tamper-evident provenance for bounded vNext validation evidence.

The key is intentionally runtime-only. GitHub validation workflows generate an
ephemeral key file for the job and never upload it. Reports and replay proofs are
therefore verifiable inside the authorized validation runtime but cannot be made
trustworthy merely by editing JSON/SQLite metadata after the fact.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

PROVENANCE_VERSION = 1
KEY_ENV = "G2B_VNEXT_PROVENANCE_KEY"
KEY_FILE_ENV = "G2B_VNEXT_PROVENANCE_KEY_FILE"
_MIN_KEY_BYTES = 32
_RUNTIME_FIELDS = (
    "github_repository",
    "github_run_id",
    "github_run_attempt",
    "github_workflow_ref",
)


class VNextProvenanceError(RuntimeError):
    pass


def _key_bytes():
    """Load provenance key, preferring the explicit ephemeral key file.

    Live validation workflows create ``G2B_VNEXT_PROVENANCE_KEY_FILE`` under
    ``$RUNNER_TEMP``. Once that file path is present it is authoritative: an
    unrelated raw environment value must never shadow it, and an unreadable file
    must fail closed instead of silently falling back to another key.
    """
    path_text = str(os.getenv(KEY_FILE_ENV, "") or "").strip()
    if path_text:
        path = Path(path_text)
        try:
            key = path.read_bytes().strip()
        except OSError as exc:
            raise VNextProvenanceError(
                f"PROVENANCE_KEY_UNREADABLE:{type(exc).__name__}"
            ) from None
    else:
        raw = str(os.getenv(KEY_ENV, "") or "")
        if not raw:
            raise VNextProvenanceError("PROVENANCE_KEY_REQUIRED")
        key = raw.encode("utf-8")
    if len(key) < _MIN_KEY_BYTES:
        raise VNextProvenanceError("PROVENANCE_KEY_TOO_SHORT")
    return key


def runtime_identity():
    return {
        "github_repository": str(os.getenv("GITHUB_REPOSITORY", "") or "").strip(),
        "github_run_id": str(os.getenv("GITHUB_RUN_ID", "") or "").strip(),
        "github_run_attempt": str(os.getenv("GITHUB_RUN_ATTEMPT", "") or "").strip(),
        "github_workflow_ref": str(os.getenv("GITHUB_WORKFLOW_REF", "") or "").strip(),
    }


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _message(payload, purpose, identity):
    return _canonical(
        {
            "version": PROVENANCE_VERSION,
            "purpose": str(purpose),
            "runtime": identity,
            "payload": payload,
        }
    )


def seal_evidence(payload, *, purpose):
    if not isinstance(payload, dict):
        raise VNextProvenanceError("PROVENANCE_PAYLOAD_INVALID")
    identity = runtime_identity()
    signature = hmac.new(
        _key_bytes(), _message(payload, purpose, identity), hashlib.sha256
    ).hexdigest()
    return {
        "version": PROVENANCE_VERSION,
        "purpose": str(purpose),
        **identity,
        "hmac_sha256": signature,
    }


def verify_evidence(payload, provenance, *, purpose):
    if not isinstance(payload, dict):
        raise VNextProvenanceError("PROVENANCE_PAYLOAD_INVALID")
    if not isinstance(provenance, dict):
        raise VNextProvenanceError("PROVENANCE_REQUIRED")
    if int(provenance.get("version") or 0) != PROVENANCE_VERSION:
        raise VNextProvenanceError("PROVENANCE_VERSION_MISMATCH")
    if str(provenance.get("purpose") or "") != str(purpose):
        raise VNextProvenanceError("PROVENANCE_PURPOSE_MISMATCH")

    recorded_identity = {
        key: str(provenance.get(key) or "").strip() for key in _RUNTIME_FIELDS
    }
    current_identity = runtime_identity()
    for key in _RUNTIME_FIELDS:
        current = current_identity[key]
        if current and recorded_identity[key] != current:
            raise VNextProvenanceError("PROVENANCE_RUNTIME_IDENTITY_MISMATCH")

    expected = hmac.new(
        _key_bytes(), _message(payload, purpose, recorded_identity), hashlib.sha256
    ).hexdigest()
    actual = str(provenance.get("hmac_sha256") or "").strip()
    if not actual or not hmac.compare_digest(actual, expected):
        raise VNextProvenanceError("PROVENANCE_SIGNATURE_MISMATCH")
    return True


def seal_report(report, *, purpose):
    if not isinstance(report, dict):
        raise VNextProvenanceError("PROVENANCE_REPORT_INVALID")
    body = dict(report)
    body.pop("provenance", None)
    body["provenance"] = seal_evidence(body, purpose=purpose)
    return body


def verify_report(report, *, purpose):
    if not isinstance(report, dict):
        raise VNextProvenanceError("PROVENANCE_REPORT_INVALID")
    body = dict(report)
    provenance = body.pop("provenance", None)
    verify_evidence(body, provenance, purpose=purpose)
    return body
