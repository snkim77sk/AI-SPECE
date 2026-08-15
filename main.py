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

# v2.4.1 data stabilization: one-time cleanup, exact 12 detail-item codes,
# live G2B field aliases and line amount normalization.
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

# Restore the familiar procurement screen without reverting the stabilized data
# collector/authentication logic.
from sinsung_ui_restore import apply_ui_restore  # noqa: E402
apply_ui_restore()

# Preserve legacy explicit blank region= links.
from sinsung_region_fix import apply_region_fix  # noqa: E402
apply_region_fix()

# v2.5.0: official 지방재정365 detailed-project expenditure collector and
# budget monitoring UI. This is isolated from procurement/bid collectors.
from sinsung_budget_monitor import apply_budget_monitor  # noqa: E402
apply_budget_monitor()
from sinsung_budget_flash_fix import apply_budget_flash_fix  # noqa: E402
apply_budget_flash_fix()

# v2.5.1: use an explicit nationwide sentinel so 전국 never falls back to the
# configured default region, and surface the budget API-key box at page top.
from sinsung_v251_patch import apply_v251_patch  # noqa: E402
apply_v251_patch()

# v2.5.2: also expose the budget API key controls on the main settings page.
from sinsung_v252_patch import apply_v252_patch  # noqa: E402
apply_v252_patch()

# v2.5.3: build shopping procurement history from 2025-01-01 through today,
# preserving monthly resume and the existing UPSERT duplicate protection.
from sinsung_v253_patch import apply_v253_patch  # noqa: E402
apply_v253_patch()

# v2.5.4: correct shopping API date-time parameter names/format, reset the false
# empty-complete backfill state once, and keep the 2025 history builder active.
from sinsung_v254_patch import VERSION, apply_v254_patch  # noqa: E402
apply_v254_patch()

import app as app_module  # noqa: E402

# Keep the public FastAPI health/version metadata aligned with the backend.
app_module.APP_VERSION = VERSION
app_module.app.title = "신성라이텍 G2B DATA VIEW"
app_module.app.version = VERSION

app = app_module.app

__all__ = ["app"]
