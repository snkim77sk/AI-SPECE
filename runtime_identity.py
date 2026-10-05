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
    "G2B_VNEXT_SOURCE_COMMIT_SHA",
    "G2B_BUILD_COMMIT",
)


def _valid_sha(value):
    text = str(value or "").strip()
    return (
        7 <= len(text) <= 64
        and all(ch in "0123456789abcdefABCDEF" for ch in text)
    )


def build_commit_info(environ=None):
    env = os.environ if environ is None else environ
    for name in _BUILD_COMMIT_ENV_NAMES:
        value = str(env.get(name, "") or "").strip()
        if _valid_sha(value):
            return {
                "build_commit": value.lower(),
                "build_commit_source": name,
                "build_commit_platform_authoritative": name == "GITHUB_SHA",
            }
    return {
        "build_commit": "",
        "build_commit_source": "",
        "build_commit_platform_authoritative": False,
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
    "source_fingerprint_info",
    "runtime_identity",
]
