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
import threading
from contextlib import contextmanager
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_TRUE = {"1", "true", "yes", "on"}
_ENGINE = None
_ENGINE_URL = None
_ENGINE_CONFIG = None
# A web process may receive concurrent menu requests before its first checkout.
# Serialize pool initialization/reset so only one QueuePool (1+1 by default)
# exists per process; the isolated collector has its own separate process pool.
_ENGINE_LOCK = threading.RLock()
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


def validate_schema_layout():
    """Return the two workload schemas or fail closed on destructive collisions."""
    app = app_schema()
    budget = budget_schema()
    reserved = "g2b_meta"
    if app == budget or app == reserved or budget == reserved:
        raise RuntimeError("G2B_SCHEMA_LAYOUT_INVALID")
    return app, budget


def _env_int(name, default, *, lower, upper):
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(lower), min(int(upper), value))


def engine():
    """Return one shared production SQLAlchemy engine per process.

    Authenticated menu reads can arrive concurrently while a disposable
    shopping/budget worker is running. Avoid constructing several independent
    QueuePools during the first concurrent checkout (or reconfiguration).
    This lock never holds a PostgreSQL connection and cannot serialize SQL.
    """
    global _ENGINE, _ENGINE_URL, _ENGINE_CONFIG

    validate_schema_layout()
    url = resolve_database_url()
    if not url:
        raise RuntimeError("G2B_DATABASE_NOT_CONFIGURED")

    config = (
        # Cafe24 memory safety: stale/high environment values must not recreate
        # the old 5+2 pool footprint. Keep at most 2 persistent + 1 overflow.
        _env_int("G2B_DB_POOL_SIZE", 1, lower=1, upper=2),
        _env_int("G2B_DB_MAX_OVERFLOW", 1, lower=0, upper=1),
        _env_int("G2B_DB_POOL_TIMEOUT_SECONDS", 5, lower=1, upper=30),
        _env_int("G2B_DB_POOL_RECYCLE_SECONDS", 900, lower=60, upper=3600),
        _env_int("G2B_DB_CONNECT_TIMEOUT_SECONDS", 3, lower=1, upper=30),
        _env_int("G2B_DB_LOCK_TIMEOUT_MS", 5000, lower=1000, upper=60000),
        _env_int("G2B_DB_STATEMENT_TIMEOUT_MS", 120000, lower=5000, upper=600000),
    )
    with _ENGINE_LOCK:
        if _ENGINE is not None and _ENGINE_URL == url and _ENGINE_CONFIG == config:
            return _ENGINE

        (
            pool_size,
            max_overflow,
            pool_timeout,
            pool_recycle,
            connect_timeout,
            lock_timeout_ms,
            statement_timeout_ms,
        ) = config
        # Construct before disposing the previous pool. If driver/engine setup
        # fails, existing menu sessions retain their last valid engine.
        replacement = create_engine(
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
        previous = _ENGINE
        _ENGINE = replacement
        _ENGINE_URL = url
        _ENGINE_CONFIG = config
        if previous is not None:
            try:
                previous.dispose()
            except Exception:
                pass
        return replacement

def reset_engine_cache():
    global _ENGINE, _ENGINE_URL, _ENGINE_CONFIG, _SOURCE_LABEL
    # A concurrent first checkout must not recreate an engine after reset
    # has already returned (nor leave multiple undisposed QueuePools behind).
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            try:
                _ENGINE.dispose()
            except Exception:
                pass
        _ENGINE = None
        _ENGINE_URL = None
        _ENGINE_CONFIG = None
        _SOURCE_LABEL = ""



def _operational_lease_connection():
    """Open one disposable, autocommit PostgreSQL session for advisory locks.

    A collector holds its advisory locks across source API calls. Using the
    normal pool would consume one of its 1+1 web/data slots for that entire
    cycle; leaving a transaction open would also hit the 60-second
    idle_in_transaction_session_timeout. A dedicated psycopg session avoids
    both problems. Physical close always releases session advisory locks,
    including if an explicit unlock encounters a connection failure.
    """
    import psycopg

    url = make_url(resolve_database_url()).set(drivername="postgresql")
    return psycopg.connect(
        url.render_as_string(hide_password=False),
        autocommit=True,
        connect_timeout=_env_int(
            "G2B_DB_CONNECT_TIMEOUT_SECONDS", 3, lower=1, upper=30
        ),
        application_name="g2b_v41_operational_lease",
    )



def _isolated_lease_watchdog_enabled():
    """Only a disposable production source child may self-exit on lease loss."""
    return bool(
        _flag("G2B_ISOLATED_HEAVY_WORKER")
        and not _flag("G2B_TEST_MODE")
        and str(os.getenv("G2B_RUNTIME_ROLE", "") or "").upper() == "LOCAL_COLLECTOR"
    )


def _start_operational_lease_watchdog(conn, mode, *, interval_seconds=5.0):
    """Monitor the *same* PostgreSQL session that holds source advisory locks.

    A disconnected advisory session can release its lock while the HTTP source
    request continues. Under rollouts this would allow another collector to
    acquire the same lock. The disposable child must stop before it performs
    subsequent source work without the lease. The parent classifies exit 77 as
    retryable LEASE_HELD; the page transaction rolls back automatically.
    """
    if not _isolated_lease_watchdog_enabled():
        return None, None
    stop = threading.Event()
    interval = max(0.01, float(interval_seconds))

    def watch():
        while not stop.wait(interval):
            try:
                # The psycopg connection is already autocommit and is shared
                # only with its owning context manager during acquire/release.
                # This SELECT opens no idle transaction and consumes no data
                # pool slot. SQL errors mean the session lock cannot be trusted.
                conn.execute("SELECT 1").fetchone()
            except Exception as exc:
                if not stop.is_set():
                    print(
                        "G2B_ISOLATED_LEASE_SESSION_LOST",
                        str(mode or "")[:20],
                        type(exc).__name__,
                        flush=True,
                    )
                    os._exit(77)
                return

    thread = threading.Thread(
        target=watch,
        name="g2b-isolated-pg-lease-watchdog",
        daemon=True,
    )
    thread.start()
    return stop, thread


def _stop_operational_lease_watchdog(stop, thread):
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=4.0)


