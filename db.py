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
# runtime resolves a process-local default once, preferring Cafe24 persistent storage.
DB_PATH = str(os.getenv("G2B_DB_PATH", "") or "").strip()
_RESOLVED_DB_PATH = None

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS vnext_source_credentials(
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def current_db_path():
    global _RESOLVED_DB_PATH
    configured = str(DB_PATH or os.getenv("G2B_DB_PATH", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if _RESOLVED_DB_PATH:
        return _RESOLVED_DB_PATH

    # Resolve once per process. Never switch databases mid-process if a mount
    # appears later, because that would make one request see a different schema.
    if os.path.isdir(PERSISTENT_DIR) or os.path.isdir("/app"):
        _RESOLVED_DB_PATH = os.path.join(PERSISTENT_DIR, "g2b-vnext.sqlite3")
    else:
        _RESOLVED_DB_PATH = "/tmp/g2b-vnext.sqlite3"
    return _RESOLVED_DB_PATH


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


def _harden_db_file_permissions(path):
    """Best-effort owner-only permissions because the vNext DB can contain API credentials."""
    if os.name == "nt":
        return
    try:
        if os.path.isfile(path):
            os.chmod(path, 0o600)
    except OSError:
        # Managed filesystems may reject chmod; runtime availability takes priority
        # and deployment diagnostics should then verify platform-level permissions.
        pass


@contextmanager
def connect():
    path = current_db_path()
    db_dir = os.path.dirname(path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    timeout = _timeout_seconds()
    conn = sqlite3.connect(path, timeout=timeout)
    _harden_db_file_permissions(path)
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


_SOURCE_CREDENTIAL_NAMES = frozenset({"g2b_service_key", "lofin_api_key", "eduinfo_api_key"})


def _get_source_credential(name, default=""):
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM vnext_source_credentials WHERE name=?",
            (str(name),),
        ).fetchone()
    return str(row["value"] if row else default or "").strip()


def set_source_credential(name, value):
    """Persist an administrator-entered source credential without exposing it via settings_dict()."""
    name = str(name or "").strip()
    if name not in _SOURCE_CREDENTIAL_NAMES:
        raise ValueError("unsupported source credential")
    secret = str(value or "").strip()
    if len(secret) > 8192:
        raise ValueError("source credential is too long")
    init_db()
    with connect() as conn:
        if not secret:
            conn.execute("DELETE FROM vnext_source_credentials WHERE name=?", (name,))
            return
        conn.execute(
            """INSERT INTO vnext_source_credentials(name,value,updated_at)
               VALUES(?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(name) DO UPDATE SET
                 value=excluded.value,
                 updated_at=CURRENT_TIMESTAMP""",
            (name, secret),
        )


def get_service_key(default=""):
    """Return the G2B credential, preferring a deployment environment override."""
    return str(
        os.getenv("G2B_SERVICE_KEY", "")
        or _get_source_credential("g2b_service_key", "")
        or default
        or ""
    ).strip()


def get_setting(key, default=""):
    """Read vNext runtime settings without ever exposing saved credentials in settings_dict()."""
    name = str(key or "")
    if name == "api_key":
        return get_service_key(default)
    if name == "lofin_api_key":
        return str(
            os.getenv("LOFIN_API_KEY", "")
            or _get_source_credential("lofin_api_key", "")
            or default
            or ""
        ).strip()
    if name == "eduinfo_api_key":
        return str(
            os.getenv("EDUINFO_API_KEY", "")
            or _get_source_credential("eduinfo_api_key", "")
            or default
            or ""
        ).strip()
    return _get_db_setting(name, default)


def set_setting(key, value):
    """Persist only non-secret vNext state."""
    name = str(key or "")
    if name in {"api_key", "lofin_api_key", "eduinfo_api_key"}:
        raise ValueError("source credentials must be configured through the credential store")
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
