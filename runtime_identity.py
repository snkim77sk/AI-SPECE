"""Dependency-free runtime identity for G2B deployment verification.

This module intentionally uses only the Python standard library so emergency
HTTP/ASGI recovery can identify the deployed artifact without importing the
full application or touching PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import secrets
import time

MANIFEST_VERSION = "G2B_CORE_SOURCE_V1"
CORE_SOURCE_FILES = (
    "VERSION.txt",
    "runtime_identity.py",
    "main.py",
    "run.py",
    "vnext_clean_app.py",
    "runtime_role.py",
    "requirements.txt",
)
_BUILD_COMMIT_ENV_NAMES = (
    "GITHUB_SHA",
    "G2B_BUILD_COMMIT",
    "G2B_VNEXT_SOURCE_COMMIT_SHA",
)


_PROCESS_STARTED_MONOTONIC = time.monotonic()
_PROCESS_STARTED_AT_UTC = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
_PROCESS_INSTANCE_ID = secrets.token_hex(12)


def process_identity_info():
    """Return safe process-lifetime identity without storage or network access."""
    return {
        "process_started_at_utc": _PROCESS_STARTED_AT_UTC,
        "process_instance_id": _PROCESS_INSTANCE_ID,
        "process_uptime_seconds": max(
            0.0,
            round(time.monotonic() - _PROCESS_STARTED_MONOTONIC, 3),
        ),
    }


def _valid_sha(value):
    text = str(value or "").strip()
    return (
        7 <= len(text) <= 64
        and all(ch in "0123456789abcdefABCDEF" for ch in text)
    )


def _normalized_sha(value):
    text = str(value or "").strip()
    return text.lower() if _valid_sha(text) else ""


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _git_metadata_dirs(root):
    root = Path(root).resolve()
    dotgit = root / ".git"
    git_dir = None

    if dotgit.is_dir():
        git_dir = dotgit
    elif dotgit.is_file():
        pointer = _read_text(dotgit)
        if pointer.lower().startswith("gitdir:"):
            value = pointer.split(":", 1)[1].strip()
            candidate = Path(value)
            git_dir = candidate if candidate.is_absolute() else (root / candidate).resolve()

    if git_dir is None or not git_dir.is_dir():
        return ()

    dirs = [git_dir]
    common = _read_text(git_dir / "commondir")
    if common:
        candidate = Path(common)
        common_dir = candidate if candidate.is_absolute() else (git_dir / candidate).resolve()
        if common_dir.is_dir() and common_dir not in dirs:
            dirs.append(common_dir)
    return tuple(dirs)


def git_checkout_commit(root=None):
    root = Path(__file__).resolve().parent if root is None else Path(root).resolve()
    dirs = _git_metadata_dirs(root)
    if not dirs:
        return ""

    head = _read_text(dirs[0] / "HEAD")
    if not head:
        return ""
    if not head.lower().startswith("ref:"):
        return _normalized_sha(head)

    ref = head.split(":", 1)[1].strip()
    ref_path = Path(*[part for part in ref.split("/") if part])
    for base in dirs:
        value = _normalized_sha(_read_text(base / ref_path))
        if value:
            return value

    for base in dirs:
        packed = base / "packed-refs"
        try:
            lines = packed.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == ref:
                value = _normalized_sha(parts[0])
                if value:
                    return value
    return ""


def _manual_build_commit(env):
    for name in ("G2B_BUILD_COMMIT", "G2B_VNEXT_SOURCE_COMMIT_SHA"):
        value = _normalized_sha(env.get(name, ""))
        if value:
            return value, name
    return "", ""


def _same_sha(left, right):
    left = _normalized_sha(left)
    right = _normalized_sha(right)
    return bool(left and right and (left.startswith(right) or right.startswith(left)))


def build_commit_info(environ=None, root=None):
    env = os.environ if environ is None else environ
    platform = _normalized_sha(env.get("GITHUB_SHA", ""))
    checkout = git_checkout_commit(root)
    manual, manual_source = _manual_build_commit(env)

    if platform:
        selected, source = platform, "GITHUB_SHA"
    elif checkout:
        selected, source = checkout, "GIT_CHECKOUT"
    elif manual:
        selected, source = manual, manual_source
    else:
        selected, source = "", ""

    return {
        "build_commit": selected,
        "build_commit_source": source,
        "build_commit_platform_authoritative": source == "GITHUB_SHA",
        "build_commit_checkout_authoritative": source == "GIT_CHECKOUT",
        "build_commit_authoritative": source in {"GITHUB_SHA", "GIT_CHECKOUT"},
        "git_checkout_commit": checkout,
        "configured_build_commit": manual,
        "configured_build_commit_source": manual_source,
        "build_commit_mismatch": bool(
            checkout and manual and not _same_sha(checkout, manual)
        ),
    }


@lru_cache(maxsize=1)
def source_fingerprint_info():
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    missing = []

    digest.update((MANIFEST_VERSION + "\n").encode("utf-8"))
    for relative in CORE_SOURCE_FILES:
        path = root / relative
        digest.update((relative + "\0").encode("utf-8"))
        try:
            payload = path.read_bytes()
        except OSError:
            missing.append(relative)
            digest.update(b"MISSING\0")
            continue
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")

    return {
        "source_fingerprint": digest.hexdigest(),
        "source_fingerprint_manifest": MANIFEST_VERSION,
        "source_fingerprint_complete": not missing,
        "source_fingerprint_missing": tuple(missing),
    }


def _normalized_fingerprint(value):
    text = str(value or "").strip().lower()
    if len(text) == 64 and all(ch in "0123456789abcdef" for ch in text):
        return text
    return ""


def deployment_verdict_info(
    identity=None,
    *,
    phase="",
    recovery_mode=False,
    environ=None,
):
    """Summarize local deployment state without claiming remote-main freshness.

    STALE is emitted only when an explicit expected commit/fingerprint is
    supplied and the running artifact disagrees with it.
    """
    env = os.environ if environ is None else environ
    current = dict(identity or {
        **build_commit_info(env),
        **source_fingerprint_info(),
        **process_identity_info(),
    })

    raw_expected_commit = str(
        env.get("G2B_EXPECTED_BUILD_COMMIT", "") or ""
    ).strip()
    raw_expected_fingerprint = str(
        env.get("G2B_EXPECTED_SOURCE_FINGERPRINT", "") or ""
    ).strip()
    expected_commit = _normalized_sha(raw_expected_commit)
    expected_fingerprint = _normalized_fingerprint(
        raw_expected_fingerprint
    )
    actual_commit = _normalized_sha(current.get("build_commit", ""))
    actual_fingerprint = _normalized_fingerprint(
        current.get("source_fingerprint", "")
    )

    stale_reasons = []
    incomplete_reasons = []

    if raw_expected_commit and not expected_commit:
        incomplete_reasons.append("EXPECTED_BUILD_COMMIT_INVALID")
    if raw_expected_fingerprint and not expected_fingerprint:
        incomplete_reasons.append("EXPECTED_SOURCE_FINGERPRINT_INVALID")

    if expected_commit:
        if not actual_commit:
            incomplete_reasons.append("EXPECTED_BUILD_COMMIT_UNVERIFIABLE")
        elif not _same_sha(actual_commit, expected_commit):
            stale_reasons.append("BUILD_COMMIT_MISMATCH")

    if expected_fingerprint:
        if not actual_fingerprint:
            incomplete_reasons.append("EXPECTED_SOURCE_FINGERPRINT_UNVERIFIABLE")
        elif actual_fingerprint != expected_fingerprint:
            stale_reasons.append("SOURCE_FINGERPRINT_MISMATCH")

    if not bool(current.get("source_fingerprint_complete")):
        incomplete_reasons.append("SOURCE_FINGERPRINT_INCOMPLETE")
    if not str(current.get("process_instance_id") or ""):
        incomplete_reasons.append("PROCESS_INSTANCE_MISSING")
    if not str(current.get("process_started_at_utc") or ""):
        incomplete_reasons.append("PROCESS_START_TIME_MISSING")
    if not actual_commit and not actual_fingerprint:
        incomplete_reasons.append("ARTIFACT_IDENTITY_MISSING")

    expected_target_present = bool(
        raw_expected_commit or raw_expected_fingerprint
    )
    freshness_checked = expected_target_present
    freshness_verified = bool(
        expected_target_present
        and not stale_reasons
        and not incomplete_reasons
    )

    if stale_reasons:
        verdict = "STALE"
        reasons = stale_reasons + incomplete_reasons
    elif incomplete_reasons:
        verdict = "IDENTITY_INCOMPLETE"
        reasons = incomplete_reasons
    elif recovery_mode or str(phase or "").startswith("PHASE0"):
        verdict = "SAFE_PHASE0"
        reasons = ["RECOVERY_HTTP_ACTIVE"]
    else:
        verdict = "ACTIVE"
        reasons = ["PROCESS_ACTIVE"]

    return {
        "deployment_verdict": verdict,
        "deployment_verdict_reasons": tuple(reasons),
        "deployment_verdict_scope": (
            "EXPECTED_TARGET" if expected_target_present else "LOCAL_PROCESS"
        ),
        "deployment_expected_build_commit": expected_commit,
        "deployment_expected_source_fingerprint": expected_fingerprint,
        "deployment_freshness_checked": freshness_checked,
        "deployment_freshness_verified": freshness_verified,
    }


def runtime_identity():
    return {
        **build_commit_info(),
        **source_fingerprint_info(),
        **process_identity_info(),
    }


__all__ = [
    "MANIFEST_VERSION",
    "CORE_SOURCE_FILES",
    "build_commit_info",
    "deployment_verdict_info",
    "git_checkout_commit",
    "process_identity_info",
    "source_fingerprint_info",
    "runtime_identity",
]
