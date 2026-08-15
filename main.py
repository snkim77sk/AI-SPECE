"""Top-level AI SPACE entrypoint shim for the current GitHub layout.

The application remains in the nested folder created during the initial GitHub
upload. This shim supplies safe test defaults, then imports the nested app.
"""
import os
import secrets
import sys


def _truth(value: str) -> bool:
    return str(value or "").lower() in ("1", "true", "yes", "on")


TEST_MODE = _truth(os.getenv("G2B_TEST_MODE", "1"))
if TEST_MODE:
    os.environ["G2B_TEST_MODE"] = "1"
    os.environ.setdefault("DASHBOARD_USER", "admin")
    if not os.getenv("DASHBOARD_PASSWORD"):
        os.environ["DASHBOARD_PASSWORD"] = "1234"
    if not os.getenv("DASHBOARD_SECRET"):
        os.environ["DASHBOARD_SECRET"] = secrets.token_urlsafe(48)
    os.environ.setdefault("G2B_SEED_SAMPLE", "1")
    os.environ.setdefault("G2B_AUTO_SYNC", "0")
    os.environ.setdefault("G2B_AUTO_SYNC_HOURS", "3")
    os.environ.setdefault("G2B_AUTO_SYNC_DAYS", "14")
    os.environ.setdefault("G2B_API_DAILY_LIMIT", "900")
    os.environ.setdefault("G2B_ALLOW_API_URL_EDIT", "0")

_APP_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "LIGHTING_SKETCH_G2B_GITHUB_READY_v2.3.1 (1)",
)
if not os.path.isdir(_APP_DIR):
    raise RuntimeError(f"application folder not found: {_APP_DIR}")

sys.path.insert(0, _APP_DIR)
os.chdir(_APP_DIR)

from app import app  # noqa: E402

__all__ = ["app"]
