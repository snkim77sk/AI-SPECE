"""Fail-closed approval gate for vNext live historical collection.

A caller cannot unlock historical source traffic with a boolean alone.  It must
supply the sanitized JSON report produced by ``scripts/g2b_bounded_canary.py``.
No credential or source-row value is consumed or returned here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

APPROVAL_VERSION = 1
MAX_APPROVAL_AGE = dt.timedelta(hours=24)


class LiveApprovalError(RuntimeError):
    pass


def _load(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (str, os.PathLike)) and str(value).strip():
        path = Path(value)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise LiveApprovalError(f"CANARY_APPROVAL_UNREADABLE:{type(exc).__name__}") from None
        if isinstance(data, dict):
            return data
    raise LiveApprovalError("CANARY_APPROVAL_REQUIRED")


def _utc(value):
    text = str(value or "").strip()
    if not text:
        raise LiveApprovalError("CANARY_APPROVAL_TIMESTAMP_MISSING")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise LiveApprovalError("CANARY_APPROVAL_TIMESTAMP_INVALID") from None
    if parsed.tzinfo is None:
        raise LiveApprovalError("CANARY_APPROVAL_TIMESTAMP_NAIVE")
    return parsed.astimezone(dt.timezone.utc)


def require_canary_approval(value, *, now=None):
    """Return sanitized approval metadata or raise before any source request.

    The bounded canary is a schema/fact gate, not whole-source completeness proof.
    Therefore ``whole_source_completeness_verified`` is deliberately expected to
    remain false; completeness is established later by receipt/replay auditing.
    """
    report = _load(value)
    if int(report.get("approval_version") or 0) != APPROVAL_VERSION:
        raise LiveApprovalError("CANARY_APPROVAL_VERSION_MISMATCH")
    if report.get("production_db_touched") is not False:
        raise LiveApprovalError("CANARY_APPROVAL_PRODUCTION_DB_UNSAFE")
    if report.get("bulk_collection_attempted") is not False:
        raise LiveApprovalError("CANARY_APPROVAL_BULK_UNSAFE")
    if report.get("live_allowed_for_this_invocation") is not True:
        raise LiveApprovalError("CANARY_APPROVAL_NOT_LIVE")

    g2b = report.get("g2b") if isinstance(report.get("g2b"), dict) else {}
    budget = report.get("budget") if isinstance(report.get("budget"), dict) else {}
    if g2b.get("status") != "CONCLUSIVE" or g2b.get("live_request_attempted") is not True:
        raise LiveApprovalError("CANARY_APPROVAL_G2B_NOT_CONCLUSIVE")
    if (budget.get("status") != "SCHEMA_PASS" or budget.get("schema_verified") is not True
            or budget.get("live_request_attempted") is not True):
        raise LiveApprovalError("CANARY_APPROVAL_BUDGET_NOT_VERIFIED")
    if report.get("all_sample_schemas_verified") is not True:
        raise LiveApprovalError("CANARY_APPROVAL_SAMPLE_GATE_FAILED")

    generated = _utc(report.get("generated_at_utc"))
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    age = current - generated
    if age < dt.timedelta(minutes=-5):
        raise LiveApprovalError("CANARY_APPROVAL_FROM_FUTURE")
    if age > MAX_APPROVAL_AGE:
        raise LiveApprovalError("CANARY_APPROVAL_EXPIRED")

    expected_sha = str(os.getenv("GITHUB_SHA", "") or "").strip()
    source_sha = str(report.get("source_commit_sha") or "").strip()
    if expected_sha:
        if not source_sha:
            raise LiveApprovalError("CANARY_APPROVAL_SOURCE_SHA_MISSING")
        if source_sha != expected_sha:
            raise LiveApprovalError("CANARY_APPROVAL_SOURCE_SHA_MISMATCH")

    return {
        "approval_version": APPROVAL_VERSION,
        "generated_at_utc": generated.isoformat(),
        "source_commit_sha": source_sha,
        "g2b_status": "CONCLUSIVE",
        "budget_status": "SCHEMA_PASS",
    }
