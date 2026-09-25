"""SQLite primitives for the clean G2B vNext runtime.

The web process must be able to bind its HTTP port even when persistent storage is
late, locked, or temporarily unavailable. Database selection is therefore resolved
at connection time and SQLite lock waits are deliberately short.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSISTENT_DIR = "/app/user_data"

# Tests and explicit deployments may monkeypatch/override DB_PATH. When empty, the
# runtime chooses the current persistent mount dynamically on each connection.
DB_PATH = str(os.getenv("G2B_DB_PATH", "") or "").strip()

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


def current_db_path():
    configured = str(DB_PATH or os.getenv("G2B_DB_PATH", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if os.path.isdir(PERSISTENT_DIR):
        return os.path.join(PERSISTENT_DIR, "g2b-vnext.sqlite3")
    # A writable ephemeral fallback lets the HTTP process boot even before a
    # platform persistent mount is attached. Cafe24 normally provides /app/user_data.
    return "/tmp/g2b-vnext.sqlite3"


def db_is_persistent():
    path = os.path.abspath(current_db_path())
    persistent = os.path.abspath(PERSISTENT_DIR) + os.sep
    return path.startswith(persistent)


def _timeout_seconds():
    raw = str(os.getenv("G2B_SQLITE_TIMEOUT", "3") or "3").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 3.0
    return max(0.25, min(value, 30.0))


@contextmanager
def connect():
    path = current_db_path()
    db_dir = os.path.dirname(path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    timeout = _timeout_seconds()
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        # WAL is opt-in. DELETE journal mode is more portable on managed/network
        # filesystems and is sufficient for this small SQLite deployment.
        try:
            if str(os.getenv("G2B_SQLITE_WAL", "0")).lower() in ("1", "true", "yes", "on"):
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

    Source credentials are never read from SQLite after the 2.x removal.
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
