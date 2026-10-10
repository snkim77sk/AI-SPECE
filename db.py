"""G2B v4.1 database primitives.

Production is PostgreSQL-only.  One PostgreSQL database is the source of truth and
is split into schemas by workload.  SQLite is retained only for explicit test-mode
regression fixtures so the historical unit suite can stay fast and hermetic.

The public helpers keep the old call shape (connect()/execute with qmark params)
while production statements are translated to psycopg through SQLAlchemy.
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from urllib.parse import unquote

import g2b_database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PERSISTENT_DIR = "/app/user_data"

DB_PATH = str(os.getenv("G2B_DB_PATH", "") or "").strip()
_RESOLVED_DB_PATH = None
_TRUE = {"1", "true", "yes", "on"}

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


def _flag(name, default=False):
    return str(os.getenv(name, "1" if default else "0") or "").strip().lower() in _TRUE


def _use_sqlite():
    """SQLite is allowed only in tests or an explicit local compatibility fixture."""
    backend = str(os.getenv("G2B_DB_BACKEND", "") or "").strip().lower()
    if backend:
        if backend in {"postgres", "postgresql"}:
            return False
        return backend in {"sqlite", "test-sqlite"}
    # Historical regression tests assume a local SQLite database whenever test
    # mode is enabled, even when they do not set an explicit path.
    return _flag("G2B_TEST_MODE")


def current_db_path():
    """Return a real path only for the test SQLite backend.

    Production callers receive a non-secret logical locator so diagnostics cannot
    accidentally expose a PostgreSQL DSN.
    """
    global _RESOLVED_DB_PATH
    if not _use_sqlite():
        return f"postgresql://configured/{g2b_database.app_schema()}"

    configured = str(DB_PATH or os.getenv("G2B_DB_PATH", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if _RESOLVED_DB_PATH:
        return _RESOLVED_DB_PATH
    _RESOLVED_DB_PATH = "/tmp/g2b-vnext-test.sqlite3"
    return _RESOLVED_DB_PATH


def db_is_persistent():
    if not _use_sqlite():
        return g2b_database.database_configured()
    path = os.path.abspath(current_db_path())
    persistent = os.path.abspath(PERSISTENT_DIR) + os.sep
    return path.startswith(persistent) or _flag("G2B_TEST_MODE")


def backend_name():
    return "SQLITE_TEST" if _use_sqlite() else "POSTGRESQL"


def _timeout_seconds():
    raw = str(os.getenv("G2B_SQLITE_TIMEOUT", "3") or "3").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 3.0
    return max(0.25, min(value, 30.0))


def _harden_db_file_permissions(path):
    if os.name == "nt":
        return
    try:
        if os.path.isfile(path):
            os.chmod(path, 0o600)
    except OSError:
        pass


class _CompatRow(Mapping):
    """Mapping row that also supports legacy integer indexing."""

    def __init__(self, keys, values):
        self._keys = list(keys)
        self._values = list(values)
        self._mapping = dict(zip(self._keys, self._values))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def keys(self):
        return self._keys


class _NoopResult:
    rowcount = 0

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def fetchmany(self, _size=None):
        return []

    def first(self):
        return None

    def scalar(self):
        return None

    def __iter__(self):
        return iter(())


class _ResultAdapter:
    def __init__(self, result):
        self._result = result
        self.rowcount = getattr(result, "rowcount", -1)

    @staticmethod
    def _row(row):
        if row is None:
            return None
        mapping = row._mapping
        return _CompatRow(mapping.keys(), mapping.values())

    def fetchone(self):
        return self._row(self._result.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._result.fetchall()]

    def fetchmany(self, size=None):
        rows = self._result.fetchmany(size) if size is not None else self._result.fetchmany()
        return [self._row(row) for row in rows]

    def first(self):
        return self._row(self._result.first())

    def scalar(self):
        return self._result.scalar()

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row


def _qmark_to_driver(sql):
    return str(sql).replace("?", "%s")


def _translate_insert_or_ignore(sql):
    text = str(sql)
    if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", text, flags=re.I):
        text = re.sub(
            r"\bINSERT\s+OR\s+IGNORE\s+INTO\b",
            "INSERT INTO",
            text,
            count=1,
            flags=re.I,
        )
        stripped = text.rstrip().rstrip(";")
        if " ON CONFLICT " not in stripped.upper():
            stripped += " ON CONFLICT DO NOTHING"
        text = stripped
    return text


def _translate_pg_sql(sql):
    text = str(sql or "").strip()
    if not text:
        return text
    upper = " ".join(text.upper().split())
    if upper == "BEGIN IMMEDIATE":
        return ""

    # Minimal DDL compatibility for the old SQLite schema strings. Production
    # tables are still ordinary PostgreSQL tables in g2b_app.
    text = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "BIGSERIAL PRIMARY KEY",
        text,
        flags=re.I,
    )
    text = _translate_insert_or_ignore(text)
    return _qmark_to_driver(text)


class _PgCompatConnection:
    def __init__(self, sa_conn):
        self._conn = sa_conn

    def execute(self, sql, params=()):
        text = str(sql or "").strip()
        if not text:
            return _NoopResult()
        upper = " ".join(text.upper().split())
        if upper == "BEGIN IMMEDIATE":
            return _NoopResult()

        pragma = re.match(r"PRAGMA\s+table_info\(([^)]+)\)", text, flags=re.I)
        if pragma:
            table = pragma.group(1).strip().strip('"').strip("'")
            result = self._conn.exec_driver_sql(
                """SELECT ordinal_position - 1 AS cid,
                          column_name AS name,
                          data_type AS type,
                          CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull,
                          column_default AS dflt_value,
                          0 AS pk
                   FROM information_schema.columns
                   WHERE table_schema=%s AND table_name=%s
                   ORDER BY ordinal_position""",
                (g2b_database.app_schema(), table),
            )
            return _ResultAdapter(result)

        if "sqlite_master" in text.lower():
            selected_name = bool(
                re.match(r"\s*SELECT\s+name\s+FROM\s+sqlite_master", text, flags=re.I)
            )
            translated = text
            translated = re.sub(
                r"FROM\s+sqlite_master",
                "FROM information_schema.tables",
                translated,
                flags=re.I,
            )
            translated = re.sub(
                r"type\s*=\s*'table'\s+AND\s+",
                "table_schema=%s AND ",
                translated,
                flags=re.I,
            )
            translated = re.sub(
                r"type\s*=\s*'table'",
                "table_schema=%s",
                translated,
                flags=re.I,
            )
            translated = re.sub(r"\bname\b", "table_name", translated, flags=re.I)
            if selected_name:
                translated = re.sub(
                    r"\s*SELECT\s+table_name\s+FROM",
                    "SELECT table_name AS name FROM",
                    translated,
                    count=1,
                    flags=re.I,
                )
            bind = (g2b_database.app_schema(),) + tuple(params or ())
            result = self._conn.exec_driver_sql(_qmark_to_driver(translated), bind)
            return _ResultAdapter(result)

        translated = _translate_pg_sql(text)
        if not translated:
            return _NoopResult()
        result = self._conn.exec_driver_sql(translated, tuple(params or ()))
        return _ResultAdapter(result)

    def execute_streaming(self, sql, params=(), *, max_row_buffer=250):
        """Execute a PostgreSQL SELECT with bounded server-side row buffering."""
        text = str(sql or "").strip()
        if not text:
            return _NoopResult()
        translated = _translate_pg_sql(text)
        if not translated:
            return _NoopResult()
        buffer_size = max(1, min(int(max_row_buffer), 1000))
        stream_conn = self._conn.execution_options(
            stream_results=True,
            max_row_buffer=buffer_size,
        )
        result = stream_conn.exec_driver_sql(
            translated,
            tuple(params or ()),
        )
        return _ResultAdapter(result)

    def executemany(self, sql, seq_of_params):
        translated = _translate_pg_sql(sql)
        if not translated:
            return _NoopResult()
        rows = [tuple(row) for row in seq_of_params]
        # SQLAlchemy/psycopg treats an empty executemany batch as a plain
        # parameterized execution. PostgreSQL then sees unbound %s placeholders
        # and raises SQLSTATE 42P02. Empty source pages are valid, so make an
        # empty batch a true no-op just like sqlite3.executemany().
        if not rows:
            return _NoopResult()
        result = self._conn.exec_driver_sql(translated, rows)
        return _ResultAdapter(result)

    def executescript(self, script):
        # Existing schema scripts contain simple CREATE/ALTER/INDEX statements only.
        # Splitting on semicolons is therefore deterministic and avoids retaining a
        # second SQLite-specific schema implementation.
        last = _NoopResult()
        for statement in str(script or "").split(";"):
            statement = statement.strip()
            if statement:
                last = self.execute(statement)
        return last

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


def _ensure_app_schema_on_connection(conn):
    schema = g2b_database.app_schema()
    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_namespace WHERE nspname=%s",
        (schema,),
    ).first()
    if not exists:
        quoted = conn.dialect.identifier_preparer.quote_schema(schema)
        try:
            conn.exec_driver_sql(f"CREATE SCHEMA {quoted}")
        except Exception as exc:
            raise RuntimeError("G2B_APP_SCHEMA_CREATE_FAILED") from exc
    quoted = conn.dialect.identifier_preparer.quote_schema(schema)
    conn.exec_driver_sql(f"SET search_path TO {quoted}, public")


@contextmanager
def connect():
    if _use_sqlite():
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
        return

    eng = g2b_database.engine()
    raw = eng.connect()
    try:
        _ensure_app_schema_on_connection(raw)
        wrapped = _PgCompatConnection(raw)
        yield wrapped
        raw.commit()
    except Exception:
        try:
            raw.rollback()
        except Exception:
            pass
        raise
    finally:
        raw.close()


def init_db():
    with connect() as conn:
        if _use_sqlite():
            try:
                if _flag("G2B_SQLITE_WAL"):
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.DatabaseError:
                pass
        conn.executescript(CORE_SCHEMA)


def _ensure_runtime_settings_storage():
    """Tests bootstrap SQLite lazily; production startup owns schema DDL."""
    if _use_sqlite():
        init_db()


def _get_db_setting(key, default=""):
    _ensure_runtime_settings_storage()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (str(key),),
        ).fetchone()
    return row["value"] if row else default


_SOURCE_CREDENTIAL_NAMES = frozenset({
    "g2b_service_key", "lofin_api_key", "eduinfo_api_key", "result_sync_token",
})
_SOURCE_CREDENTIAL_ENV = {
    "g2b_service_key": "G2B_SERVICE_KEY",
    "lofin_api_key": "LOFIN_API_KEY",
    "eduinfo_api_key": "EDUINFO_API_KEY",
    "result_sync_token": "G2B_RESULT_SYNC_TOKEN",
}


def source_credential_configured(name):
    key = str(name or "").strip()
    if key not in _SOURCE_CREDENTIAL_NAMES:
        return False
    env_name = _SOURCE_CREDENTIAL_ENV[key]
    if str(os.getenv(env_name, "") or "").strip():
        return True
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM vnext_source_credentials "
                "WHERE name=? AND TRIM(value)<>'' LIMIT 1",
                (key,),
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def _get_source_credential(name, default=""):
    _ensure_runtime_settings_storage()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM vnext_source_credentials WHERE name=?",
            (str(name),),
        ).fetchone()
    return str(row["value"] if row else default or "").strip()


def set_source_credential(name, value):
    name = str(name or "").strip()
    if name not in _SOURCE_CREDENTIAL_NAMES:
        raise ValueError("unsupported source credential")
    secret = str(value or "").strip()
    if len(secret) > 8192:
        raise ValueError("source credential is too long")
    _ensure_runtime_settings_storage()
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


def _normalize_g2b_service_key(value):
    key = unquote(str(value or "").strip())
    if any(ord(ch) < 32 for ch in key):
        return ""
    return key


def get_result_sync_token(default=""):
    return str(
        os.getenv("G2B_RESULT_SYNC_TOKEN", "")
        or _get_source_credential("result_sync_token", "")
        or default
        or ""
    ).strip()


def get_service_key(default=""):
    raw = (
        os.getenv("G2B_SERVICE_KEY", "")
        or _get_source_credential("g2b_service_key", "")
        or default
        or ""
    )
    return _normalize_g2b_service_key(raw)


def get_setting(key, default=""):
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
    name = str(key or "")
    if name in {"api_key", "lofin_api_key", "eduinfo_api_key"}:
        raise ValueError("source credentials must be configured through the credential store")
    _ensure_runtime_settings_storage()
    with connect() as conn:
        conn.execute(
            """INSERT INTO app_settings(key,value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (name, str(value)),
        )


def settings_dict():
    _ensure_runtime_settings_storage()
    with connect() as conn:
        return {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key,value FROM app_settings").fetchall()
        }
