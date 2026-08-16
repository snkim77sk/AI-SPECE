"""Top-level Cafe24 AI SPACE entrypoint for SINSUNG G2B DATA VIEW 2.2."""
import os
import re
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


def _apply_budget_admin_cleanup():
    """Keep budget API controls/status in one place and surface useful LOFIN errors."""
    import budget_sync as bs
    import scheduler as scheduler_module
    import server as s
    import sinsung_budget_monitor as bm

    if getattr(s, "_budget_admin_cleanup_applied", False):
        return

    original_page = s.budgets_html
    original_sync = bs.sync_budget_snapshot
    original_test = bm.test_budget_api

    def key_help():
        return (
            "지방재정365 전용 OpenAPI 인증키가 필요합니다. "
            "나라장터/공공데이터포털 서비스키와는 별도이며 지방재정365에서 발급한 키를 사용해야 합니다."
        )

    def explain_error(exc):
        text = str(exc or "").strip()
        lower = text.lower()
        if not bs.get_lofin_key() or "인증키가 설정되지" in text:
            return key_help()
        if (
            "인증" in text
            or "유효하지" in text
            or "401" in lower
            or "403" in lower
            or "unauthorized" in lower
            or "forbidden" in lower
        ):
            return key_help() + (f" · 원문: {text}" if text else "")
        return text or "지방재정365 API 응답을 확인할 수 없습니다."

    def checked_sync(fiscal_year=None, snapshot_date=None, keywords=None, max_pages=100):
        if not bs.get_lofin_key():
            raise bs.LofinApiError(key_help())
        try:
            result = original_sync(fiscal_year, snapshot_date, keywords, max_pages)
            last = bs.get_setting("last_budget_sync_result", "")
            if int(result or 0) == 0 and "연도검색 보완 실패:" in last:
                raise bs.LofinApiError(last.split("연도검색 보완 실패:", 1)[1].strip())
            return result
        except Exception as exc:
            raise bs.LofinApiError(explain_error(exc)) from exc

    def checked_test(fiscal_year=None, snapshot_date=None):
        if not bs.get_lofin_key():
            raise bs.LofinApiError(key_help())
        try:
            result = original_test(fiscal_year, snapshot_date)
            n, total, code, message = result
            # The mapping fallback intentionally used to suppress a year-query
            # exception. Recheck a zero result once so bad credentials are not
            # misreported as a successful connection with zero rows.
            if int(n or 0) == 0 and int(total or 0) == 0 and hasattr(bs, "fetch_budget_year_page"):
                year = int(fiscal_year or __import__("datetime").date.today().year)
                rows, year_total, year_code, year_message = bs.fetch_budget_year_page(
                    year, "조명", page=1, size=5
                )
                return len(rows), year_total, year_code, (
                    str(year_message or "") + (" · " if year_message else "") + "연도검색 확인"
                )
            return result
        except Exception as exc:
            raise bs.LofinApiError(explain_error(exc)) from exc

    bs.sync_budget_snapshot = checked_sync
    bs.test_budget_api = checked_test
    bm.sync_budget_snapshot = checked_sync
    bm.test_budget_api = checked_test
    scheduler_module.sync_budget_snapshot = checked_sync

    def budgets_html(qs):
        page = original_page(qs)

        # Normal users have these admin panels removed by the role guard before
        # this wrapper sees the page. Only rearrange the page when the admin block exists.
        start_marker = '<hr><div class="grid2"><section class="panel"><h3>지방재정365 연동</h3>'
        start = page.find(start_marker)
        if start < 0:
            return page
        end_marker = "</section></div>\n</section>"
        end = page.find(end_marker, start)
        if end < 0:
            return page
        block_end = end + len("</section></div>")
        admin_grid = page[start + len("<hr>"):block_end]
        page = page[:start] + page[block_end:]

        # Remove the separate top flash created by the legacy budget flash patch.
        page = re.sub(
            r'<div class="flash (?:error|ok)">.*?</div>',
            "",
            page,
            count=1,
            flags=re.S,
        )

        admin_grid = admin_grid.replace(
            "지방재정365에서 발급받은 인증키를 사용합니다. 인증키는 화면에 다시 표시하지 않습니다.",
            "지방재정365 로그인 후 발급한 OpenAPI 인증키만 사용합니다. 나라장터/공공데이터포털 서비스키와는 다릅니다. 인증키는 화면에 다시 표시하지 않습니다.",
            1,
        )

        msg = (qs.get("msg") or [""])[0]
        is_error = (qs.get("error") or ["0"])[0] == "1"
        flash = ""
        if msg:
            flash = f'<div class="flash {"error" if is_error else "ok"}">{s.esc(msg)}</div>'

        zone = (
            '<section class="panel" style="margin:16px 0">'
            '<h3>예산 데이터 관리</h3>'
            f'{flash}'
            '<div class="notice">API 설정 · 연결 테스트 · 수집 실행을 이 영역에서만 관리합니다.</div>'
            f'{admin_grid}'
            '</section>'
        )

        notice_start = page.find('<div class="notice"><b>수집 상태:</b>')
        if notice_start >= 0:
            notice_end = page.find("</div>", notice_start)
            if notice_end >= 0:
                insert_at = notice_end + len("</div>")
                page = page[:insert_at] + zone + page[insert_at:]
                return page
        return page

    s.budgets_html = budgets_html
    s._budget_admin_cleanup_applied = True


_apply_budget_admin_cleanup()

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
