"""Compatibility import for the 2.0 clean collector.

All production collection logic lives in collector_v200.py.
Service-key resolution is centralized in db.get_service_key(), so this module
must not patch collector settings at import time.
"""
import collector_v200 as _collector

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
