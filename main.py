"""Top-level Cafe24 AI SPACE entrypoint for SINSUNG G2B DATA VIEW."""
import os
import secrets
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

sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

# Stable data base: exact target detail-item codes, live G2B field aliases,
# line amount normalization and one-time data cleanup.
from sinsung_runtime_fix import apply_runtime_fixes, prepare_database_once  # noqa: E402
prepare_database_once()

from db import connect, get_setting, set_setting  # noqa: E402

# Cafe24 should start safely even when DASHBOARD_SECRET was not manually added.
# Generate it once, store it only in persistent SQLite, and reuse it on restart.
if not os.getenv("DASHBOARD_SECRET"):
    session_secret = get_setting("internal_dashboard_secret", "")
    if len(session_secret) < 32:
        session_secret = secrets.token_urlsafe(48)
        set_setting("internal_dashboard_secret", session_secret)
    os.environ["DASHBOARD_SECRET"] = session_secret

# Force first-admin setup once on this migration. This removes any legacy test
# account (including admin/1234) but does not repeat after the user creates the
# new administrator account.
if get_setting("v241_first_admin_reset", "") != "1":
    with connect() as conn:
        conn.execute("DELETE FROM users")
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name='users'")
        except Exception:
            pass
    set_setting("v241_first_admin_reset", "1")

apply_runtime_fixes()

# Restore the familiar procurement screen without reverting stabilized data
# collector/authentication logic.
from sinsung_ui_restore import apply_ui_restore  # noqa: E402
apply_ui_restore()

# Preserve legacy explicit blank region= links.
from sinsung_region_fix import apply_region_fix  # noqa: E402
apply_region_fix()

# Official 지방재정365 detailed-project expenditure collector and budget UI.
from sinsung_budget_monitor import apply_budget_monitor  # noqa: E402
apply_budget_monitor()
from sinsung_budget_flash_fix import apply_budget_flash_fix  # noqa: E402
apply_budget_flash_fix()

# Stable nationwide filter and budget API-key settings UI.
from sinsung_v251_patch import apply_v251_patch  # noqa: E402
apply_v251_patch()
from sinsung_v252_patch import apply_v252_patch  # noqa: E402
apply_v252_patch()

# Release numbering is intentionally simple from this rollback baseline.
# 1.0 = stable rollback baseline, 1.1/1.2 = verified minor updates,
# 2.0 = next major architecture/data-collector release.
RELEASE_VERSION = "1.0"

import server as server_module  # noqa: E402
server_module.APP_VERSION = RELEASE_VERSION

import app as app_module  # noqa: E402
app_module.APP_VERSION = RELEASE_VERSION
app_module.app.title = "신성라이텍 G2B DATA VIEW"
app_module.app.version = RELEASE_VERSION

app = app_module.app

__all__ = ["app"]
