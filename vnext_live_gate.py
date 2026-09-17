"""Fail-closed approval gates for vNext live source collection.

A caller cannot unlock historical source traffic with booleans alone. Bounded canary
proof is required for the first one-day validation, and a successful one-day
validation proof is additionally required before wider historical collection.
No credential or source-row value is consumed or returned here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

APPROVAL_VERSION = 1
SMALL_VALIDATION_APPROVAL_VERSION = 1
MAX_APPROVAL_AGE = dt.timedelta(hours=24)


class LiveApprovalError(RuntimeError):
    pass


def runtime_source_sha():
    """Return the commit identity of the code that is about to issue live requests."""
    explicit = str(os.getenv("G2B_VNEXT_SOURCE_COMMIT_SHA", "") or "").strip()
    github = str(os.getenv("GITHUB_SHA", "") or "").strip()
    if explicit and github and explicit != github:
        raise LiveApprovalError("CANARY_APPROVAL_RUNTIME_SHA_CONFLICT")
    return github or explicit


def _load(value, required_code):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (str, os.PathLike)) and str(value).strip():
        path = Path(value)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise LiveApprovalError(f"{required_code}_UNREADABLE:{type(exc).__name__}") from None
        if isinstance(data, dict):
            return data
    raise LiveApprovalError(f"{required_code}_REQUIRED")


def _utc(value, prefix):
    text = str(value or "").strip()
    if not text:
        raise LiveApprovalError(f"{prefix}_TIMESTAMP_MISSING")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise LiveApprovalError(f"{prefix}_TIMESTAMP_INVALID") from None
    if parsed.tzinfo is None:
        raise LiveApprovalError(f"{prefix}_TIMESTAMP_NAIVE")
    return parsed.astimezone(dt.timezone.utc)


def _freshness(value, *, now, prefix):
    generated = _utc(value, prefix)
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    age = current - generated
    if age < dt.timedelta(minutes=-5):
        raise LiveApprovalError(f"{prefix}_FROM_FUTURE")
    if age > MAX_APPROVAL_AGE:
        raise LiveApprovalError(f"{prefix}_EXPIRED")
    return generated


def _matching_runtime_sha(report, *, prefix):
    source_sha = str(report.get("source_commit_sha") or "").strip()
    if not source_sha:
        raise LiveApprovalError(f"{prefix}_SOURCE_SHA_MISSING")
    expected_sha = runtime_source_sha()
    if not expected_sha:
        raise LiveApprovalError(f"{prefix}_RUNTIME_SHA_MISSING")
    if source_sha != expected_sha:
        raise LiveApprovalError(f"{prefix}_SOURCE_SHA_MISMATCH")
    return source_sha


def require_canary_approval(value, *, now=None):
    """Require recent same-runtime-commit sanitized bounded-canary evidence."""
    report = _load(value, "CANARY_APPROVAL")
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

    generated = _freshness(report.get("generated_at_utc"), now=now, prefix="CANARY_APPROVAL")
    source_sha = _matching_runtime_sha(report, prefix="CANARY_APPROVAL")
    return {
        "approval_version": APPROVAL_VERSION,
        "generated_at_utc": generated.isoformat(),
        "source_commit_sha": source_sha,
        "g2b_status": "CONCLUSIVE",
        "budget_status": "SCHEMA_PASS",
    }


def require_small_validation_approval(value, *, now=None):
    """Require one recent successful requested-scope validation before expansion."""
    report = _load(value, "SMALL_VALIDATION_APPROVAL")
    if int(report.get("small_validation_approval_version") or 0) != SMALL_VALIDATION_APPROVAL_VERSION:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_VERSION_MISMATCH")
    if report.get("validation_only") is not True:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_SCOPE_INVALID")
    if report.get("production_db_touched") is not False:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_PRODUCTION_DB_UNSAFE")
    if report.get("db_artifact_exported") is not False:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_DB_EXPORT_UNSAFE")
    if report.get("requested_validation_scope_complete") is not True:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_SCOPE_INCOMPLETE")
    if report.get("whole_source_completeness_verified") is not False:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_WHOLE_SOURCE_CLAIM_INVALID")
    if str(report.get("validation_scope") or "") != "one recent completed KST date only":
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_SCOPE_INVALID")
    pages = int(report.get("max_pages_per_stage") or 0)
    if pages < 1 or pages > 2:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_PAGE_BUDGET_INVALID")
    try:
        dt.date.fromisoformat(str(report.get("date_kst") or ""))
    except ValueError:
        raise LiveApprovalError("SMALL_VALIDATION_APPROVAL_DATE_INVALID") from None

    generated = _freshness(
        report.get("generated_at_utc"), now=now, prefix="SMALL_VALIDATION_APPROVAL"
    )
    source_sha = _matching_runtime_sha(report, prefix="SMALL_VALIDATION_APPROVAL")
    return {
        "approval_version": SMALL_VALIDATION_APPROVAL_VERSION,
        "generated_at_utc": generated.isoformat(),
        "source_commit_sha": source_sha,
        "date_kst": str(report.get("date_kst")),
        "requested_validation_scope_complete": True,
    }
