"""Top-level Cafe24 AI SPACE entrypoint for SINSUNG G2B DATA VIEW 2.0."""
import os
import secrets
import sys
import time

VERSION = "2.0"


def _truth(value: str) -> bool:
    return str(value or "").lower() in ("1", "true", "yes", "on")


os.environ["TZ"] = "Asia/Seoul"
if hasattr(time, "tzset"):
    time.tzset()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

if not os.getenv("G2B_DB_PATH"):
    persistent_dir = "/app/user_data"
    if os.path.isdir(persistent_dir) and os.access(persistent_dir, os.W_OK):
        os.environ["G2B_DB_PATH"] = os.path.join(persistent_dir, "g2b.sqlite3")

# Real-data-only policy and manual-only verification for 2.0.
os.environ["G2B_SEED_SAMPLE"] = "0"
os.environ["G2B_PURGE_SAMPLE_DATA"] = "1"
os.environ["G2B_AUTO_SYNC"] = "0"

TEST_MODE = _truth(os.getenv("G2B_TEST_MODE", "0"))
if TEST_MODE:
    os.environ["G2B_TEST_MODE"] = "1"

sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

# Initialize schema only. In 2.0 the old collector monkey-patch is NOT applied.
from sinsung_runtime_fix import patch_server, prepare_database_once  # noqa: E402
prepare_database_once()

from db import get_setting, set_setting  # noqa: E402

# Persistent session secret; never expose it in the UI.
if not os.getenv("DASHBOARD_SECRET"):
    session_secret = get_setting("internal_dashboard_secret", "")
    if len(session_secret) < 32:
        session_secret = secrets.token_urlsafe(48)
        set_setting("internal_dashboard_secret", session_secret)
    os.environ["DASHBOARD_SECRET"] = session_secret

# 2.0 clean reset: collected data/logs only. Users, API keys and company settings remain.
from sinsung_v200_reset import reset_data_once  # noqa: E402
reset_data_once()

# Branding/auth/server UI only. Collector patching is intentionally skipped.
patch_server()

from sinsung_ui_restore import apply_ui_restore  # noqa: E402
apply_ui_restore()

from sinsung_region_fix import apply_region_fix  # noqa: E402
apply_region_fix()

# Existing verified budget module stays, but its data was reset once above.
from sinsung_budget_monitor import apply_budget_monitor  # noqa: E402
apply_budget_monitor()
from sinsung_budget_flash_fix import apply_budget_flash_fix  # noqa: E402
apply_budget_flash_fix()

from sinsung_v251_patch import apply_v251_patch  # noqa: E402
apply_v251_patch()
from sinsung_v252_patch import apply_v252_patch  # noqa: E402
apply_v252_patch()

# No v2.5.3~v2.6.3 API collector patches are executed in 2.0.
from sinsung_v200_ui import apply_v200_ui  # noqa: E402
apply_v200_ui()

import server as server_module  # noqa: E402
server_module.APP_VERSION = VERSION

import app as app_module  # noqa: E402
app_module.APP_VERSION = VERSION
app_module.app.title = "신성라이텍 G2B DATA VIEW"
app_module.app.version = VERSION

app = app_module.app

__all__ = ["app"]
