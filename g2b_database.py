"""Canonical PostgreSQL configuration for G2B v4.1.

Production uses one PostgreSQL database as the single source of truth.
The database is split into workload schemas:
- g2b_app: CONTROL + normalized business records + lightweight READ state
- g2b_budget: normalized BUDGET current state + bounded revisions/checkpoints

SQLite remains available only for explicit test-mode regression fixtures.
"""
from __future__ import annotations

import os
import re
from urllib.parse import quote

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

_TRUE = {"1", "true", "yes", "on"}
_ENGINE = None
_ENGINE_URL = None
_ENGINE_CONFIG = None
_SOURCE_LABEL = ""

DEFAULT_APP_SCHEMA = "g2b_app"
DEFAULT_BUDGET_SCHEMA = "g2b_budget"


def _flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in _TRUE


def _get(env, *names):
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _compose(host, port, database, user, password):
    auth = quote(str(user), safe="")
    if password:
        auth += ":" + quote(str(password), safe="")
    return (
        f"postgresql://{auth}@{str(host).strip()}:{str(port or '5432').strip()}/"
        f"{quote(str(database).strip(), safe='')}"
    )


def _candidate_urls(environ=None):
    """Yield PostgreSQL candidates in deterministic preference order.

    G2B_DATABASE_URL is the v4.1 canonical setting. Production does not accept the
    old budget-specific URL. Platform-provided PostgreSQL variables are used only
    when no explicit G2B URL exists.
    """
    env = os.environ if environ is None else environ

    direct = _get(env, "G2B_DATABASE_URL")
    if direct:
        yield "G2B_DATABASE_URL", direct
        return

    host = _get(env, "DB_HOST")
    database = _get(env, "DB_NAME", "DB_DATABASE")
    user = _get(env, "DB_USER", "DB_USERNAME")
    if host and database and user:
        yield (
            "DB_*",
            _compose(
                host,
                _get(env, "DB_PORT") or "5432",
                database,
                user,
                str(env.get("DB_PASSWORD", "") or ""),
            ),
        )
        return

    host = _get(env, "PGHOST", "POSTGRES_HOST")
    database = _get(env, "PGDATABASE", "POSTGRES_DB")
    user = _get(env, "PGUSER", "POSTGRES_USER")
    if host and database and user:
        yield (
            "PG*",
            _compose(
                host,
                _get(env, "PGPORT", "POSTGRES_PORT") or "5432",
                database,
                user,
                str(env.get("PGPASSWORD", "") or env.get("POSTGRES_PASSWORD", "") or ""),
            ),
        )
        return

    for key in ("POSTGRES_URL", "POSTGRESQL_URL", "DATABASE_URL"):
        value = _get(env, key)
        if value:
            yield key, value
            return


def resolve_database_url(environ=None):
    """Return a normalized SQLAlchemy PostgreSQL URL without logging secrets."""
    global _SOURCE_LABEL
    for source, raw in _candidate_urls(environ):
        try:
            url = make_url(raw)
        except Exception:
            raise RuntimeError(f"{source}_INVALID") from None
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise RuntimeError(f"{source}_POSTGRESQL_REQUIRED")
        _SOURCE_LABEL = source
        return url.set(drivername="postgresql+psycopg").render_as_string(
            hide_password=False
        )
    _SOURCE_LABEL = ""
    return ""


def database_source_label():
    if not _SOURCE_LABEL:
        try:
            resolve_database_url()
        except RuntimeError:
            return ""
    return str(_SOURCE_LABEL or "")


def database_url_present(environ=None):
    """Return whether any supported PostgreSQL connection variable is supplied."""
    return next(_candidate_urls(environ), None) is not None


def database_configured():
    try:
        return bool(resolve_database_url())
    except RuntimeError:
        return False


def safe_schema(name, default):
    value = str(os.getenv(name, default) or default).strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value) is None:
        raise RuntimeError(f"{name}_INVALID")
    return value


def app_schema():
    return safe_schema("G2B_APP_SCHEMA", DEFAULT_APP_SCHEMA)


def budget_schema():
    return safe_schema("G2B_BUDGET_SCHEMA", DEFAULT_BUDGET_SCHEMA)


def _env_int(name, default, *, lower, upper):
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(lower), min(int(upper), value))


def engine():
    """Return the shared production SQLAlchemy engine.

    A single pool is deliberately shared by control/shopping/budget code so Cafe24
    does not receive multiple independent connection pools for the same database.
    """
    global _ENGINE, _ENGINE_URL, _ENGINE_CONFIG

    url = resolve_database_url()
    if not url:
        raise RuntimeError("G2B_DATABASE_NOT_CONFIGURED")

    config = (
        _env_int("G2B_DB_POOL_SIZE", 5, lower=1, upper=12),
        _env_int("G2B_DB_MAX_OVERFLOW", 2, lower=0, upper=12),
        _env_int("G2B_DB_POOL_TIMEOUT_SECONDS", 5, lower=1, upper=30),
        _env_int("G2B_DB_POOL_RECYCLE_SECONDS", 900, lower=60, upper=3600),
        _env_int("G2B_DB_CONNECT_TIMEOUT_SECONDS", 3, lower=1, upper=30),
        _env_int("G2B_DB_LOCK_TIMEOUT_MS", 5000, lower=1000, upper=60000),
        _env_int("G2B_DB_STATEMENT_TIMEOUT_MS", 120000, lower=5000, upper=600000),
    )
    if _ENGINE is not None and _ENGINE_URL == url and _ENGINE_CONFIG == config:
        return _ENGINE
    if _ENGINE is not None:
        try:
            _ENGINE.dispose()
        except Exception:
            pass

    (
        pool_size,
        max_overflow,
        pool_timeout,
        pool_recycle,
        connect_timeout,
        lock_timeout_ms,
        statement_timeout_ms,
    ) = config
    _ENGINE = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        connect_args={
            "connect_timeout": connect_timeout,
            "options": (
                f"-c lock_timeout={lock_timeout_ms} "
                f"-c statement_timeout={statement_timeout_ms} "
                "-c idle_in_transaction_session_timeout=60000"
            ),
        },
    )
    _ENGINE_URL = url
    _ENGINE_CONFIG = config
    return _ENGINE


def reset_engine_cache():
    global _ENGINE, _ENGINE_URL, _ENGINE_CONFIG, _SOURCE_LABEL
    if _ENGINE is not None:
        try:
            _ENGINE.dispose()
        except Exception:
            pass
    _ENGINE = None
    _ENGINE_URL = None
    _ENGINE_CONFIG = None
    _SOURCE_LABEL = ""


def ensure_schema(schema):
    """Create one schema only when absent.

    Existing least-privilege deployments do not need CREATE SCHEMA on every boot.
    """
    eng = engine()
    with eng.begin() as conn:
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s",
            (str(schema),),
        ).first()
        if exists:
            return
        quoted = conn.dialect.identifier_preparer.quote_schema(str(schema))
        try:
            conn.exec_driver_sql(f"CREATE SCHEMA {quoted}")
        except Exception as exc:
            raise RuntimeError("G2B_SCHEMA_CREATE_FAILED") from exc


def ensure_runtime_schemas():
    ensure_schema(app_schema())
    ensure_schema(budget_schema())
