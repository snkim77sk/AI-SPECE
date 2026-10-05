"""Dependency-free runtime identity for G2B deployment verification.

This module intentionally uses only the Python standard library so emergency
HTTP/ASGI recovery can identify the deployed artifact without importing the
full application or touching PostgreSQL.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path

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


def runtime_identity():
    return {
        **build_commit_info(),
        **source_fingerprint_info(),
    }


__all__ = [
    "MANIFEST_VERSION",
    "CORE_SOURCE_FILES",
    "build_commit_info",
    "git_checkout_commit",
    "source_fingerprint_info",
    "runtime_identity",
]
