"""One-time owner-approved fresh start for G2B 4.1.

The 4.1 release deliberately does not migrate 4.0 application data.  The owner
approved rebuilding the G2B dataset from source so the long-term runtime can use
one PostgreSQL database without carrying the SQLite/dual-store compatibility state.

Safety rules:
- only G2B-owned schemas are dropped (g2b_app / g2b_budget by default)
- the reset requires both G2B_V41_FRESH_START=1 and G2B_DESTRUCTIVE_RESET_CONFIRM=1 when prior G2B storage is detected
- a durable marker in g2b_meta prevents a repeated destructive reset
- PostgreSQL advisory locking serializes rolling deployments
- the legacy SQLite file is deleted only after the PostgreSQL transaction commits
"""
from __future__ import annotations

import os

from sqlalchemy import text

import g2b_database

RELEASE = "4.1.0"
MARKER_SCHEMA = "g2b_meta"
MARKER_KEY = "fresh_start_4_1_0"
MARKER_VALUE = "NORMALIZED_NO_RAW_V1"
LEGACY_SQLITE_PATHS = (
    "/app/user_data/g2b-vnext.sqlite3",
    "/app/user_data/g2b.sqlite3",
)
_TRUE = {"1", "true", "yes", "on"}


def _flag(name):
    return str(os.getenv(name, "0") or "").strip().lower() in _TRUE


def _quote_schema(conn, value):
    return conn.dialect.identifier_preparer.quote_schema(str(value))


def _schema_exists(conn, schema):
    return conn.execute(
        text("SELECT 1 FROM pg_namespace WHERE nspname=:schema"),
        {"schema": str(schema)},
    ).first() is not None


def _ensure_marker_table(conn):
    if not _schema_exists(conn, MARKER_SCHEMA):
        conn.execute(text(f"CREATE SCHEMA {_quote_schema(conn, MARKER_SCHEMA)}"))
    conn.execute(text(
        f"""CREATE TABLE IF NOT EXISTS {_quote_schema(conn, MARKER_SCHEMA)}.release_bootstrap(
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL,
               applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    ))


def _marker(conn):
    _ensure_marker_table(conn)
    return conn.execute(
        text(
            f"SELECT value FROM {_quote_schema(conn, MARKER_SCHEMA)}.release_bootstrap "
            "WHERE key=:key"
        ),
        {"key": MARKER_KEY},
    ).scalar()


def _write_marker(conn, value):
    conn.execute(
        text(
            f"""INSERT INTO {_quote_schema(conn, MARKER_SCHEMA)}.release_bootstrap(key,value)
                VALUES(:key,:value)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  applied_at=CURRENT_TIMESTAMP"""
        ),
        {"key": MARKER_KEY, "value": str(value)},
    )


def _legacy_sqlite_present():
    return any(os.path.isfile(path) for path in LEGACY_SQLITE_PATHS)


def _remove_legacy_sqlite():
    removed = []
    errors = []
    for path in LEGACY_SQLITE_PATHS:
        if not os.path.exists(path):
            continue
        try:
            os.remove(path)
            removed.append(path)
        except OSError as exc:
            errors.append(type(exc).__name__)
    return {"removed": removed, "errors": errors}


def prepare_v41_storage():
    """Prepare the v4.1 PostgreSQL-only storage boundary.

    A brand-new empty database may bootstrap without the destructive flag.  If any
    4.0 G2B schema or legacy SQLite file exists, the explicit fresh-start flag is
    required.  Once the marker is COMPLETE, restarts are idempotent.
    """
    if str(os.getenv("G2B_TEST_MODE", "0") or "").strip().lower() in _TRUE:
        return {
            "status": "TEST_MODE",
            "reset": False,
            "marker": False,
            "marker_value": "",
            "legacy_sqlite_removed": [],
        }

    app_schema, budget_schema = g2b_database.validate_schema_layout()
    engine = g2b_database.engine()
    prior = {}
    did_reset = False

    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:name))"),
            {"name": "g2b_v41_fresh_start"},
        )
        current = _marker(conn)
        if current == MARKER_VALUE:
            # The PostgreSQL reset must never repeat, but a managed filesystem may
            # have refused SQLite cleanup during the first boot. Retry only that
            # harmless file cleanup on later boots until the legacy file is gone.
            sqlite_cleanup = _remove_legacy_sqlite()
            return {
                "status": "SKIPPED",
                "reset": False,
                "marker": True,
                "marker_value": MARKER_VALUE,
                "legacy_sqlite_removed": sqlite_cleanup["removed"],
                "legacy_sqlite_cleanup_errors": sqlite_cleanup["errors"],
            }

        # Once a marker row exists, any unexpected value is metadata corruption or
        # an unknown release contract. Never interpret it as permission to reset
        # populated workload schemas, even when G2B_V41_FRESH_START=1 is still set.
        if current not in (None, ""):
            raise RuntimeError("G2B_V41_FRESH_START_MARKER_MISMATCH")

        prior = {
            "app_schema": _schema_exists(conn, app_schema),
            "budget_schema": _schema_exists(conn, budget_schema),
            "legacy_sqlite": _legacy_sqlite_present(),
        }
        prior_exists = any(prior.values())

        if prior_exists and not _flag("G2B_V41_FRESH_START"):
            raise RuntimeError("G2B_V41_FRESH_START_REQUIRED")
        if prior_exists and not _flag("G2B_DESTRUCTIVE_RESET_CONFIRM"):
            raise RuntimeError("G2B_DESTRUCTIVE_RESET_CONFIRM_REQUIRED")

        if prior_exists:
            for schema in (app_schema, budget_schema):
                if _schema_exists(conn, schema):
                    conn.execute(text(f"DROP SCHEMA {_quote_schema(conn, schema)} CASCADE"))
            did_reset = True

        # Recreate only the empty workload schemas here.  Normal schema installers
        # populate tables after this transaction commits.
        for schema in (app_schema, budget_schema):
            if not _schema_exists(conn, schema):
                conn.execute(text(f"CREATE SCHEMA {_quote_schema(conn, schema)}"))

        _write_marker(conn, MARKER_VALUE)

    sqlite_cleanup = _remove_legacy_sqlite() if did_reset or prior.get("legacy_sqlite") else {
        "removed": [],
        "errors": [],
    }
    return {
        "status": "COMPLETE",
        "reset": did_reset,
        "marker": True,
        "marker_value": MARKER_VALUE,
        "prior": prior,
        "legacy_sqlite_removed": sqlite_cleanup["removed"],
        "legacy_sqlite_cleanup_errors": sqlite_cleanup["errors"],
    }
