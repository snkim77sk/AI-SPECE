"""Non-destructive PostgreSQL bootstrap boundary for existing G2B 4.1 storage.

The historic 4.0-to-4.1 destructive reset is retired.  No environment
variable can authorize dropping populated app/budget schemas or removing a
legacy SQLite file.  A completed release marker keeps normal restarts fast
and non-destructive. Missing/foreign markers with pre-existing storage fail
closed, while an entirely new empty database can be provisioned once.
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


def _quote_schema(conn, value):
    return conn.dialect.identifier_preparer.quote_schema(str(value))


def _schema_exists(conn, schema):
    return conn.execute(
        text("SELECT 1 FROM pg_namespace WHERE nspname=:schema"),
        {"schema": str(schema)},
    ).first() is not None


def _ensure_marker_table(conn):
    """Create marker infrastructure only for a proven *empty* new database."""
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
    """Read the existing release marker without any DDL or marker repair."""
    if not _schema_exists(conn, MARKER_SCHEMA):
        return None
    exists = conn.execute(
        text(
            "SELECT 1 FROM pg_class tbl "
            "JOIN pg_namespace ns ON ns.oid=tbl.relnamespace "
            "WHERE ns.nspname=:schema AND tbl.relname=:table "
            "AND tbl.relkind IN ('r', 'p')"
        ),
        {"schema": MARKER_SCHEMA, "table": "release_bootstrap"},
    ).first()
    if exists is None:
        return None
    return conn.execute(
        text(
            f"SELECT value FROM {_quote_schema(conn, MARKER_SCHEMA)}.release_bootstrap "
            "WHERE key=:key"
        ),
        {"key": MARKER_KEY},
    ).scalar()


def _write_marker(conn, value):
    # Never overwrite a pre-existing marker. A concurrent or foreign marker
    # writer must not allow this bootstrap to replace unknown release metadata.
    conn.execute(
        text(
            f"""INSERT INTO {_quote_schema(conn, MARKER_SCHEMA)}.release_bootstrap(key,value)
                VALUES(:key,:value)
                ON CONFLICT(key) DO NOTHING"""
        ),
        {"key": MARKER_KEY, "value": str(value)},
    )
    if _marker(conn) != str(value):
        raise RuntimeError("G2B_V41_FRESH_START_MARKER_MISMATCH")


def _legacy_sqlite_present():
    """Detect legacy files only; this module never deletes or moves them."""
    return any(os.path.isfile(path) for path in LEGACY_SQLITE_PATHS)


def prepare_v41_storage():
    """Verify existing 4.1 storage or install *empty* new schemas.

    Historical G2B_V41_FRESH_START and G2B_DESTRUCTIVE_RESET_CONFIRM values
    are intentionally powerless here. A completed marker returns SKIPPED and
    preserves legacy files regardless of those values. Without that marker,
    ANY pre-existing workload schema or legacy SQLite file causes a hard stop
    without repair, DROP, DELETE, TRUNCATE, or API access. Only absent schemas
    and absent legacy files permit one additive initial bootstrap.
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
    with engine.begin() as conn:
        conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:name))"),
            {"name": "g2b_v41_fresh_start"},
        )
        current = _marker(conn)
        if current == MARKER_VALUE:
            return {
                "status": "SKIPPED",
                "reset": False,
                "marker": True,
                "marker_value": MARKER_VALUE,
                "legacy_sqlite_removed": [],
                "legacy_sqlite_cleanup_errors": [],
            }
        if current not in (None, ""):
            raise RuntimeError("G2B_V41_FRESH_START_MARKER_MISMATCH")

        prior = {
            "app_schema": _schema_exists(conn, app_schema),
            "budget_schema": _schema_exists(conn, budget_schema),
            "legacy_sqlite": _legacy_sqlite_present(),
        }
        if any(prior.values()):
            # Never reinterpret old 'destructive approval' flags as permission
            # to erase live PostgreSQL history or remove a legacy SQLite file.
            # Missing release metadata needs explicit, separate read-only
            # diagnosis, not in-band migration during production startup.
            raise RuntimeError("G2B_V41_EXISTING_STORAGE_PROTECTED")

        for schema in (app_schema, budget_schema):
            conn.execute(text(f"CREATE SCHEMA {_quote_schema(conn, schema)}"))
        _ensure_marker_table(conn)
        _write_marker(conn, MARKER_VALUE)

    return {
        "status": "COMPLETE",
        "reset": False,
        "marker": True,
        "marker_value": MARKER_VALUE,
        "prior": prior,
        "legacy_sqlite_removed": [],
        "legacy_sqlite_cleanup_errors": [],
    }
