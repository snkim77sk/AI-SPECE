"""Minimal SQLite utilities for the clean G2B vNext runtime.

Legacy 2.2 serving tables, scheduler settings, sync logs, and sample-data helpers
are intentionally removed. This module only provides the persistence primitives
used by the vNext foundation.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_db_path():
    configured = str(os.getenv("G2B_DB_PATH", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    persistent_dir = "/app/user_data"
    if os.path.isdir(persistent_dir) and os.access(persistent_dir, os.W_OK):
        return os.path.join(persistent_dir, "g2b-vnext.sqlite3")
    return os.path.join(BASE_DIR, "data", "g2b-vnext.sqlite3")


DB_PATH = _resolve_db_path()

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


@contextmanager
def connect():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
        conn.executescript(CORE_SCHEMA)


def _get_db_setting(key, default=""):
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (str(key),),
        ).fetchone()
    return row["value"] if row else default


def get_service_key(default=""):
    """G2B source secret is runtime-only in the clean vNext application."""
    return str(os.getenv("G2B_SERVICE_KEY", "") or default or "").strip()


def get_setting(key, default=""):
    """Read a non-secret vNext runtime marker/setting.

    Source credentials are never read from SQLite after the 2.2 removal.
    """
    name = str(key or "")
    if name == "api_key":
        return get_service_key(default)
    if name == "lofin_api_key":
        return str(os.getenv("LOFIN_API_KEY", "") or default or "").strip()
    if name == "eduinfo_api_key":
        return str(os.getenv("EDUINFO_API_KEY", "") or default or "").strip()
    return _get_db_setting(name, default)


def set_setting(key, value):
    """Persist only non-secret vNext state."""
    name = str(key or "")
    if name in {"api_key", "lofin_api_key", "eduinfo_api_key"}:
        raise ValueError("source credentials must be configured as environment secrets")
    init_db()
    with connect() as conn:
        conn.execute(
            """INSERT INTO app_settings(key,value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (name, str(value)),
        )


def settings_dict():
    init_db()
    with connect() as conn:
        return {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key,value FROM app_settings").fetchall()
        }
