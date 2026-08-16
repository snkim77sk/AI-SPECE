"""Top-level Cafe24 AI SPACE entrypoint for SINSUNG G2B DATA VIEW 2.2."""
import os
import secrets
import sys
import time

VERSION = "2.2"


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

# Real-data-only policy. 2.2 keeps the verified two-hour scheduler enabled.
os.environ["G2B_SEED_SAMPLE"] = "0"
os.environ["G2B_PURGE_SAMPLE_DATA"] = "1"
os.environ["G2B_AUTO_SYNC"] = "1"

TEST_MODE = _truth(os.getenv("G2B_TEST_MODE", "0"))
if TEST_MODE:
    os.environ["G2B_TEST_MODE"] = "1"
    # Local/CI HTTP tests must be able to receive the session cookie.
    os.environ.setdefault("G2B_COOKIE_SECURE", "0")
else:
    # Cafe24 public runtime is HTTPS. This must be set before patch_server()
    # imports server.py because server.COOKIE_SECURE is fixed at import time.
    os.environ["G2B_COOKIE_SECURE"] = "1"

sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

# Initialize schema only. The old v2.5.3~v2.6.3 collector patch chain remains disabled.
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

patch_server()

from sinsung_ui_restore import apply_ui_restore  # noqa: E402
apply_ui_restore()

from sinsung_region_fix import apply_region_fix  # noqa: E402
apply_region_fix()

# Budget module remains separate from the two-hour procurement scheduler.
from sinsung_budget_monitor import apply_budget_monitor  # noqa: E402
apply_budget_monitor()
from sinsung_budget_flash_fix import apply_budget_flash_fix  # noqa: E402
apply_budget_flash_fix()
from sinsung_budget_item_mapping import apply_budget_item_mapping  # noqa: E402
apply_budget_item_mapping()

from sinsung_v251_patch import apply_v251_patch  # noqa: E402
apply_v251_patch()
from sinsung_v252_patch import apply_v252_patch  # noqa: E402
apply_v252_patch()

# 2.2 operating/status UI; historical backfill remains disabled.
from sinsung_v220_ui import apply_v220_ui  # noqa: E402
apply_v220_ui()

# Minimal account feature only: public signup request + admin approval.
from sinsung_signup_approval import apply_signup_approval  # noqa: E402
apply_signup_approval()

import server as server_module  # noqa: E402
server_module.APP_VERSION = VERSION

# Cafe24-safe wrapper: FastAPI health responds immediately. Backend startup then
# recovers stale states, coordinates manual/auto collection and starts scheduler.
import sinsung_v220_app as app_module  # noqa: E402
app_module.APP_VERSION = VERSION
app_module.app.title = "신성라이텍 G2B DATA VIEW"
app_module.app.version = VERSION

app = app_module.app

__all__ = ["app"]
