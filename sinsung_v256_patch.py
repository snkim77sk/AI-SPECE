"""v2.5.6: default all manual sync forms to 2026-01-01 through today."""
import re

VERSION = "2.5.6-sinsung-manual-2026-start"
MANUAL_START = "2026-01-01"


def apply_v256_patch():
    import server as s

    original_settings_html = s.settings_html

    def _replace_start(page, action):
        pattern = (
            rf'(<form[^>]*action="/{re.escape(action)}"[^>]*>.*?'
            rf'<input[^>]*type="date"[^>]*name="start"[^>]*value=")[^"]*(")'
        )
        return re.sub(pattern, rf'\g<1>{MANUAL_START}\2', page, count=1, flags=re.S)

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        for action in ("sync-shop", "sync-bids", "sync-services"):
            page = _replace_start(page, action)
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
