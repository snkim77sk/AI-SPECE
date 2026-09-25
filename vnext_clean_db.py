"""Storage and authentication for the clean G2B vNext runtime.

Startup is deliberately non-destructive. Legacy 2.x code is absent from the runtime,
but old database files/tables are never deleted while the web process is starting.
This avoids managed-storage races and keeps HTTP startup independent of cleanup.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time

import db
from db import connect
from vnext_store import ensure_foundation

SESSION_TTL_SECONDS = 12 * 60 * 60
SETUP_TOKEN_KEY = "vnext_setup_token"
LEGACY_TABLES = (
    "shopping_contracts",
    "bids",
    "budget_items",
    "sync_logs",
    "users",
)


def cleanup_legacy_tables():
    """Explicit maintenance helper; never called automatically during web startup."""
    with connect() as conn:
        for table in LEGACY_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(
            """DELETE FROM app_settings
               WHERE key <> 'classifier_version'
                 AND key NOT LIKE 'vnext_%'
                 AND key NOT LIKE 'g2b_vnext_%'
                 AND key NOT LIKE 'lofin_vnext_%'"""
        )
        conn.execute(
            """INSERT INTO app_settings(key,value)
               VALUES('vnext_legacy_cleanup_complete','1')
               ON CONFLICT(key) DO UPDATE SET value='1'"""
        )


def ensure_clean_schema():
    db.init_db()
    ensure_foundation()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vnext_users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS vnext_sessions(
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_vnext_sessions_expires
              ON vnext_sessions(expires_at);
            """
        )
        conn.execute(
            """INSERT INTO app_settings(key,value)
               VALUES('vnext_clean_runtime','1')
               ON CONFLICT(key) DO UPDATE SET value='1'"""
        )
        conn.execute(
            "DELETE FROM vnext_sessions WHERE expires_at < ?",
            (int(time.time()),),
        )
        count = int(conn.execute("SELECT COUNT(*) FROM vnext_users").fetchone()[0] or 0)
        if count == 0:
            configured = str(os.getenv("G2B_SETUP_TOKEN", "") or "").strip()
            if configured:
                token = configured
            else:
                row = conn.execute(
                    "SELECT value FROM app_settings WHERE key=?",
                    (SETUP_TOKEN_KEY,),
                ).fetchone()
                token = str(row["value"] or "").strip() if row else ""
                if not token:
                    token = secrets.token_urlsafe(24)
            conn.execute(
                """INSERT INTO app_settings(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (SETUP_TOKEN_KEY, token),
            )
        else:
            conn.execute("DELETE FROM app_settings WHERE key=?", (SETUP_TOKEN_KEY,))
    # No file/table deletion here. Web startup must never mutate unrelated legacy
    # storage; cleanup is an explicit maintenance operation only.


def legacy_tables_absent():
    with connect() as conn:
        existing = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    return not any(table in existing for table in LEGACY_TABLES)


def users_empty():
    with connect() as conn:
        return int(
            conn.execute("SELECT COUNT(*) FROM vnext_users").fetchone()[0] or 0
        ) == 0


def setup_token():
    """Return the current one-time setup token while no admin exists."""
    if not users_empty():
        return ""
    configured = str(os.getenv("G2B_SETUP_TOKEN", "") or "").strip()
    if configured:
        return configured
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (SETUP_TOKEN_KEY,),
        ).fetchone()
    return str(row["value"] or "").strip() if row else ""


def validate_setup_token(value):
    expected = setup_token()
    supplied = str(value or "").strip()
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


def consume_setup_token():
    with connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (SETUP_TOKEN_KEY,))


def _hash_password(password, *, salt=None, rounds=310_000):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return "pbkdf2_sha256$" + str(rounds) + "$" + salt.hex() + "$" + digest.hex()


def verify_password(password, encoded):
    try:
        algorithm, rounds_text, salt_hex, digest_hex = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, rounds
    )
    return secrets.compare_digest(actual, expected)


def create_admin(username, password):
    username = str(username or "").strip()
    if not (4 <= len(username) <= 50):
        raise ValueError("아이디는 4~50자여야 합니다.")
    if len(str(password or "")) < 10:
        raise ValueError("비밀번호는 10자 이상이어야 합니다.")
    with connect() as conn:
        if conn.execute(
            "SELECT 1 FROM vnext_users WHERE username=?",
            (username,),
        ).fetchone():
            raise ValueError("이미 존재하는 아이디입니다.")
        conn.execute(
            """INSERT INTO vnext_users(username,password_hash,role,status)
               VALUES(?,?,'admin','active')""",
            (username, _hash_password(password)),
        )
        conn.execute("DELETE FROM app_settings WHERE key=?", (SETUP_TOKEN_KEY,))


def authenticate(username, password):
    with connect() as conn:
        row = conn.execute(
            """SELECT username,password_hash,role,status
               FROM vnext_users WHERE username=?""",
            (str(username or "").strip(),),
        ).fetchone()
    if (
        not row
        or row["status"] != "active"
        or not verify_password(str(password or ""), row["password_hash"])
    ):
        return None
    return {"username": row["username"], "role": row["role"]}


def create_session(username):
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = int(time.time()) + SESSION_TTL_SECONDS
    with connect() as conn:
        conn.execute(
            """INSERT INTO vnext_sessions(token_hash,username,expires_at)
               VALUES(?,?,?)""",
            (token_hash, str(username), expires),
        )
    return token


def session_user(token):
    if not token:
        return None
    token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    now = int(time.time())
    with connect() as conn:
        row = conn.execute(
            """SELECT u.username,u.role,u.status,s.expires_at
               FROM vnext_sessions s
               JOIN vnext_users u ON u.username=s.username
               WHERE s.token_hash=?""",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        if int(row["expires_at"]) <= now or row["status"] != "active":
            conn.execute(
                "DELETE FROM vnext_sessions WHERE token_hash=?",
                (token_hash,),
            )
            return None
    return {"username": row["username"], "role": row["role"]}


def delete_session(token):
    if not token:
        return
    token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    with connect() as conn:
        conn.execute(
            "DELETE FROM vnext_sessions WHERE token_hash=?",
            (token_hash,),
        )