@contextmanager
def operational_cycle_lease(name="g2b_v41_operational_cycle", *, shared=False):
    """Hold a cross-process advisory gate without occupying the web/data pool."""
    conn = _operational_lease_connection()
    acquired = False
    watch_stop = watch_thread = None
    lock_function = (
        "pg_try_advisory_lock_shared"
        if shared
        else "pg_try_advisory_lock"
    )
    unlock_function = (
        "pg_advisory_unlock_shared"
        if shared
        else "pg_advisory_unlock"
    )
    try:
        row = conn.execute(
            f"SELECT {lock_function}(hashtext(%s))", (str(name),)
        ).fetchone()
        acquired = bool(row and row[0])
        if acquired:
            watch_stop, watch_thread = _start_operational_lease_watchdog(
                conn, str(name)
            )
        yield acquired
    finally:
        _stop_operational_lease_watchdog(watch_stop, watch_thread)
        try:
            if acquired:
                try:
                    conn.execute(
                        f"SELECT {unlock_function}(hashtext(%s))",
                        (str(name),),
                    )
                except Exception:
                    # This is a disposable, non-pooled session. Closing it
                    # releases advisory locks even when unlock cannot run.
                    pass
        finally:
            conn.close()


@contextmanager
def operational_source_cycle_lease(source):
    """Hold the global shared and source-exclusive gates on one isolated session.

    Both locks remain session-scoped in autocommit mode, so slow source API
    calls neither hold a PostgreSQL transaction nor exhaust the main pool.
    """
    mode = str(source or "").strip().lower()
    if mode not in {"shopping", "budget"}:
        raise ValueError("UNSUPPORTED_OPERATIONAL_SOURCE_LEASE")

    conn = _operational_lease_connection()
    global_name = "g2b_v41_operational_cycle"
    source_name = f"g2b_v41_manual_{mode}"
    global_acquired = False
    source_acquired = False
    watch_stop = watch_thread = None
    try:
        row = conn.execute(
            "SELECT pg_try_advisory_lock_shared(hashtext(%s))",
            (global_name,),
        ).fetchone()
        global_acquired = bool(row and row[0])
        if not global_acquired:
            yield False
            return

        row = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s))",
            (source_name,),
        ).fetchone()
        source_acquired = bool(row and row[0])
        if source_acquired:
            watch_stop, watch_thread = _start_operational_lease_watchdog(
                conn, mode
            )
        yield source_acquired
    finally:
        _stop_operational_lease_watchdog(watch_stop, watch_thread)
        try:
            if source_acquired:
                try:
                    conn.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        (source_name,),
                    )
                except Exception:
                    pass
            if global_acquired:
                try:
                    conn.execute(
                        "SELECT pg_advisory_unlock_shared(hashtext(%s))",
                        (global_name,),
                    )
                except Exception:
                    pass
        finally:
            conn.close()


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
