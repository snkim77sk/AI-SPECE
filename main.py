"""Top-level Cafe24 AI SPACE entrypoint for SINSUNG G2B DATA VIEW."""
import os
import sys
import time


def _truth(value: str) -> bool:
    return str(value or "").lower() in ("1", "true", "yes", "on")


# Configure KST and persistent storage before importing the application.
os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

if not os.getenv("G2B_DB_PATH"):
    persistent_dir = "/app/user_data"
    if os.path.isdir(persistent_dir) and os.access(persistent_dir, os.W_OK):
        os.environ["G2B_DB_PATH"] = os.path.join(persistent_dir, "g2b.sqlite3")

# Never create sample data. Legacy sample rows may be removed safely at startup.
os.environ["G2B_SEED_SAMPLE"] = "0"
os.environ["G2B_PURGE_SAMPLE_DATA"] = "1"

TEST_MODE = _truth(os.getenv("G2B_TEST_MODE", "0"))
if TEST_MODE:
    os.environ["G2B_TEST_MODE"] = "1"
    os.environ.setdefault("DASHBOARD_SECRET", "local-test-secret-not-for-production-0001")

sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

# Procurement groups, authentication, branding, and amount normalization are
# implemented directly in the current app modules. Do not apply legacy runtime
# monkey patches here; they have incompatible function signatures and codes.
import app as app_module  # noqa: E402

app = app_module.app

__all__ = ["app"]
