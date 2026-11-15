"""Canonical product version loaded from VERSION.txt."""
from pathlib import Path

_VERSION_FILE = Path(__file__).with_name("VERSION.txt")


def load_version():
    raw = _VERSION_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        raise RuntimeError("VERSION.txt is empty")
    version = raw.split()[-1].strip()
    if not version:
        raise RuntimeError("VERSION.txt does not contain a version")
    return version


APP_VERSION = load_version()
VERSION = APP_VERSION

__all__ = ["APP_VERSION", "VERSION", "load_version"]
