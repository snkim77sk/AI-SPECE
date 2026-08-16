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
