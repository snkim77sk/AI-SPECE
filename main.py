"""Top-level Cafe24 AI SPACE entrypoint for SINSUNG G2B DATA VIEW."""
import os
import sys
import time


def _truth(value: str) -> bool:
    return str(value or "").lower() in ("1", "true", "yes", "on")


# Configure Korea Standard Time before importing any application modules.
os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Cafe24 AI SPACE persistent SQLite storage.
if not os.getenv("G2B_DB_PATH"):
    persistent_dir = "/app/user_data"
    if os.path.isdir(persistent_dir) and os.access(persistent_dir, os.W_OK):
        os.environ["G2B_DB_PATH"] = os.path.join(persistent_dir, "g2b.sqlite3")

# Real-data-only policy.
os.environ["G2B_SEED_SAMPLE"] = "0"
os.environ["G2B_PURGE_SAMPLE_DATA"] = "1"

# No admin/1234 fallback. An empty users table must go through first-admin setup.
TEST_MODE = _truth(os.getenv("G2B_TEST_MODE", "0"))
if TEST_MODE:
    os.environ["G2B_TEST_MODE"] = "1"
    os.environ.setdefault("DASHBOARD_SECRET", "local-test-secret-not-for-production-0001")

sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

# v2.4.1 performs a one-time cleanup of the previously imported bad shopping
# rows, then applies the exact 12 detail-item codes and live G2B field aliases.
from sinsung_runtime_fix import VERSION, apply_runtime_fixes, prepare_database_once  # noqa: E402
prepare_database_once()
apply_runtime_fixes()

import app as app_module  # noqa: E402

# Keep the public FastAPI health/version metadata aligned with the backend.
app_module.APP_VERSION = VERSION
app_module.app.title = "신성라이텍 G2B DATA VIEW"
app_module.app.version = VERSION

app = app_module.app

__all__ = ["app"]
