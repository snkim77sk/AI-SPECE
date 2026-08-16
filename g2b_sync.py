"""Compatibility import for the 2.0 clean collector.

All production collection logic lives in collector_v200.py.
This shim also makes the Cafe24 ``G2B_SERVICE_KEY`` environment variable
available to the clean collector without persisting the secret into SQLite.
"""
import os

import collector_v200 as _collector

_db_get_setting = _collector.get_setting


def _env_aware_get_setting(key, default=""):
    if key == "api_key":
        env_key = os.getenv("G2B_SERVICE_KEY", "").strip()
        if env_key:
            return env_key
    return _db_get_setting(key, default)


_collector.get_setting = _env_aware_get_setting

from collector_v200 import *  # noqa: E402,F401,F403

_original_test_shopping_api = _collector.test_shopping_api


def test_shopping_api():
    """Treat a successful zero-row response as connected, not disconnected."""
    try:
        return _original_test_shopping_api()
    except RuntimeError as exc:
        code = str(_collector.get_setting("last_api_result_code", "") or "")
        text = str(exc)
        if code in ("0", "00") and "0건" in text:
            _collector.set_setting("last_shop_raw_count", "0")
            return 0, 0
        raise
