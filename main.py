"""Top-level Cafe24 AI SPACE entrypoint for LIGHTING SKETCH G2B DATA VIEW.

This shim keeps the existing nested application layout intact while supplying
safe test defaults and an AI SPACE persistent SQLite location before importing
the real FastAPI app.
"""
import os
import secrets
import sys


def _truth(value: str) -> bool:
    return str(value or "").lower() in ("1", "true", "yes", "on")


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "LIGHTING_SKETCH_G2B_GITHUB_READY_v2.3.1 (1)")
if not os.path.isdir(APP_DIR):
    raise RuntimeError(f"application folder not found: {APP_DIR}")

# Cafe24 AI SPACE preserves runtime data under /app/user_data. Keep an explicit
# G2B_DB_PATH override if supplied. Otherwise prefer the persistent mount when
# it is available; local runs fall back to db.py's project data directory.
if not os.getenv("G2B_DB_PATH"):
    persistent_dir = "/app/user_data"
    if os.path.isdir(persistent_dir) and os.access(persistent_dir, os.W_OK):
        os.environ["G2B_DB_PATH"] = os.path.join(persistent_dir, "g2b.sqlite3")

# Production-data-only policy: never seed generated/sample data.
os.environ["G2B_SEED_SAMPLE"] = "0"
# The nested app uses this flag to purge any legacy is_sample=1 rows on startup.
os.environ["G2B_PURGE_SAMPLE_DATA"] = "1"

TEST_MODE = _truth(os.getenv("G2B_TEST_MODE", "1"))
if TEST_MODE:
    os.environ["G2B_TEST_MODE"] = "1"
    os.environ.setdefault("DASHBOARD_USER", "admin")
    os.environ.setdefault("DASHBOARD_PASSWORD", "1234")
    # Test secret is generated per container start and is never stored in GitHub.
    os.environ.setdefault("DASHBOARD_SECRET", secrets.token_urlsafe(48))
    # Do NOT force G2B_AUTO_SYNC here. SQLite owns the switch so the settings
    # screen can enable/disable automatic collection after deployment.
    os.environ.setdefault("G2B_AUTO_SYNC_HOURS", "3")
    os.environ.setdefault("G2B_AUTO_SYNC_DAYS", "14")
    os.environ.setdefault("G2B_API_DAILY_LIMIT", "900")
    os.environ.setdefault("G2B_ALLOW_API_URL_EDIT", "0")

sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)

from app import app  # noqa: E402

__all__ = ["app"]
