"""PostgreSQL-backed normalized budget foundation for G2B 4.1.

Source JSON is transient. PostgreSQL keeps canonical budget/project fields, source
hashes, checkpoints and bounded normalized revision history only.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
import hashlib
import json
import os
import re
import threading
import uuid

import g2b_database
import budget_normalizer_v41

from sqlalchemy import (
    BigInteger, Column, Float, Index, Integer, JSON, MetaData, String, Table, Text,
    UniqueConstraint, and_, create_engine, delete, func, insert, inspect, literal, or_, select,
    text, tuple_, update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url

BUDGET_DATASETS = frozenset({"budget", "budget_appropriation", "education_budget"})
DEFAULT_SCHEMA = "g2b_budget"
DEFAULT_RETENTION_DAYS = 365
DEFAULT_RECEIPT_RETENTION_DAYS = 3

_ENGINE = None
_TABLES = None
_ENGINE_URL = None
_ENGINE_CONFIG = None
_LAST_ERROR_CODE = ""
_ENGINE_INIT_LOCK = threading.RLock()


def _safe_error_code(exc):
    if isinstance(exc, RuntimeError):
        message = str(exc or "").strip()
        if (
            message.startswith("BUDGET_POSTGRES_")
            or message.startswith("G2B_BUDGET_")
            or message.startswith("G2B_DATABASE_")
            or message.startswith("G2B_SCHEMA_")
            or message.startswith("G2B_APP_")
        ):
            return message[:180]
    return type(exc).__name__


def postgres_last_error_code():
    return str(_LAST_ERROR_CODE or "")


@contextmanager
def operational_cycle_lease(name="g2b_v41_operational_cycle", *, shared=False):
    """Non-blocking cross-process advisory lease.

    shared=True allows independent manual source cycles to coexist while still
    conflicting with the exclusive unified all-source cycle.
    """
    engine, _tables = _engine_and_tables()
    if engine.dialect.name != "postgresql":
        yield True
        return

    conn = engine.connect()
    acquired = False
    lock_sql = (
        "SELECT pg_try_advisory_lock_shared(hashtext(:name))"
        if shared
        else "SELECT pg_try_advisory_lock(hashtext(:name))"
    )
    unlock_sql = (
        "SELECT pg_advisory_unlock_shared(hashtext(:name))"
        if shared
        else "SELECT pg_advisory_unlock(hashtext(:name))"
    )
    try:
        acquired = bool(conn.execute(
            text(lock_sql),
            {"name": str(name)},
        ).scalar())
        yield acquired
    finally:
        if acquired:
            try:
                conn.execute(
                    text(unlock_sql),
                    {"name": str(name)},
                )
            except Exception:
                pass
        conn.close()


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _flag(name, default=False):
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _safe_schema():
    return g2b_database.budget_schema()


def _env_int(name, default, *, lower, upper):
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        value = int(default)
    return max(int(lower), min(int(upper), value))


def _connect_timeout_seconds():
    return _env_int(
        "G2B_BUDGET_CONNECT_TIMEOUT_SECONDS", 3, lower=1, upper=15
    )


def _pool_size():
    return _env_int("G2B_BUDGET_POOL_SIZE", 3, lower=1, upper=10)


def _max_overflow():
    return _env_int("G2B_BUDGET_MAX_OVERFLOW", 1, lower=0, upper=10)


def _pool_timeout_seconds():
    return _env_int("G2B_BUDGET_POOL_TIMEOUT_SECONDS", 5, lower=1, upper=30)


def _pool_recycle_seconds():
    return _env_int("G2B_BUDGET_POOL_RECYCLE_SECONDS", 900, lower=60, upper=3600)


def _statement_timeout_ms():
    return _env_int(
        "G2B_BUDGET_STATEMENT_TIMEOUT_MS", 120000, lower=5000, upper=600000
    )


def _lock_timeout_ms():
    return _env_int(
        "G2B_BUDGET_LOCK_TIMEOUT_MS", 5000, lower=1000, upper=60000
    )


def _retention_batch_size():
    return _env_int(
        "G2B_BUDGET_RETENTION_BATCH_SIZE", 5000, lower=100, upper=20000
    )


def _url_candidates():
    """Compatibility iterator for diagnostics/tests.

    Production uses the canonical G2B_DATABASE_URL resolver.  A SQLite URL is still
    accepted only when G2B_TEST_MODE=1 so the existing unit suite stays hermetic.
    """
    legacy = str(os.getenv("G2B_BUDGET_DATABASE_URL", "") or "").strip()
    if _flag("G2B_TEST_MODE") and legacy:
        try:
            url = make_url(legacy)
        except Exception:
            raise RuntimeError("G2B_BUDGET_DATABASE_URL_INVALID") from None
        if url.drivername in {"sqlite", "sqlite+pysqlite"}:
            yield "G2B_BUDGET_DATABASE_URL", legacy
            return
    value = g2b_database.resolve_database_url()
    if value:
        yield g2b_database.database_source_label() or "G2B_DATABASE_URL", value


def resolve_database_url():
    for _source, raw in _url_candidates():
        try:
            url = make_url(raw)
        except Exception:
            raise RuntimeError("G2B_DATABASE_URL_INVALID") from None
        if url.drivername in {"postgres", "postgresql", "postgresql+psycopg"}:
            return url.set(drivername="postgresql+psycopg").render_as_string(
                hide_password=False
            )
        if _flag("G2B_TEST_MODE") and url.drivername in {"sqlite", "sqlite+pysqlite"}:
            return url.render_as_string(hide_password=False)
        raise RuntimeError("G2B_DATABASE_URL_POSTGRESQL_REQUIRED")
    return ""


def postgres_url_present():
    if _flag("G2B_TEST_MODE"):
        legacy = str(os.getenv("G2B_BUDGET_DATABASE_URL", "") or "").strip()
        if legacy:
            return True
    return g2b_database.database_url_present()


def postgres_configured():
    try:
        return bool(resolve_database_url())
    except RuntimeError:
        return False


def _json_type():
    return JSON().with_variant(JSONB, "postgresql")


def _build_tables(schema):
    metadata = MetaData(schema=schema)
    payload_type = _json_type()

    observations = Table(
        "budget_source_observations", metadata,
        Column("id", String(32), primary_key=True),
        Column("dataset", String(40), nullable=False),
        Column("source_system", String(200), nullable=False, default=""),
        Column("source_operation", String(120), nullable=False, default=""),
        Column("record_key", String(180), nullable=False),
        Column("source_date", String(20), nullable=False, default=""),
        Column("sha256", String(64), nullable=False),
        Column("quality", String(20), nullable=False, default="RAW"),
        Column("issues", payload_type, nullable=False, default=list),
        Column("observed_at", String(40), nullable=False),
        UniqueConstraint("dataset", "record_key", "sha256", name="uq_budget_observation_payload"),
    )
    Index("ix_budget_observation_dataset_date", observations.c.dataset, observations.c.source_date)
    Index("ix_budget_observation_record", observations.c.dataset, observations.c.record_key)
    Index("ix_budget_observation_observed", observations.c.observed_at)

    states = Table(
        "budget_record_states", metadata,
        Column("dataset", String(40), primary_key=True),
        Column("record_key", String(180), primary_key=True),
        Column("observation_id", String(32), nullable=False),
        Column("payload_sha256", String(64), nullable=False),
        Column("source_date", String(20), nullable=False, default=""),
        Column("last_seen_at", String(40), nullable=False),
    )
    Index("ix_budget_state_seen", states.c.last_seen_at)
    Index("ix_budget_state_observation", states.c.observation_id)

    project_revisions = Table(
        "budget_project_revisions", metadata,
        Column("observation_id", String(32), primary_key=True),
        Column("dataset", String(40), nullable=False),
        Column("record_key", String(180), nullable=False),
        Column("source_system", String(200), nullable=False, default=""),
        Column("source_operation", String(120), nullable=False, default=""),
        Column("source_date", String(20), nullable=False, default=""),
        Column("source_layer", String(40), nullable=False, default=""),
        Column("fiscal_year", Integer, nullable=False, default=0),
        Column("snapshot_date", String(20), nullable=False, default=""),
        Column("region_code", String(80), nullable=False, default=""),
        Column("region_name", String(200), nullable=False, default=""),
        Column("org_code", String(120), nullable=False, default=""),
        Column("org_name", String(300), nullable=False, default=""),
        Column("dept_code", String(120), nullable=False, default=""),
        Column("dept_name", String(300), nullable=False, default=""),
        Column("institution_code", String(120), nullable=False, default=""),
        Column("institution_name", String(300), nullable=False, default=""),
        Column("project_code", String(160), nullable=False, default=""),
        Column("project_name", Text, nullable=False, default=""),
        Column("field_code", String(120), nullable=False, default=""),
        Column("field_name", String(300), nullable=False, default=""),
        Column("section_code", String(120), nullable=False, default=""),
        Column("section_name", String(300), nullable=False, default=""),
        Column("account_code", String(120), nullable=False, default=""),
        Column("account_name", String(300), nullable=False, default=""),
        Column("budget_amount", BigInteger, nullable=False, default=0),
        Column("appropriation_amount", BigInteger, nullable=False, default=0),
        Column("executed_amount", BigInteger, nullable=False, default=0),
        Column("remaining_amount", BigInteger, nullable=False, default=0),
        Column("national_amount", BigInteger, nullable=False, default=0),
        Column("province_amount", BigInteger, nullable=False, default=0),
        Column("local_amount", BigInteger, nullable=False, default=0),
        Column("other_amount", BigInteger, nullable=False, default=0),
        Column("payload_sha256", String(64), nullable=False),
        Column("observed_at", String(40), nullable=False),
    )
    Index("ix_budget_project_revision_record", project_revisions.c.dataset, project_revisions.c.record_key)
    Index("ix_budget_project_revision_observed", project_revisions.c.observed_at)
    Index(
        "ix_budget_project_revision_dataset_date",
        project_revisions.c.dataset,
        project_revisions.c.source_date,
    )

    checkpoints = Table(
        "budget_collection_checkpoints", metadata,
        Column("dataset", String(40), primary_key=True),
        Column("scope_key", String(240), primary_key=True),
        Column("cursor_value", Text, nullable=False, default=""),
        Column("range_start", String(40), nullable=False, default=""),
        Column("range_end", String(40), nullable=False, default=""),
        Column("page_no", Integer, nullable=False, default=0),
        Column("page_size", Integer, nullable=False, default=0),
        Column("last_page_fingerprint", String(64), nullable=False, default=""),
        Column("source_total", BigInteger, nullable=False, default=0),
        Column("fetched_count", BigInteger, nullable=False, default=0),
        Column("saved_count", BigInteger, nullable=False, default=0),
        Column("status", String(20), nullable=False, default="IDLE"),
        Column("last_error", String(240), nullable=False, default=""),
        Column("updated_at", String(40), nullable=False),
    )
    Index("ix_budget_checkpoint_updated", checkpoints.c.updated_at)

    pages = Table(
        "budget_collection_pages", metadata,
        Column("dataset", String(40), primary_key=True),
        Column("scope_key", String(240), primary_key=True),
        Column("generation", String(40), primary_key=True),
        Column("page_no", Integer, primary_key=True),
        Column("page_size", Integer, nullable=False),
        Column("response_hash", String(64), nullable=False),
        Column("item_count", Integer, nullable=False),
        Column("source_total", BigInteger, nullable=False),
        Column("terminal_reason", String(80), nullable=False, default=""),
    )
    items = Table(
        "budget_collection_items", metadata,
        Column("dataset", String(40), primary_key=True),
        Column("scope_key", String(240), primary_key=True),
        Column("generation", String(40), primary_key=True),
        Column("source_key", String(180), primary_key=True),
        Column("page_no", Integer, nullable=False),
        Column("payload_sha256", String(64), nullable=False),
    )
    Index("ix_budget_collection_items_page", items.c.dataset, items.c.scope_key, items.c.generation, items.c.page_no)

    classifications = Table(
        "budget_classifications", metadata,
        Column("dataset", String(40), primary_key=True),
        Column("record_key", String(180), primary_key=True),
        Column("classifier_version", String(60), primary_key=True),
        Column("primary_category", String(40), nullable=False, default="UNCLASSIFIED"),
        Column("subcategory", String(80), nullable=False, default=""),
        Column("confidence", Float, nullable=False, default=0),
        Column("reason", Text, nullable=False, default=""),
        Column("source_payload_sha256", String(64), nullable=False, default=""),
        Column("classified_at", String(40), nullable=False),
    )
    Index(
        "ix_budget_classification_category",
        classifications.c.dataset, classifications.c.primary_category,
    )
    Index(
        "ix_budget_classification_category_version",
        classifications.c.primary_category,
        classifications.c.classifier_version,
        classifications.c.dataset,
        classifications.c.record_key,
    )

    projects = Table(
        "budget_projects", metadata,
        Column("dataset", String(40), primary_key=True),
        Column("record_key", String(180), primary_key=True),
        Column("source_system", String(200), nullable=False, default=""),
        Column("source_operation", String(120), nullable=False, default=""),
        Column("source_layer", String(40), nullable=False, default=""),
        Column("fiscal_year", Integer, nullable=False, default=0),
        Column("snapshot_date", String(20), nullable=False, default=""),
        Column("region_code", String(80), nullable=False, default=""),
        Column("region_name", String(200), nullable=False, default=""),
        Column("org_code", String(120), nullable=False, default=""),
        Column("org_name", String(300), nullable=False, default=""),
        Column("dept_code", String(120), nullable=False, default=""),
        Column("dept_name", String(300), nullable=False, default=""),
        Column("institution_code", String(120), nullable=False, default=""),
        Column("institution_name", String(300), nullable=False, default=""),
        Column("project_code", String(160), nullable=False, default=""),
        Column("project_name", Text, nullable=False, default=""),
        Column("field_code", String(120), nullable=False, default=""),
        Column("field_name", String(300), nullable=False, default=""),
        Column("section_code", String(120), nullable=False, default=""),
        Column("section_name", String(300), nullable=False, default=""),
        Column("account_code", String(120), nullable=False, default=""),
        Column("account_name", String(300), nullable=False, default=""),
        Column("budget_amount", BigInteger, nullable=False, default=0),
        Column("appropriation_amount", BigInteger, nullable=False, default=0),
        Column("executed_amount", BigInteger, nullable=False, default=0),
        Column("remaining_amount", BigInteger, nullable=False, default=0),
        Column("national_amount", BigInteger, nullable=False, default=0),
        Column("province_amount", BigInteger, nullable=False, default=0),
        Column("local_amount", BigInteger, nullable=False, default=0),
        Column("other_amount", BigInteger, nullable=False, default=0),
        Column("amounts", payload_type, nullable=False, default=dict),
        Column("payload_sha256", String(64), nullable=False, default=""),
        Column("updated_at", String(40), nullable=False),
    )
    Index("ix_budget_projects_year_layer", projects.c.fiscal_year, projects.c.source_layer)
    Index("ix_budget_projects_region", projects.c.region_code, projects.c.region_name)
    Index("ix_budget_projects_remaining", projects.c.remaining_amount)

    department_evidence = Table(
        "budget_department_detail_evidence", metadata,
        Column("dataset", String(40), primary_key=True),
        Column("record_key", String(180), primary_key=True),
        Column("fiscal_year", Integer, nullable=False, default=0),
        Column("org_code", String(120), nullable=False, default=""),
        Column("project_code", String(160), nullable=False, default=""),
        Column("dept_code", String(120), nullable=False, default=""),
        Column("department_source_name", String(300), nullable=False, default=""),
        Column("dept_name", String(300), nullable=False, default=""),
        Column("bureau_name", String(200), nullable=False, default=""),
        Column("executor_name", String(300), nullable=False, default=""),
        Column("source_snapshot_date", String(20), nullable=False, default=""),
        Column("source_payload_sha256", String(64), nullable=False, default=""),
        Column("verified_at", String(40), nullable=False),
    )
    Index(
        "ix_budget_department_evidence_identity",
        department_evidence.c.fiscal_year,
        department_evidence.c.org_code,
        department_evidence.c.project_code,
        department_evidence.c.dept_code,
    )

    return {
        "metadata": metadata,
        "observations": observations,
        "states": states,
        "project_revisions": project_revisions,
        "checkpoints": checkpoints,
        "pages": pages,
        "items": items,
        "classifications": classifications,
        "projects": projects,
        "department_evidence": department_evidence,
    }


def _schema_exists(conn, schema):
    if not schema:
        return True
    return conn.execute(
        text("SELECT 1 FROM pg_namespace WHERE nspname=:schema"),
        {"schema": str(schema)},
    ).first() is not None


def _ensure_database_schema(engine, schema):
    """Create the dedicated schema only when it is actually absent.

    Pre-provisioned production roles may have USAGE/CREATE on tables without
    database-level CREATE SCHEMA. Reusing an existing schema must not require that
    broader privilege on every restart.
    """
    if not schema:
        return
    with engine.begin() as conn:
        if _schema_exists(conn, schema):
            return
        try:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        except Exception as exc:
            raise RuntimeError("BUDGET_POSTGRES_SCHEMA_CREATE_FAILED") from exc


def _qualified_index_name(engine, index):
    preparer = engine.dialect.identifier_preparer
    name = preparer.quote(str(index.name))
    schema = index.table.schema
    if schema:
        return preparer.quote_schema(schema) + "." + name
    return name


def _concurrent_index_sql(engine, index):
    preparer = engine.dialect.identifier_preparer
    table = index.table
    table_name = preparer.quote(table.name)
    if table.schema:
        table_name = (
            preparer.quote_schema(table.schema) + "." + table_name
        )

    columns = []
    for expression in index.expressions:
        name = getattr(expression, "name", "")
        if not name:
            raise RuntimeError(
                f"BUDGET_POSTGRES_INDEX_EXPRESSION_UNSUPPORTED:{index.name}"
            )
        columns.append(preparer.quote(str(name)))

    unique = "UNIQUE " if bool(index.unique) else ""
    return (
        f"CREATE {unique}INDEX CONCURRENTLY IF NOT EXISTS "
        f"{preparer.quote(str(index.name))} ON {table_name} "
        f"({','.join(columns)})"
    )


def _postgres_index_states(conn, table):
    rows = conn.execute(
        text(
            """SELECT idx.relname AS name,
                      pi.indisvalid AS is_valid,
                      pi.indisready AS is_ready
               FROM pg_index pi
               JOIN pg_class tbl ON tbl.oid=pi.indrelid
               JOIN pg_class idx ON idx.oid=pi.indexrelid
               JOIN pg_namespace ns ON ns.oid=tbl.relnamespace
               WHERE ns.nspname=:schema AND tbl.relname=:table"""
        ),
        {
            "schema": str(table.schema or "public"),
            "table": str(table.name),
        },
    ).mappings().all()
    return {
        str(row["name"]): {
            "valid": bool(row["is_valid"]),
            "ready": bool(row["is_ready"]),
        }
        for row in rows
    }


def _ensure_postgres_indexes(engine, tables):
    lock_name = f"{_safe_schema()}:budget_index_migration"
    with engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as conn:
        conn.execute(
            text("SELECT pg_advisory_lock(hashtext(:name))"),
            {"name": lock_name},
        )
        try:
            for table_name, table in tables.items():
                if table_name == "metadata":
                    continue
                states = _postgres_index_states(conn, table)
                for index in sorted(
                    table.indexes,
                    key=lambda value: str(value.name or ""),
                ):
                    index_name = str(index.name or "")
                    state = states.get(index_name)
                    if state and state["valid"]:
                        continue
                    if state:
                        conn.execute(text(
                            "DROP INDEX CONCURRENTLY IF EXISTS "
                            + _qualified_index_name(engine, index)
                        ))
                    conn.execute(text(_concurrent_index_sql(engine, index)))
                    states[index_name] = {"valid": True, "ready": True}
        finally:
            try:
                conn.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:name))"),
                    {"name": lock_name},
                )
            except Exception:
                pass


def _ensure_declared_indexes(engine, tables):
    """Apply additive index migrations safely to existing tables."""
    if engine.dialect.name == "postgresql":
        try:
            _ensure_postgres_indexes(engine, tables)
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc).startswith(
                "BUDGET_POSTGRES_"
            ):
                raise
            raise RuntimeError(
                "BUDGET_POSTGRES_INDEX_MIGRATION_FAILED"
            ) from exc
        return

    inspector = inspect(engine)
    for name, table in tables.items():
        if name == "metadata":
            continue
        try:
            existing = {
                str(row.get("name") or "")
                for row in inspector.get_indexes(
                    table.name,
                    schema=table.schema,
                )
            }
        except Exception as exc:
            raise RuntimeError(
                f"BUDGET_POSTGRES_INDEX_INSPECTION_FAILED:{table.name}"
            ) from exc

        for index in sorted(
            table.indexes, key=lambda value: str(value.name or "")
        ):
            index_name = str(index.name or "")
            if index_name in existing:
                continue
            try:
                index.create(bind=engine, checkfirst=True)
                existing.add(index_name)
            except Exception as exc:
                raise RuntimeError(
                    f"BUDGET_POSTGRES_INDEX_MIGRATION_FAILED:{index_name}"
                ) from exc


def _verify_table_contract(engine, tables):
    """Fail with one deterministic contract error for partial/old schemas."""
    inspector = inspect(engine)
    for name, table in tables.items():
        if name == "metadata":
            continue
        try:
            actual = {
                str(column["name"])
                for column in inspector.get_columns(
                    table.name,
                    schema=table.schema,
                )
            }
        except Exception as exc:
            raise RuntimeError(
                f"BUDGET_POSTGRES_SCHEMA_INSPECTION_FAILED:{table.name}"
            ) from exc
        missing = sorted(set(table.c.keys()) - actual)
        if missing:
            raise RuntimeError(
                "BUDGET_POSTGRES_SCHEMA_CONTRACT_MISMATCH:"
                + table.name + ":" + ",".join(missing)
            )

        expected_pk = {
            str(column.name) for column in table.primary_key.columns
        }
        actual_pk = {
            str(column)
            for column in (
                inspector.get_pk_constraint(
                    table.name,
                    schema=table.schema,
                ).get("constrained_columns") or []
            )
        }
        if expected_pk != actual_pk:
            raise RuntimeError(
                "BUDGET_POSTGRES_PRIMARY_KEY_MISMATCH:"
                + table.name
            )

        expected_unique = {
            frozenset(str(column.name) for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        if expected_unique:
            actual_unique = {
                frozenset(str(column) for column in (
                    row.get("column_names") or []
                ))
                for row in inspector.get_unique_constraints(
                    table.name,
                    schema=table.schema,
                )
            }
            missing_unique = expected_unique - actual_unique
            if missing_unique:
                raise RuntimeError(
                    "BUDGET_POSTGRES_UNIQUE_CONSTRAINT_MISMATCH:"
                    + table.name
                )


def _engine_config_key(url_text, schema):
    return (str(url_text), str(schema or ""))


def _engine_and_tables_unlocked():
    global _ENGINE, _TABLES, _ENGINE_URL, _ENGINE_CONFIG, _LAST_ERROR_CODE
    url_text = resolve_database_url()
    if not url_text:
        raise RuntimeError("BUDGET_POSTGRES_NOT_CONFIGURED")
    url = make_url(url_text)
    schema = None if url.drivername.startswith("sqlite") else _safe_schema()
    config_key = _engine_config_key(url_text, schema)
    if _ENGINE is not None and _ENGINE_CONFIG == config_key:
        return _ENGINE, _TABLES

    old_engine = _ENGINE
    if schema:
        engine = g2b_database.engine()
    else:
        # SQLite exists only for explicit test-mode fixtures.
        engine = create_engine(url_text, pool_pre_ping=True, future=True)
    try:
        _ensure_database_schema(engine, schema)
        tables = _build_tables(schema)
        try:
            tables["metadata"].create_all(engine)
        except Exception as exc:
            raise RuntimeError(
                "BUDGET_POSTGRES_TABLE_MIGRATION_FAILED"
            ) from exc
        _verify_table_contract(engine, tables)
        _ensure_declared_indexes(engine, tables)
    except Exception as exc:
        _LAST_ERROR_CODE = _safe_error_code(exc)
        engine.dispose()
        raise
    _ENGINE, _TABLES, _ENGINE_URL, _ENGINE_CONFIG = (
        engine, tables, url_text, config_key
    )
    _LAST_ERROR_CODE = ""
    if old_engine is not None and old_engine is not engine:
        try:
            if old_engine.dialect.name != "postgresql":
                old_engine.dispose()
        except Exception:
            pass
    return engine, tables



def _engine_and_tables():
    """Serialize first-use schema/table initialization inside one process."""
    with _ENGINE_INIT_LOCK:
        return _engine_and_tables_unlocked()


def reset_engine_cache():
    """Tests/config reload only; does not drop data."""
    global _ENGINE, _TABLES, _ENGINE_URL, _ENGINE_CONFIG
    if _ENGINE is not None:
        try:
            if _ENGINE.dialect.name != "postgresql":
                _ENGINE.dispose()
        except Exception:
            pass
    _ENGINE = _TABLES = _ENGINE_URL = _ENGINE_CONFIG = None
    g2b_database.reset_engine_cache()


def postgres_ready():
    """Return whether the dedicated budget store and its core table are usable now."""
    global _LAST_ERROR_CODE
    if not postgres_url_present():
        _LAST_ERROR_CODE = "BUDGET_POSTGRES_NOT_CONFIGURED"
        return False
    try:
        engine, tables = _engine_and_tables()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.execute(
                select(tables["states"].c.dataset).limit(1)
            ).first()
        _LAST_ERROR_CODE = ""
        return True
    except Exception as exc:
        _LAST_ERROR_CODE = _safe_error_code(exc)
        # Drop stale pooled connections/metadata after an outage or failover. The
        # next readiness/collection attempt will recreate or migrate the schema.
        reset_engine_cache()
        return False


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _semantic_payload(dataset, payload):
    """Return the budget business state used for change detection.

    QWGJK exe_ymd is the requested snapshot date. It changes on every daily
    collection even when the underlying budget project is unchanged. Preserve
    the original JSON, but exclude only that transport snapshot dimension from
    the semantic change digest.
    """
    if not isinstance(payload, dict):
        raise ValueError("BUDGET_PAYLOAD_OBJECT_REQUIRED")
    if str(dataset) != "budget":
        return payload
    return {
        key: value
        for key, value in payload.items()
        if "".join(ch for ch in str(key).casefold() if ch.isalnum()) != "exeymd"
    }


def observation_digest(dataset, payload):
    return hashlib.sha256(
        _canonical(_semantic_payload(dataset, payload)).encode("utf-8")
    ).hexdigest()


def _write(engine, existing=None):
    if existing is not None:
        class _Existing:
            def __enter__(self):
                return existing
            def __exit__(self, exc_type, exc, tb):
                return False
        return _Existing()
    return engine.begin()


def _normalized_values(dataset, record_key, payload, *, source_system="", source_operation="", source_date="", observed_at=""):
    fact = budget_normalizer_v41.normalize_record(
        dataset, payload, source_date=source_date
    )
    digest = observation_digest(dataset, payload)
    return {
        "dataset": str(dataset),
        "record_key": str(record_key),
        "source_system": str(source_system or ""),
        "source_operation": str(source_operation or ""),
        **fact,
        "amounts": {
            "budget_amount": int(fact.get("budget_amount") or 0),
            "appropriation_amount": int(fact.get("appropriation_amount") or 0),
            "executed_amount": int(fact.get("executed_amount") or 0),
            "remaining_amount": int(fact.get("remaining_amount") or 0),
        },
        "payload_sha256": digest,
        "updated_at": str(observed_at or _now_iso()),
    }


def preserve_observation(dataset, record_key, payload, *, source_system="", source_operation="",
                         source_date="", quality="NORMALIZED", issues=None, _conn=None,
                         advance_current=True):
    """Normalize one source row and persist no source JSON.

    Historical backfill may set advance_current=False. Immutable normalized
    observation/revision history is stored while live current state stays newest.
    """
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not record_key:
        raise ValueError("BUDGET_RECORD_KEY_REQUIRED")
    if not isinstance(payload, dict):
        raise ValueError("BUDGET_PAYLOAD_OBJECT_REQUIRED")

    digest = observation_digest(dataset, payload)
    engine, t = _engine_and_tables()
    now = _now_iso()
    obs, state = t["observations"], t["states"]
    revisions, projects = t["project_revisions"], t["projects"]
    normalized = _normalized_values(
        dataset,
        record_key,
        payload,
        source_system=source_system,
        source_operation=source_operation,
        source_date=source_date,
        observed_at=now,
    )

    with _write(engine, _conn) as conn:
        existing = conn.execute(
            select(obs.c.id, obs.c.observed_at).where(
                and_(obs.c.dataset == dataset, obs.c.record_key == record_key, obs.c.sha256 == digest)
            ).limit(1)
        ).mappings().first()
        observation_id = str(existing["id"]) if existing else uuid.uuid4().hex
        if existing is None:
            conn.execute(insert(obs).values(
                id=observation_id,
                dataset=dataset,
                source_system=str(source_system or ""),
                source_operation=str(source_operation or ""),
                record_key=str(record_key),
                source_date=str(source_date or ""),
                sha256=digest,
                quality=str(quality or "NORMALIZED"),
                issues=list(issues or []),
                observed_at=now,
            ))
            revision_values = {
                k: v for k, v in normalized.items()
                if k not in {"amounts", "updated_at"}
            }
            revision_values.update(
                observation_id=observation_id,
                source_date=str(source_date or ""),
                observed_at=now,
            )
            conn.execute(insert(revisions).values(**revision_values))

        if not bool(advance_current):
            return {
                "sha256": digest,
                "observation_id": observation_id,
                "new_observation": existing is None,
                "storage": "NORMALIZED_HISTORY_ONLY",
                "current_advanced": False,
            }

        current = conn.execute(
            select(state.c.observation_id).where(
                and_(state.c.dataset == dataset, state.c.record_key == record_key)
            )
        ).first()
        state_values = dict(
            observation_id=observation_id,
            payload_sha256=digest,
            source_date=str(source_date or ""),
            last_seen_at=now,
        )
        if current:
            conn.execute(
                update(state).where(
                    and_(state.c.dataset == dataset, state.c.record_key == record_key)
                ).values(**state_values)
            )
        else:
            conn.execute(insert(state).values(dataset=dataset, record_key=record_key, **state_values))

        exists_project = conn.execute(
            select(projects.c.dataset).where(
                and_(projects.c.dataset == dataset, projects.c.record_key == record_key)
            )
        ).first()
        if exists_project:
            writable = {
                k: v for k, v in normalized.items()
                if k not in {"dataset", "record_key"}
            }
            conn.execute(
                update(projects).where(
                    and_(projects.c.dataset == dataset, projects.c.record_key == record_key)
                ).values(**writable)
            )
        else:
            conn.execute(insert(projects).values(**normalized))

    return {
        "sha256": digest,
        "observation_id": observation_id,
        "new_observation": existing is None,
        "storage": "NORMALIZED_ONLY",
        "current_advanced": True,
    }


def _current_projection_row(row):
    item = dict(row)
    dataset = str(item["dataset"])
    return {
        "dataset": dataset,
        "record_key": str(item["record_key"]),
        "source_date": str(item.get("source_date") or item.get("snapshot_date") or ""),
        "last_seen_at": str(item.get("last_seen_at") or item.get("updated_at") or ""),
        "payload_sha256": str(item.get("payload_sha256") or ""),
        "source_system": str(item.get("source_system") or ""),
        "source_operation": str(item.get("source_operation") or ""),
        "observed_at": str(item.get("updated_at") or ""),
        "payload": budget_normalizer_v41.compat_payload(dataset, item),
        "quality": "NORMALIZED",
        "issues": [],
    }


def current_row_batches(datasets=None, *, batch_size=1000):
    """Yield current normalized budget rows through the legacy adapter shape."""
    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    size = max(1, min(int(batch_size), 5000))
    stmt = (
        select(
            projects,
            state.c.source_date.label("source_date"),
            state.c.last_seen_at.label("last_seen_at"),
            state.c.payload_sha256.label("current_payload_sha256"),
        )
        .select_from(
            state.join(
                projects,
                and_(
                    state.c.dataset == projects.c.dataset,
                    state.c.record_key == projects.c.record_key,
                ),
            )
        )
        .where(state.c.dataset.in_(selected))
        .order_by(state.c.dataset, state.c.record_key)
    )
    with engine.connect() as conn:
        result = conn.execution_options(
            stream_results=True,
            max_row_buffer=size,
        ).execute(stmt).mappings()
        while True:
            rows = result.fetchmany(size)
            if not rows:
                break
            yield [_current_projection_row(row) for row in rows]


def current_rows_for_keys(dataset, record_keys, *, batch_size=1000):
    """Yield normalized current rows for explicit keys."""
    name = str(dataset)
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    keys = list(dict.fromkeys(str(key) for key in record_keys if str(key)))
    size = max(1, min(int(batch_size), 2000))
    if not keys:
        return

    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    for start in range(0, len(keys), size):
        chunk = keys[start:start + size]
        stmt = (
            select(
                projects,
                state.c.source_date.label("source_date"),
                state.c.last_seen_at.label("last_seen_at"),
            )
            .select_from(
                state.join(
                    projects,
                    and_(
                        state.c.dataset == projects.c.dataset,
                        state.c.record_key == projects.c.record_key,
                    ),
                )
            )
            .where(and_(
                state.c.dataset == name,
                state.c.record_key.in_(chunk),
            ))
            .order_by(state.c.record_key)
        )
        with engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        if rows:
            yield [_current_projection_row(row) for row in rows]


def current_rows(datasets=None):
    rows = []
    for batch in current_row_batches(datasets, batch_size=1000):
        rows.extend(batch)
    return rows



def current_organization_names(
    datasets=None,
    *,
    fiscal_year=None,
    source_layers=None,
    region_terms=None,
):
    """Return distinct current institution names from stored PostgreSQL facts.

    This is a read-only helper for the nationwide budget selector. It never calls
    an external source and materializes only short organization-name strings.
    """
    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")

    stmt = (
        select(
            projects.c.org_name,
            projects.c.institution_name,
            projects.c.dept_name,
        )
        .select_from(
            state.join(
                projects,
                and_(
                    state.c.dataset == projects.c.dataset,
                    state.c.record_key == projects.c.record_key,
                ),
            )
        )
        .where(state.c.dataset.in_(selected))
    )
    if fiscal_year is not None:
        stmt = stmt.where(projects.c.fiscal_year == int(fiscal_year))

    layers = [
        str(value or "").strip()
        for value in (source_layers or ())
        if str(value or "").strip()
    ]
    if layers:
        stmt = stmt.where(projects.c.source_layer.in_(layers))

    terms = [
        str(value or "").strip()
        for value in (region_terms or ())
        if str(value or "").strip()
    ]
    if terms:
        checks = []
        for value in terms:
            checks.extend([
                projects.c.region_name.startswith(value),
                projects.c.org_name.startswith(value),
                projects.c.institution_name.startswith(value),
            ])
        stmt = stmt.where(or_(*checks))

    stmt = stmt.distinct().order_by(
        projects.c.org_name.asc(),
        projects.c.institution_name.asc(),
    )
    names = set()
    with engine.connect() as conn:
        for row in conn.execute(stmt):
            for value in row:
                text_value = str(value or "").strip()
                if text_value:
                    names.add(text_value)
    return sorted(names, key=lambda value: value.casefold())



def current_institution_names(
    datasets=None,
    *,
    fiscal_year=None,
    source_layers=None,
    region_terms=None,
):
    """Return distinct stored institution/local-government names for one region."""
    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")

    stmt = (
        select(projects.c.org_name, projects.c.institution_name)
        .select_from(
            state.join(
                projects,
                and_(
                    state.c.dataset == projects.c.dataset,
                    state.c.record_key == projects.c.record_key,
                ),
            )
        )
        .where(state.c.dataset.in_(selected))
    )
    if fiscal_year is not None:
        stmt = stmt.where(projects.c.fiscal_year == int(fiscal_year))

    layers = [
        str(value or "").strip()
        for value in (source_layers or ())
        if str(value or "").strip()
    ]
    if layers:
        stmt = stmt.where(projects.c.source_layer.in_(layers))

    terms = [
        str(value or "").strip()
        for value in (region_terms or ())
        if str(value or "").strip()
    ]
    if terms:
        checks = []
        for value in terms:
            checks.extend([
                projects.c.region_name.startswith(value),
                projects.c.org_name.startswith(value),
                projects.c.institution_name.startswith(value),
            ])
        stmt = stmt.where(or_(*checks))

    stmt = stmt.distinct().order_by(
        projects.c.org_name.asc(),
        projects.c.institution_name.asc(),
    )
    names = set()
    with engine.connect() as conn:
        for row in conn.execute(stmt):
            for value in row:
                text_value = str(value or "").strip()
                if text_value:
                    names.add(text_value)
    return sorted(names, key=lambda value: value.casefold())


def current_department_names(
    datasets=None,
    *,
    fiscal_year=None,
    source_layers=None,
    region_terms=None,
    organization_exact_names=None,
    organization_contains_terms=None,
):
    """Return distinct stored department names for the selected institution scope."""
    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")

    stmt = (
        select(projects.c.dept_name)
        .select_from(
            state.join(
                projects,
                and_(
                    state.c.dataset == projects.c.dataset,
                    state.c.record_key == projects.c.record_key,
                ),
            )
        )
        .where(state.c.dataset.in_(selected))
        .where(projects.c.dept_name != "")
    )
    if fiscal_year is not None:
        stmt = stmt.where(projects.c.fiscal_year == int(fiscal_year))

    layers = [
        str(value or "").strip()
        for value in (source_layers or ())
        if str(value or "").strip()
    ]
    if layers:
        stmt = stmt.where(projects.c.source_layer.in_(layers))

    terms = [
        str(value or "").strip()
        for value in (region_terms or ())
        if str(value or "").strip()
    ]
    if terms:
        checks = []
        for value in terms:
            checks.extend([
                projects.c.region_name.startswith(value),
                projects.c.org_name.startswith(value),
                projects.c.institution_name.startswith(value),
            ])
        stmt = stmt.where(or_(*checks))

    exact_names = [
        str(value or "").strip()
        for value in (organization_exact_names or ())
        if str(value or "").strip()
    ]
    contains_terms = [
        str(value or "").strip()
        for value in (organization_contains_terms or ())
        if str(value or "").strip()
    ]
    if exact_names or contains_terms:
        organization_checks = []
        for value in exact_names:
            organization_checks.extend([
                projects.c.org_name == value,
                projects.c.institution_name == value,
                projects.c.dept_name == value,
            ])
        for value in contains_terms:
            pattern = f"%{value}%"
            organization_checks.extend([
                projects.c.org_name.ilike(pattern),
                projects.c.institution_name.ilike(pattern),
                projects.c.dept_name.ilike(pattern),
            ])
        stmt = stmt.where(or_(*organization_checks))

    stmt = stmt.distinct().order_by(projects.c.dept_name.asc())
    with engine.connect() as conn:
        return [
            str(row[0]).strip()
            for row in conn.execute(stmt)
            if str(row[0] or "").strip()
        ]


def current_project_record(dataset, record_key, *, classifier_version=""):
    """Return one current normalized project plus exact-current classification."""
    name = str(dataset or "").strip()
    key = str(record_key or "").strip()
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not key:
        return None

    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    classifications = t["classifications"]
    version = str(classifier_version or "").strip()
    base = state.join(
        projects,
        and_(
            state.c.dataset == projects.c.dataset,
            state.c.record_key == projects.c.record_key,
        ),
    )
    columns = [
        projects,
        state.c.source_date.label("source_date"),
        state.c.last_seen_at.label("last_seen_at"),
    ]
    if version:
        base = base.outerjoin(
            classifications,
            and_(
                classifications.c.dataset == projects.c.dataset,
                classifications.c.record_key == projects.c.record_key,
                classifications.c.classifier_version == version,
                classifications.c.source_payload_sha256 == projects.c.payload_sha256,
            ),
        )
        columns.extend([
            classifications.c.primary_category.label("primary_category"),
            classifications.c.subcategory.label("subcategory"),
            classifications.c.confidence.label("classification_confidence"),
            classifications.c.reason.label("classification_reason"),
        ])

    stmt = (
        select(*columns)
        .select_from(base)
        .where(
            and_(
                state.c.dataset == name,
                state.c.record_key == key,
            )
        )
        .limit(1)
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None



def _detail_identity_text(value):
    return "".join(
        ch for ch in str(value or "").casefold()
        if ch.isalnum()
    )


def department_detail_evidence(dataset, record_key):
    """Return compact verified LOFIN detail evidence for one current project."""
    name = str(dataset or "").strip()
    key = str(record_key or "").strip()
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not key:
        return None
    engine, t = _engine_and_tables()
    evidence = t["department_evidence"]
    with engine.connect() as conn:
        row = conn.execute(
            select(evidence).where(and_(
                evidence.c.dataset == name,
                evidence.c.record_key == key,
            )).limit(1)
        ).mappings().first()
    return dict(row) if row else None


def _detail_evidence_matches_project(project, evidence):
    if not project or not evidence:
        return False
    return bool(
        str(project.get("source_layer") or "").upper() == "DETAIL_EXECUTION"
        and int(project.get("fiscal_year") or 0)
        == int(evidence.get("fiscal_year") or 0)
        and str(project.get("org_code") or "").strip()
        == str(evidence.get("org_code") or "").strip()
        and str(project.get("project_code") or "").strip()
        == str(evidence.get("project_code") or "").strip()
        and str(project.get("dept_code") or "").strip()
        == str(evidence.get("dept_code") or "").strip()
        and bool(str(evidence.get("dept_name") or "").strip())
    )


def apply_department_detail_evidence(dataset, record_key):
    """Reapply already-verified evidence without source I/O."""
    name = str(dataset or "").strip()
    key = str(record_key or "").strip()
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not key:
        return {"applied": 0, "matched": False, "source_io_performed": False}

    engine, t = _engine_and_tables()
    projects = t["projects"]
    evidence = t["department_evidence"]
    with _write(engine) as conn:
        project = conn.execute(
            select(projects).where(and_(
                projects.c.dataset == name,
                projects.c.record_key == key,
            )).limit(1)
        ).mappings().first()
        stored = conn.execute(
            select(evidence).where(and_(
                evidence.c.dataset == name,
                evidence.c.record_key == key,
            )).limit(1)
        ).mappings().first()
        matched = _detail_evidence_matches_project(
            dict(project) if project else None,
            dict(stored) if stored else None,
        )
        applied = 0
        if matched and not str(project.get("dept_name") or "").strip():
            result = conn.execute(
                update(projects)
                .where(and_(
                    projects.c.dataset == name,
                    projects.c.record_key == key,
                    projects.c.dept_name == "",
                ))
                .values(dept_name=str(stored["dept_name"]).strip())
            )
            applied = int(result.rowcount or 0)
    return {
        "applied": applied,
        "matched": bool(matched),
        "source_io_performed": False,
    }


def save_department_detail_evidence(dataset, record_key, detail):
    """Persist compact official-detail evidence and fill only a blank current name."""
    name = str(dataset or "").strip()
    key = str(record_key or "").strip()
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not key or not isinstance(detail, dict):
        raise ValueError("BUDGET_DEPARTMENT_DETAIL_INVALID")

    engine, t = _engine_and_tables()
    projects = t["projects"]
    evidence = t["department_evidence"]
    now = _now_iso()

    with _write(engine) as conn:
        project_row = conn.execute(
            select(projects).where(and_(
                projects.c.dataset == name,
                projects.c.record_key == key,
            )).limit(1)
        ).mappings().first()
        if not project_row:
            raise ValueError("BUDGET_PROJECT_NOT_FOUND")
        project = dict(project_row)
        if str(project.get("source_layer") or "").upper() != "DETAIL_EXECUTION":
            raise ValueError("BUDGET_PROJECT_NOT_QWGJK_DETAIL")

        dept_code = str(project.get("dept_code") or "").strip()
        org_code = str(project.get("org_code") or "").strip()
        project_code = str(project.get("project_code") or "").strip()
        dept_name = str(detail.get("department_name") or "").strip()
        source_name = str(detail.get("department_source_name") or "").strip()
        if not dept_code or not org_code or not project_code or not dept_name:
            raise ValueError("BUDGET_DEPARTMENT_DETAIL_IDENTITY_INCOMPLETE")

        returned_project = str(detail.get("project_name") or "").strip()
        current_project = str(project.get("project_name") or "").strip()
        if (
            returned_project
            and current_project
            and _detail_identity_text(returned_project)
            != _detail_identity_text(current_project)
        ):
            raise ValueError("BUDGET_DEPARTMENT_DETAIL_PROJECT_MISMATCH")

        values = {
            "dataset": name,
            "record_key": key,
            "fiscal_year": int(project.get("fiscal_year") or 0),
            "org_code": org_code,
            "project_code": project_code,
            "dept_code": dept_code,
            "department_source_name": source_name or dept_name,
            "dept_name": dept_name,
            "bureau_name": str(detail.get("bureau_name") or "").strip()[:200],
            "executor_name": str(detail.get("executor_name") or "").strip()[:300],
            "source_snapshot_date": str(
                project.get("snapshot_date") or ""
            ).strip(),
            "source_payload_sha256": str(
                detail.get("source_payload_sha256") or ""
            ).strip()[:64],
            "verified_at": now,
        }
        existing = conn.execute(
            select(evidence.c.dataset).where(and_(
                evidence.c.dataset == name,
                evidence.c.record_key == key,
            )).limit(1)
        ).first()
        if existing:
            conn.execute(
                update(evidence).where(and_(
                    evidence.c.dataset == name,
                    evidence.c.record_key == key,
                )).values(**{
                    k: v for k, v in values.items()
                    if k not in {"dataset", "record_key"}
                })
            )
        else:
            conn.execute(insert(evidence).values(**values))

        applied = 0
        if not str(project.get("dept_name") or "").strip():
            result = conn.execute(
                update(projects)
                .where(and_(
                    projects.c.dataset == name,
                    projects.c.record_key == key,
                    projects.c.fiscal_year == values["fiscal_year"],
                    projects.c.org_code == org_code,
                    projects.c.project_code == project_code,
                    projects.c.dept_code == dept_code,
                    projects.c.dept_name == "",
                ))
                .values(dept_name=dept_name)
            )
            applied = int(result.rowcount or 0)

    return {
        **values,
        "applied": applied,
        "source_io_performed": False,
    }


def repair_current_department_names_from_detail_evidence(
    datasets=("budget",),
    *,
    batch_size=250,
    max_batches=4,
):
    """Restore blank current names only from exact persisted detail evidence."""
    selected = tuple(
        str(value or "").strip()
        for value in (datasets or ("budget",))
        if str(value or "").strip()
    )
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not selected:
        return {
            "scanned": 0,
            "repaired": 0,
            "remaining_repairable": 0,
            "source_io_performed": False,
        }

    size = max(1, min(int(batch_size), 500))
    batches = max(1, min(int(max_batches), 32))
    engine, t = _engine_and_tables()
    projects = t["projects"]
    evidence = t["department_evidence"]
    scanned = 0
    repaired = 0
    last_dataset = ""
    last_record_key = ""

    match = and_(
        evidence.c.dataset == projects.c.dataset,
        evidence.c.record_key == projects.c.record_key,
        evidence.c.fiscal_year == projects.c.fiscal_year,
        evidence.c.org_code == projects.c.org_code,
        evidence.c.project_code == projects.c.project_code,
        evidence.c.dept_code == projects.c.dept_code,
        evidence.c.dept_name != "",
    )
    repairable_exists = (
        select(literal(1)).select_from(evidence).where(match).exists()
    )

    for _ in range(batches):
        with _write(engine) as conn:
            filters = [
                projects.c.dataset.in_(selected),
                projects.c.source_layer == "DETAIL_EXECUTION",
                projects.c.dept_name == "",
                projects.c.dept_code != "",
                repairable_exists,
            ]
            if last_dataset or last_record_key:
                filters.append(or_(
                    projects.c.dataset > last_dataset,
                    and_(
                        projects.c.dataset == last_dataset,
                        projects.c.record_key > last_record_key,
                    ),
                ))
            candidates = conn.execute(
                select(
                    projects.c.dataset,
                    projects.c.record_key,
                )
                .where(and_(*filters))
                .order_by(
                    projects.c.dataset.asc(),
                    projects.c.record_key.asc(),
                )
                .limit(size)
            ).mappings().all()
            if not candidates:
                break
            scanned += len(candidates)
            last_dataset = str(candidates[-1]["dataset"])
            last_record_key = str(candidates[-1]["record_key"])
            keys = [
                (str(row["dataset"]), str(row["record_key"]))
                for row in candidates
            ]
            stored = {
                (str(row["dataset"]), str(row["record_key"])): str(
                    row["dept_name"] or ""
                ).strip()
                for row in conn.execute(
                    select(
                        evidence.c.dataset,
                        evidence.c.record_key,
                        evidence.c.dept_name,
                    ).where(tuple_(
                        evidence.c.dataset,
                        evidence.c.record_key,
                    ).in_(keys))
                ).mappings()
            }
            for dataset, record_key in keys:
                dept_name = stored.get((dataset, record_key), "")
                if not dept_name:
                    continue
                result = conn.execute(
                    update(projects)
                    .where(and_(
                        projects.c.dataset == dataset,
                        projects.c.record_key == record_key,
                        projects.c.dept_name == "",
                        repairable_exists,
                    ))
                    .values(dept_name=dept_name)
                )
                repaired += int(result.rowcount or 0)
        if len(candidates) < size:
            break

    with engine.connect() as conn:
        remaining_repairable = int(
            conn.execute(
                select(func.count())
                .select_from(projects)
                .where(projects.c.dataset.in_(selected))
                .where(projects.c.source_layer == "DETAIL_EXECUTION")
                .where(projects.c.dept_name == "")
                .where(repairable_exists)
            ).scalar_one()
            or 0
        )
    return {
        "scanned": scanned,
        "repaired": repaired,
        "remaining_repairable": remaining_repairable,
        "source_io_performed": False,
    }


def repair_current_department_names_from_revisions(
    datasets=("budget",),
    *,
    batch_size=250,
    max_batches=4,
):
    """Restore blank current department fields from the same record's revisions.

    The operation is source-free and bounded. It never infers a department from
    project text or another project: only an older normalized revision with the
    exact same dataset/record_key may supply the last known department.
    """
    selected = tuple(
        str(value or "").strip()
        for value in (datasets or ("budget",))
        if str(value or "").strip()
    )
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not selected:
        return {
            "scanned": 0,
            "repaired": 0,
            "remaining_empty": 0,
            "source_io_performed": False,
        }

    size = max(1, min(int(batch_size), 500))
    batches = max(1, min(int(max_batches), 32))
    engine, t = _engine_and_tables()
    projects = t["projects"]
    revisions = t["project_revisions"]

    scanned = 0
    repaired = 0
    last_dataset = ""
    last_record_key = ""

    repairable_revision_exists = (
        select(literal(1))
        .select_from(revisions)
        .where(
            and_(
                revisions.c.dataset == projects.c.dataset,
                revisions.c.record_key == projects.c.record_key,
                revisions.c.dept_name != "",
            )
        )
        .exists()
    )

    for _ in range(batches):
        with _write(engine) as conn:
            candidate_filters = [
                projects.c.dataset.in_(selected),
                projects.c.source_layer == "DETAIL_EXECUTION",
                projects.c.dept_name == "",
                repairable_revision_exists,
            ]
            if last_dataset or last_record_key:
                candidate_filters.append(
                    or_(
                        projects.c.dataset > last_dataset,
                        and_(
                            projects.c.dataset == last_dataset,
                            projects.c.record_key > last_record_key,
                        ),
                    )
                )
            candidates = conn.execute(
                select(
                    projects.c.dataset,
                    projects.c.record_key,
                    projects.c.dept_code,
                )
                .where(and_(*candidate_filters))
                .order_by(projects.c.dataset.asc(), projects.c.record_key.asc())
                .limit(size)
            ).mappings().all()

            if not candidates:
                break

            scanned += len(candidates)
            last_dataset = str(candidates[-1]["dataset"])
            last_record_key = str(candidates[-1]["record_key"])
            key_pairs = [
                (str(row["dataset"]), str(row["record_key"]))
                for row in candidates
            ]
            revision_rows = conn.execute(
                select(
                    revisions.c.dataset,
                    revisions.c.record_key,
                    revisions.c.dept_code,
                    revisions.c.dept_name,
                    revisions.c.observed_at,
                )
                .where(
                    tuple_(
                        revisions.c.dataset,
                        revisions.c.record_key,
                    ).in_(key_pairs)
                )
                .where(revisions.c.dept_name != "")
                .order_by(
                    revisions.c.dataset.asc(),
                    revisions.c.record_key.asc(),
                    revisions.c.observed_at.desc(),
                )
            ).mappings().all()

            latest = {}
            for row in revision_rows:
                key = (str(row["dataset"]), str(row["record_key"]))
                latest.setdefault(key, row)

            current_codes = {
                (str(row["dataset"]), str(row["record_key"])): str(
                    row.get("dept_code") or ""
                ).strip()
                for row in candidates
            }
            for key, revision in latest.items():
                dept_name = str(revision.get("dept_name") or "").strip()
                if not dept_name:
                    continue
                values = {"dept_name": dept_name}
                if (
                    not current_codes.get(key)
                    and str(revision.get("dept_code") or "").strip()
                ):
                    values["dept_code"] = str(revision["dept_code"]).strip()
                result = conn.execute(
                    update(projects)
                    .where(
                        and_(
                            projects.c.dataset == key[0],
                            projects.c.record_key == key[1],
                            projects.c.dept_name == "",
                        )
                    )
                    .values(**values)
                )
                repaired += int(result.rowcount or 0)

        if len(candidates) < size:
            break

    with engine.connect() as conn:
        remaining_empty = int(
            conn.execute(
                select(func.count())
                .select_from(projects)
                .where(projects.c.dataset.in_(selected))
                .where(projects.c.source_layer == "DETAIL_EXECUTION")
                .where(projects.c.dept_name == "")
            ).scalar_one()
            or 0
        )
        remaining_repairable = int(
            conn.execute(
                select(func.count())
                .select_from(projects)
                .where(projects.c.dataset.in_(selected))
                .where(projects.c.source_layer == "DETAIL_EXECUTION")
                .where(projects.c.dept_name == "")
                .where(repairable_revision_exists)
            ).scalar_one()
            or 0
        )

    return {
        "scanned": scanned,
        "repaired": repaired,
        "remaining_empty": remaining_empty,
        "remaining_repairable": remaining_repairable,
        "source_io_performed": False,
    }



def repair_current_department_names_from_code_evidence(
    datasets=("budget",),
    *,
    batch_size=250,
    max_batches=4,
):
    """Resolve blank department names from unambiguous stored code evidence.

    Evidence may come from another normalized current/revision row only when the
    organization identity and department code are identical. A code that maps to
    more than one non-empty name is treated as ambiguous and is never applied.
    No external source is queried and project text is never used for inference.
    """
    selected = tuple(
        str(value or "").strip()
        for value in (datasets or ("budget",))
        if str(value or "").strip()
    )
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not selected:
        return {
            "scanned": 0,
            "repaired": 0,
            "ambiguous": 0,
            "remaining_code_only": 0,
            "source_io_performed": False,
        }

    size = max(1, min(int(batch_size), 500))
    batches = max(1, min(int(max_batches), 32))
    engine, t = _engine_and_tables()
    projects = t["projects"]
    revisions = t["project_revisions"]
    current_evidence = projects.alias("dept_current_evidence")
    revision_evidence = revisions.alias("dept_revision_evidence")

    def evidence_matches(evidence):
        return and_(
            evidence.c.dataset == projects.c.dataset,
            evidence.c.dept_code == projects.c.dept_code,
            evidence.c.dept_name != "",
            or_(
                and_(
                    projects.c.org_code != "",
                    evidence.c.org_code == projects.c.org_code,
                ),
                and_(
                    projects.c.org_code == "",
                    projects.c.org_name != "",
                    evidence.c.org_name == projects.c.org_name,
                ),
            ),
        )

    repairable_evidence_exists = or_(
        select(literal(1))
        .select_from(current_evidence)
        .where(evidence_matches(current_evidence))
        .exists(),
        select(literal(1))
        .select_from(revision_evidence)
        .where(evidence_matches(revision_evidence))
        .exists(),
    )

    scanned = 0
    repaired = 0
    ambiguous = 0
    last_dataset = ""
    last_record_key = ""

    for _ in range(batches):
        with _write(engine) as conn:
            filters = [
                projects.c.dataset.in_(selected),
                projects.c.source_layer == "DETAIL_EXECUTION",
                projects.c.dept_name == "",
                projects.c.dept_code != "",
                repairable_evidence_exists,
            ]
            if last_dataset or last_record_key:
                filters.append(
                    or_(
                        projects.c.dataset > last_dataset,
                        and_(
                            projects.c.dataset == last_dataset,
                            projects.c.record_key > last_record_key,
                        ),
                    )
                )

            candidates = conn.execute(
                select(
                    projects.c.dataset,
                    projects.c.record_key,
                    projects.c.org_code,
                    projects.c.org_name,
                    projects.c.dept_code,
                )
                .where(and_(*filters))
                .order_by(projects.c.dataset.asc(), projects.c.record_key.asc())
                .limit(size)
            ).mappings().all()
            if not candidates:
                break

            scanned += len(candidates)
            last_dataset = str(candidates[-1]["dataset"])
            last_record_key = str(candidates[-1]["record_key"])

            code_pairs = sorted({
                (
                    str(row.get("dataset") or ""),
                    str(row.get("org_code") or "").strip(),
                    str(row.get("dept_code") or "").strip(),
                )
                for row in candidates
                if str(row.get("org_code") or "").strip()
            })
            name_pairs = sorted({
                (
                    str(row.get("dataset") or ""),
                    str(row.get("org_name") or "").strip(),
                    str(row.get("dept_code") or "").strip(),
                )
                for row in candidates
                if (
                    not str(row.get("org_code") or "").strip()
                    and str(row.get("org_name") or "").strip()
                )
            })

            def evidence_filter(table):
                checks = []
                if code_pairs:
                    checks.append(
                        tuple_(
                            table.c.dataset,
                            table.c.org_code,
                            table.c.dept_code,
                        ).in_(code_pairs)
                    )
                if name_pairs:
                    checks.append(
                        tuple_(
                            table.c.dataset,
                            table.c.org_name,
                            table.c.dept_code,
                        ).in_(name_pairs)
                    )
                return and_(
                    table.c.dept_name != "",
                    or_(*checks),
                )

            evidence_rows = []
            if code_pairs or name_pairs:
                evidence_rows.extend(
                    conn.execute(
                        select(
                            current_evidence.c.dataset,
                            current_evidence.c.org_code,
                            current_evidence.c.org_name,
                            current_evidence.c.dept_code,
                            current_evidence.c.dept_name,
                        ).where(evidence_filter(current_evidence))
                    ).mappings().all()
                )
                evidence_rows.extend(
                    conn.execute(
                        select(
                            revision_evidence.c.dataset,
                            revision_evidence.c.org_code,
                            revision_evidence.c.org_name,
                            revision_evidence.c.dept_code,
                            revision_evidence.c.dept_name,
                        ).where(evidence_filter(revision_evidence))
                    ).mappings().all()
                )

            names_by_identity = {}
            for row in evidence_rows:
                dataset = str(row.get("dataset") or "")
                org_code = str(row.get("org_code") or "").strip()
                org_name = str(row.get("org_name") or "").strip()
                dept_code = str(row.get("dept_code") or "").strip()
                dept_name = str(row.get("dept_name") or "").strip()
                if not dept_code or not dept_name:
                    continue
                org_key = ("CODE", org_code) if org_code else ("NAME", org_name)
                key = (dataset, org_key, dept_code)
                names_by_identity.setdefault(key, set()).add(dept_name)

            for row in candidates:
                dataset = str(row.get("dataset") or "")
                record_key = str(row.get("record_key") or "")
                org_code = str(row.get("org_code") or "").strip()
                org_name = str(row.get("org_name") or "").strip()
                dept_code = str(row.get("dept_code") or "").strip()
                org_key = ("CODE", org_code) if org_code else ("NAME", org_name)
                names = names_by_identity.get(
                    (dataset, org_key, dept_code),
                    set(),
                )
                if len(names) != 1:
                    if len(names) > 1:
                        ambiguous += 1
                    continue
                dept_name = next(iter(names))
                result = conn.execute(
                    update(projects)
                    .where(
                        and_(
                            projects.c.dataset == dataset,
                            projects.c.record_key == record_key,
                            projects.c.dept_name == "",
                            projects.c.dept_code == dept_code,
                        )
                    )
                    .values(dept_name=dept_name)
                )
                repaired += int(result.rowcount or 0)

        if len(candidates) < size:
            break

    with engine.connect() as conn:
        remaining_code_only = int(
            conn.execute(
                select(func.count())
                .select_from(projects)
                .where(projects.c.dataset.in_(selected))
                .where(projects.c.source_layer == "DETAIL_EXECUTION")
                .where(projects.c.dept_name == "")
                .where(projects.c.dept_code != "")
            ).scalar_one()
            or 0
        )

    return {
        "scanned": scanned,
        "repaired": repaired,
        "ambiguous": ambiguous,
        "remaining_code_only": remaining_code_only,
        "source_io_performed": False,
    }


def current_project_rows(
    datasets=None,
    *,
    fiscal_year=None,
    source_layers=None,
    region_terms=None,
    categories=None,
    classifier_version="",
    organization_exact_names=None,
    organization_contains_terms=None,
    department_exact_names=None,
    query="",
    execution_status="",
    remaining_positive=False,
    sort_order="RECENT",
    limit=None,
    offset=0,
):
    """Return canonical normalized current project rows with bounded SQL filters.

    Category filtering is applied in PostgreSQL against the exact-current
    classification row before LIMIT/OFFSET. This prevents a dense early
    institution (for example one county) from consuming the bounded screen slice
    before lighting/pole rows from later institutions are even considered.
    """
    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    classifications = t["classifications"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")

    selected_categories = tuple(
        sorted({
            str(value or "").strip().upper()
            for value in (categories or ())
            if str(value or "").strip()
        })
    )
    version = str(classifier_version or "").strip()
    if selected_categories and not version:
        raise ValueError("BUDGET_CLASSIFIER_VERSION_REQUIRED")

    base = state.join(
        projects,
        and_(
            state.c.dataset == projects.c.dataset,
            state.c.record_key == projects.c.record_key,
        ),
    )
    columns = [
        projects,
        state.c.source_date.label("source_date"),
        state.c.last_seen_at.label("last_seen_at"),
    ]
    if selected_categories:
        base = base.join(
            classifications,
            and_(
                classifications.c.dataset == projects.c.dataset,
                classifications.c.record_key == projects.c.record_key,
                classifications.c.classifier_version == version,
                classifications.c.source_payload_sha256 == projects.c.payload_sha256,
            ),
        )
        columns.extend([
            classifications.c.primary_category.label("primary_category"),
            classifications.c.subcategory.label("subcategory"),
            classifications.c.confidence.label("classification_confidence"),
            classifications.c.reason.label("classification_reason"),
        ])

    stmt = (
        select(*columns)
        .select_from(base)
        .where(state.c.dataset.in_(selected))
    )
    if fiscal_year is not None:
        stmt = stmt.where(projects.c.fiscal_year == int(fiscal_year))

    layers = [
        str(value or "").strip()
        for value in (source_layers or ())
        if str(value or "").strip()
    ]
    if layers:
        stmt = stmt.where(projects.c.source_layer.in_(layers))

    terms = [
        str(value or "").strip()
        for value in (region_terms or ())
        if str(value or "").strip()
    ]
    if terms:
        checks = []
        for value in terms:
            checks.extend([
                projects.c.region_name.startswith(value),
                projects.c.org_name.startswith(value),
                projects.c.institution_name.startswith(value),
            ])
        stmt = stmt.where(or_(*checks))

    exact_names = [
        str(value or "").strip()
        for value in (organization_exact_names or ())
        if str(value or "").strip()
    ]
    contains_terms = [
        str(value or "").strip()
        for value in (organization_contains_terms or ())
        if str(value or "").strip()
    ]
    if exact_names or contains_terms:
        organization_checks = []
        for value in exact_names:
            organization_checks.extend([
                projects.c.org_name == value,
                projects.c.institution_name == value,
                projects.c.dept_name == value,
            ])
        for value in contains_terms:
            pattern = f"%{value}%"
            organization_checks.extend([
                projects.c.org_name.ilike(pattern),
                projects.c.institution_name.ilike(pattern),
                projects.c.dept_name.ilike(pattern),
            ])
        stmt = stmt.where(or_(*organization_checks))

    department_names = [
        str(value or "").strip()
        for value in (department_exact_names or ())
        if str(value or "").strip()
    ]
    if department_names:
        stmt = stmt.where(projects.c.dept_name.in_(department_names))

    search_terms = [
        value
        for value in str(query or "").strip().split()
        if value
    ]
    for search in search_terms:
        pattern = f"%{search}%"
        stmt = stmt.where(or_(
            projects.c.project_name.ilike(pattern),
            projects.c.org_name.ilike(pattern),
            projects.c.dept_name.ilike(pattern),
            projects.c.institution_name.ilike(pattern),
            projects.c.field_name.ilike(pattern),
            projects.c.section_name.ilike(pattern),
            projects.c.account_name.ilike(pattern),
        ))

    status = str(execution_status or "").strip().upper()
    if status == "UNEXECUTED":
        stmt = stmt.where(projects.c.executed_amount <= 0)
    elif status == "PARTIAL":
        stmt = stmt.where(and_(
            projects.c.executed_amount > 0,
            projects.c.remaining_amount > 0,
        ))
    elif status == "FULL":
        stmt = stmt.where(and_(
            projects.c.executed_amount > 0,
            projects.c.remaining_amount <= 0,
        ))
    elif status:
        raise ValueError("INVALID_BUDGET_EXECUTION_STATUS")

    if bool(remaining_positive):
        stmt = stmt.where(projects.c.remaining_amount > 0)

    if selected_categories:
        stmt = stmt.where(
            classifications.c.primary_category.in_(selected_categories)
        )

    sort_key = str(sort_order or "RECENT").strip().upper()
    if sort_key == "REMAINING_DESC":
        stmt = stmt.order_by(
            projects.c.remaining_amount.desc(),
            projects.c.budget_amount.desc(),
            state.c.source_date.desc(),
            projects.c.record_key.desc(),
        )
    elif sort_key == "BUDGET_DESC":
        stmt = stmt.order_by(
            projects.c.budget_amount.desc(),
            projects.c.remaining_amount.desc(),
            state.c.source_date.desc(),
            projects.c.record_key.desc(),
        )
    elif sort_key == "ORG_ASC":
        stmt = stmt.order_by(
            projects.c.org_name.asc(),
            projects.c.dept_name.asc(),
            projects.c.project_name.asc(),
            projects.c.record_key.asc(),
        )
    elif sort_key == "RECENT":
        stmt = stmt.order_by(
            projects.c.fiscal_year.desc(),
            state.c.source_date.desc(),
            projects.c.updated_at.desc(),
            projects.c.record_key.desc(),
        )
    else:
        raise ValueError("INVALID_BUDGET_SORT_ORDER")
    if limit is not None:
        stmt = stmt.limit(max(1, min(int(limit), 5000))).offset(
            max(0, int(offset or 0))
        )
    elif int(offset or 0) > 0:
        stmt = stmt.offset(max(0, int(offset)))

    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(row) for row in rows]


def current_project_summary(
    datasets=None,
    *,
    fiscal_year=None,
    source_layers=None,
    region_terms=None,
    categories=None,
    classifier_version="",
    organization_exact_names=None,
    organization_contains_terms=None,
    department_exact_names=None,
    query="",
    execution_status="",
    remaining_positive=False,
):
    """Aggregate the full filtered current project set entirely in PostgreSQL.

    This mirrors current_project_rows filters but returns only scalar counts and
    sums, so the budget overview is accurate without materializing every filtered
    project row in the 256 MiB web process.
    """
    engine, t = _engine_and_tables()
    state, projects = t["states"], t["projects"]
    classifications = t["classifications"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")

    selected_categories = tuple(sorted({
        str(value or "").strip().upper()
        for value in (categories or ())
        if str(value or "").strip()
    }))
    version = str(classifier_version or "").strip()
    if selected_categories and not version:
        raise ValueError("BUDGET_CLASSIFIER_VERSION_REQUIRED")

    base = state.join(
        projects,
        and_(
            state.c.dataset == projects.c.dataset,
            state.c.record_key == projects.c.record_key,
        ),
    )
    classification_join = bool(version)
    if selected_categories:
        base = base.join(
            classifications,
            and_(
                classifications.c.dataset == projects.c.dataset,
                classifications.c.record_key == projects.c.record_key,
                classifications.c.classifier_version == version,
                classifications.c.source_payload_sha256
                == projects.c.payload_sha256,
            ),
        )
    elif classification_join:
        base = base.outerjoin(
            classifications,
            and_(
                classifications.c.dataset == projects.c.dataset,
                classifications.c.record_key == projects.c.record_key,
                classifications.c.classifier_version == version,
                classifications.c.source_payload_sha256
                == projects.c.payload_sha256,
            ),
        )

    filters = [state.c.dataset.in_(selected)]
    if fiscal_year is not None:
        filters.append(projects.c.fiscal_year == int(fiscal_year))

    layers = [
        str(value or "").strip()
        for value in (source_layers or ())
        if str(value or "").strip()
    ]
    if layers:
        filters.append(projects.c.source_layer.in_(layers))

    terms = [
        str(value or "").strip()
        for value in (region_terms or ())
        if str(value or "").strip()
    ]
    if terms:
        checks = []
        for value in terms:
            checks.extend([
                projects.c.region_name.startswith(value),
                projects.c.org_name.startswith(value),
                projects.c.institution_name.startswith(value),
            ])
        filters.append(or_(*checks))

    exact_names = [
        str(value or "").strip()
        for value in (organization_exact_names or ())
        if str(value or "").strip()
    ]
    contains_terms = [
        str(value or "").strip()
        for value in (organization_contains_terms or ())
        if str(value or "").strip()
    ]
    if exact_names or contains_terms:
        organization_checks = []
        for value in exact_names:
            organization_checks.extend([
                projects.c.org_name == value,
                projects.c.institution_name == value,
                projects.c.dept_name == value,
            ])
        for value in contains_terms:
            pattern = f"%{value}%"
            organization_checks.extend([
                projects.c.org_name.ilike(pattern),
                projects.c.institution_name.ilike(pattern),
                projects.c.dept_name.ilike(pattern),
            ])
        filters.append(or_(*organization_checks))

    department_names = [
        str(value or "").strip()
        for value in (department_exact_names or ())
        if str(value or "").strip()
    ]
    if department_names:
        filters.append(projects.c.dept_name.in_(department_names))

    search_terms = [
        value
        for value in str(query or "").strip().split()
        if value
    ]
    for search in search_terms:
        pattern = f"%{search}%"
        filters.append(or_(
            projects.c.project_name.ilike(pattern),
            projects.c.org_name.ilike(pattern),
            projects.c.dept_name.ilike(pattern),
            projects.c.institution_name.ilike(pattern),
            projects.c.field_name.ilike(pattern),
            projects.c.section_name.ilike(pattern),
            projects.c.account_name.ilike(pattern),
        ))

    status = str(execution_status or "").strip().upper()
    if status == "UNEXECUTED":
        filters.append(projects.c.executed_amount <= 0)
    elif status == "PARTIAL":
        filters.append(and_(
            projects.c.executed_amount > 0,
            projects.c.remaining_amount > 0,
        ))
    elif status == "FULL":
        filters.append(and_(
            projects.c.executed_amount > 0,
            projects.c.remaining_amount <= 0,
        ))
    elif status:
        raise ValueError("INVALID_BUDGET_EXECUTION_STATUS")

    if bool(remaining_positive):
        filters.append(projects.c.remaining_amount > 0)

    if selected_categories:
        filters.append(
            classifications.c.primary_category.in_(selected_categories)
        )

    if classification_join:
        classified_count = func.count(classifications.c.record_key)
        sales_filter = and_(
            classifications.c.primary_category.in_(("LIGHTING", "POLE")),
            projects.c.remaining_amount > 0,
        )
        sales_ready_count = func.count().filter(sales_filter)
        sales_ready_remaining = func.coalesce(
            func.sum(projects.c.remaining_amount).filter(sales_filter),
            0,
        )
    else:
        classified_count = literal(0)
        sales_ready_count = literal(0)
        sales_ready_remaining = literal(0)

    stmt = (
        select(
            func.count().label("project_count"),
            func.coalesce(func.sum(projects.c.budget_amount), 0).label(
                "budget_total"
            ),
            func.coalesce(func.sum(projects.c.executed_amount), 0).label(
                "executed_total"
            ),
            func.coalesce(func.sum(projects.c.remaining_amount), 0).label(
                "remaining_total"
            ),
            func.count().filter(
                projects.c.executed_amount <= 0
            ).label("unexecuted_count"),
            func.count().filter(and_(
                projects.c.executed_amount > 0,
                projects.c.remaining_amount > 0,
            )).label("partial_count"),
            func.count().filter(and_(
                projects.c.executed_amount > 0,
                projects.c.remaining_amount <= 0,
            )).label("full_count"),
            classified_count.label("classified_count"),
            sales_ready_count.label("sales_ready_count"),
            sales_ready_remaining.label("sales_ready_remaining"),
        )
        .select_from(base)
        .where(and_(*filters))
    )
    with engine.connect() as conn:
        row = conn.execute(stmt).mappings().one()
    project_count = int(row["project_count"] or 0)
    classified = int(row["classified_count"] or 0)
    classification_pending = max(0, project_count - classified)
    return {
        "project_count": project_count,
        "classified_count": classified,
        "classification_pending_count": classification_pending,
        "classification_complete": classification_pending == 0,
        "budget_total": int(row["budget_total"] or 0),
        "executed_total": int(row["executed_total"] or 0),
        "remaining_total": int(row["remaining_total"] or 0),
        "unexecuted_count": int(row["unexecuted_count"] or 0),
        "partial_count": int(row["partial_count"] or 0),
        "full_count": int(row["full_count"] or 0),
        "sales_ready_count": int(row["sales_ready_count"] or 0),
        "sales_ready_remaining": int(
            row["sales_ready_remaining"] or 0
        ),
        "scope": "FULL_FILTERED_CURRENT",
        "source_io_performed": False,
    }


def current_payload_hashes(datasets=None):
    """Return current budget identity -> payload hash without loading payload JSON."""
    engine, t = _engine_and_tables()
    state = t["states"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    stmt = select(
        state.c.dataset, state.c.record_key, state.c.payload_sha256
    ).where(state.c.dataset.in_(selected))
    result = {}
    with engine.connect() as conn:
        rows = conn.execution_options(
            stream_results=True,
            max_row_buffer=2000,
        ).execute(stmt).mappings()
        while True:
            batch = rows.fetchmany(2000)
            if not batch:
                break
            for row in batch:
                result[(str(row["dataset"]), str(row["record_key"]))] = str(
                    row["payload_sha256"] or ""
                )
    return result


def current_payload_hash(dataset, record_key):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    state = t["states"]
    with engine.connect() as conn:
        value = conn.execute(
            select(state.c.payload_sha256).where(and_(
                state.c.dataset == str(dataset),
                state.c.record_key == str(record_key),
            ))
        ).scalar_one_or_none()
    return str(value or "")


def _revision_adapter(row, *, current_sha=""):
    item = dict(row)
    dataset = str(item["dataset"])
    return {
        "id": str(item.get("observation_id") or ""),
        "dataset": dataset,
        "record_key": str(item["record_key"]),
        "source_system": str(item.get("source_system") or ""),
        "source_operation": str(item.get("source_operation") or ""),
        "source_date": str(item.get("source_date") or item.get("snapshot_date") or ""),
        "observed_at": str(item.get("observed_at") or ""),
        "payload": budget_normalizer_v41.compat_payload(dataset, item),
        "sha256": str(item.get("payload_sha256") or ""),
        "current_payload_sha256": str(current_sha or ""),
    }


def revision_rows(dataset, record_key):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    revisions, state = t["project_revisions"], t["states"]
    with engine.connect() as conn:
        current_sha = str(conn.execute(
            select(state.c.payload_sha256).where(and_(
                state.c.dataset == str(dataset),
                state.c.record_key == str(record_key),
            ))
        ).scalar_one_or_none() or "")
        rows = conn.execute(
            select(revisions).where(and_(
                revisions.c.dataset == str(dataset),
                revisions.c.record_key == str(record_key),
            )).order_by(revisions.c.observed_at, revisions.c.observation_id)
        ).mappings().all()
    return [_revision_adapter(row, current_sha=current_sha) for row in rows]


def all_revision_rows(dataset, *, source_date_prefix=""):
    """Return bounded normalized revision history; original source JSON is not stored."""
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    revisions, state = t["project_revisions"], t["states"]
    stmt = (
        select(
            revisions,
            state.c.payload_sha256.label("current_payload_sha256"),
        )
        .select_from(
            revisions.outerjoin(
                state,
                and_(
                    state.c.dataset == revisions.c.dataset,
                    state.c.record_key == revisions.c.record_key,
                ),
            )
        )
        .where(revisions.c.dataset == str(dataset))
    )
    prefix = str(source_date_prefix or "")
    if prefix:
        stmt = stmt.where(revisions.c.source_date.like(prefix + "%"))
    stmt = stmt.order_by(
        revisions.c.source_date,
        revisions.c.observed_at,
        revisions.c.observation_id,
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [
        _revision_adapter(
            row,
            current_sha=str(row.get("current_payload_sha256") or ""),
        )
        for row in rows
    ]


def revision_project_rows(
    dataset,
    *,
    start_date="",
    end_date="",
    fiscal_year=None,
    region_terms=None,
    query="",
    limit=300,
    offset=0,
):
    """Read bounded normalized revision history directly from PostgreSQL."""
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    revisions = t["project_revisions"]
    stmt = select(revisions).where(revisions.c.dataset == str(dataset))

    if str(start_date or "").strip():
        stmt = stmt.where(revisions.c.source_date >= str(start_date).strip())
    if str(end_date or "").strip():
        stmt = stmt.where(revisions.c.source_date <= str(end_date).strip())
    if fiscal_year is not None:
        stmt = stmt.where(revisions.c.fiscal_year == int(fiscal_year))

    terms = [str(value or "").strip() for value in (region_terms or ()) if str(value or "").strip()]
    if terms:
        region_checks = []
        for value in terms:
            region_checks.extend([
                revisions.c.region_name.startswith(value),
                revisions.c.org_name.startswith(value),
                revisions.c.institution_name.startswith(value),
            ])
        stmt = stmt.where(or_(*region_checks))

    search = str(query or "").strip()
    if search:
        stmt = stmt.where(or_(
            revisions.c.org_name.contains(search, autoescape=True),
            revisions.c.dept_name.contains(search, autoescape=True),
            revisions.c.institution_name.contains(search, autoescape=True),
            revisions.c.project_name.contains(search, autoescape=True),
            revisions.c.field_name.contains(search, autoescape=True),
            revisions.c.section_name.contains(search, autoescape=True),
            revisions.c.account_name.contains(search, autoescape=True),
        ))

    stmt = stmt.order_by(
        revisions.c.source_date.desc(),
        revisions.c.observed_at.desc(),
        revisions.c.observation_id.desc(),
    )
    if limit is not None:
        stmt = stmt.limit(max(1, min(int(limit), 5000))).offset(
            max(0, int(offset))
        )
    elif int(offset or 0) > 0:
        stmt = stmt.offset(max(0, int(offset)))

    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(row) for row in rows]


def save_checkpoint(dataset, scope_key="default", _conn=None, **values):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    cp = t["checkpoints"]
    defaults = {
        "cursor_value": "", "range_start": "", "range_end": "", "page_no": 0,
        "page_size": 0, "last_page_fingerprint": "", "source_total": 0,
        "fetched_count": 0, "saved_count": 0, "status": "IDLE", "last_error": "",
    }
    unknown = set(values) - set(defaults)
    if unknown:
        raise ValueError("UNKNOWN_BUDGET_CHECKPOINT_FIELD")
    defaults.update(values)
    defaults["updated_at"] = _now_iso()
    with _write(engine, _conn) as conn:
        exists = conn.execute(
            select(cp.c.dataset).where(and_(cp.c.dataset == dataset, cp.c.scope_key == scope_key))
        ).first()
        if exists:
            conn.execute(
                update(cp).where(and_(cp.c.dataset == dataset, cp.c.scope_key == scope_key))
                .values(**defaults)
            )
        else:
            conn.execute(insert(cp).values(dataset=dataset, scope_key=scope_key, **defaults))


def get_checkpoint(dataset, scope_key="default"):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    cp = t["checkpoints"]
    with engine.connect() as conn:
        row = conn.execute(
            select(cp).where(and_(cp.c.dataset == dataset, cp.c.scope_key == scope_key))
        ).mappings().first()
    return dict(row) if row else None


def save_classification(dataset, record_key, primary_category, *, classifier_version,
                        subcategory="", confidence=0.0, reason="",
                        source_payload_sha256="", _conn=None):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    table = t["classifications"]
    key = and_(
        table.c.dataset == dataset,
        table.c.record_key == record_key,
        table.c.classifier_version == classifier_version,
    )
    values = dict(
        primary_category=str(primary_category or "UNCLASSIFIED"),
        subcategory=str(subcategory or ""),
        confidence=float(confidence or 0),
        reason=str(reason or ""),
        source_payload_sha256=str(source_payload_sha256 or ""),
        classified_at=_now_iso(),
    )
    with _write(engine, _conn) as conn:
        exists = conn.execute(select(table.c.dataset).where(key)).first()
        if exists:
            conn.execute(update(table).where(key).values(**values))
        else:
            conn.execute(insert(table).values(
                dataset=dataset, record_key=record_key,
                classifier_version=classifier_version, **values
            ))


def current_state_count(dataset):
    """Return current normalized row count without materializing identities."""
    name = str(dataset)
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    state = t["states"]
    with engine.connect() as conn:
        return int(
            conn.execute(
                select(func.count()).select_from(state).where(
                    state.c.dataset == name
                )
            ).scalar_one()
            or 0
        )


def pending_classification_key_batches(
    dataset,
    classifier_version,
    *,
    batch_size=1000,
    force=False,
):
    """Yield only missing/stale classification keys in bounded keyset batches.

    The previous classifier built a Python dict of every current payload hash,
    then a set of every key, then loaded all classification rows. This SQL join
    keeps that comparison inside PostgreSQL and returns only the keys that need
    work. Each batch closes its connection before yielding so the 1+1 pool can be
    reused by payload reads and classification writes.
    """
    name = str(dataset)
    if name not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    version = str(classifier_version or "")
    size = max(1, min(int(batch_size), 2000))

    engine, t = _engine_and_tables()
    state = t["states"]
    classifications = t["classifications"]
    last_key = ""

    while True:
        join_condition = and_(
            classifications.c.dataset == state.c.dataset,
            classifications.c.record_key == state.c.record_key,
            classifications.c.classifier_version == version,
        )
        stmt = (
            select(state.c.record_key)
            .select_from(state.outerjoin(classifications, join_condition))
            .where(state.c.dataset == name)
        )
        if last_key:
            stmt = stmt.where(state.c.record_key > last_key)
        if not bool(force):
            stmt = stmt.where(or_(
                classifications.c.record_key.is_(None),
                classifications.c.source_payload_sha256
                != state.c.payload_sha256,
            ))
        stmt = stmt.order_by(state.c.record_key).limit(size)

        with engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        if not rows:
            break
        keys = [str(row["record_key"]) for row in rows]
        last_key = keys[-1]
        yield keys


def classification_rows(datasets, classifier_version):
    engine, t = _engine_and_tables()
    table = t["classifications"]
    selected = tuple(datasets)
    with engine.connect() as conn:
        rows = conn.execute(
            select(table).where(
                and_(
                    table.c.dataset.in_(selected),
                    table.c.classifier_version == str(classifier_version),
                )
            )
        ).mappings().all()
    return [dict(row) for row in rows]


def upsert_project(row):
    engine, t = _engine_and_tables()
    table = t["projects"]
    dataset = str(row["dataset"])
    record_key = str(row["record_key"])
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    values = dict(row)
    values["dataset"] = dataset
    values["record_key"] = record_key
    values["updated_at"] = str(values.get("updated_at") or _now_iso())
    with engine.begin() as conn:
        exists = conn.execute(
            select(table.c.dataset).where(
                and_(table.c.dataset == dataset, table.c.record_key == record_key)
            )
        ).first()
        if exists:
            writable = {k: v for k, v in values.items() if k not in {"dataset", "record_key"}}
            conn.execute(
                update(table).where(
                    and_(table.c.dataset == dataset, table.c.record_key == record_key)
                ).values(**writable)
            )
        else:
            conn.execute(insert(table).values(**values))


def project_rows(*, fiscal_year=None):
    engine, t = _engine_and_tables()
    table = t["projects"]
    stmt = select(table)
    if fiscal_year is not None:
        stmt = stmt.where(table.c.fiscal_year == int(fiscal_year))
    stmt = stmt.order_by(table.c.fiscal_year.desc(), table.c.remaining_amount.desc())
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings().all()]


def budget_region_partition_codes(
    fiscal_year,
    *,
    minimum_regions=17,
    maximum_regions=25,
):
    """Return a guarded wide-area QWGJK region plan from stored current rows.

    No hard-coded administrative code list is used here. The plan is derived from
    already normalized current QWGJK/AIDFA rows for the same fiscal year, and
    fallback is enabled only when a full-looking first-tier set is present. AIDFA
    can safely supply a region code that a broken nationwide QWGJK page never
    reached because both LOFIN sources use the same wide-area code dimension.
    """
    engine, t = _engine_and_tables()
    projects = t["projects"]
    minimum = max(1, int(minimum_regions))
    maximum = max(minimum, min(int(maximum_regions), 32))
    stmt = (
        select(
            projects.c.region_code,
            func.max(projects.c.region_name).label("region_name"),
        )
        .where(and_(
            projects.c.dataset.in_(("budget", "budget_appropriation")),
            projects.c.fiscal_year == int(fiscal_year),
            projects.c.region_code != "",
        ))
        .group_by(projects.c.region_code)
        .order_by(projects.c.region_code)
        .limit(maximum + 1)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    values = [str(row[0] or "").strip() for row in rows]
    region_names = {
        str(row[0] or "").strip(): str(row[1] or "").strip()
        for row in rows
        if str(row[0] or "").strip()
    }
    codes = [
        value for value in values
        if len(value) == 7 and value.isdigit()
    ]
    if len(codes) < minimum:
        return {
            "ready": False,
            "reason": "INSUFFICIENT_STORED_REGION_CODES",
            "region_codes": codes,
            "region_names": {code: region_names.get(code, "") for code in codes},
            "region_count": len(codes),
            "minimum_regions": minimum,
            "maximum_regions": maximum,
        }
    if len(codes) > maximum:
        return {
            "ready": False,
            "reason": "AMBIGUOUS_STORED_REGION_CODES",
            "region_codes": codes[:maximum],
            "region_names": {code: region_names.get(code, "") for code in codes[:maximum]},
            "region_count": len(codes),
            "minimum_regions": minimum,
            "maximum_regions": maximum,
        }
    return {
        "ready": True,
        "reason": "STORED_CURRENT_REGION_PLAN",
        "region_codes": codes,
        "region_names": {code: region_names.get(code, "") for code in codes},
        "region_count": len(codes),
        "minimum_regions": minimum,
        "maximum_regions": maximum,
    }


def mark_partition_complete_checkpoint(
    dataset,
    scope_key,
    *,
    region_count,
):
    """Mark one nationwide scope satisfied by a guarded regional fallback plan.

    Receipt/counter evidence from the failed nationwide generation is intentionally
    retained. The status is scheduling metadata, not a claim of source-wide archive
    completeness.
    """
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    checkpoints = t["checkpoints"]
    with engine.begin() as conn:
        result = conn.execute(
            update(checkpoints)
            .where(and_(
                checkpoints.c.dataset == str(dataset),
                checkpoints.c.scope_key == str(scope_key),
                checkpoints.c.status.in_((
                    "RUNNING", "FAILED", "INCOMPLETE",
                )),
            ))
            .values(
                status="PARTITION_COMPLETE",
                last_error=(
                    "REGION_PARTITION_PLAN_COMPLETE:"
                    + str(max(0, int(region_count)))
                ),
                updated_at=_now_iso(),
            )
        )
    return {
        "updated": max(0, int(result.rowcount or 0)),
        "status": "PARTITION_COMPLETE",
        "region_count": max(0, int(region_count)),
    }


def clear_collection_receipts(
    dataset,
    scope_key,
    *,
    keep_generation="",
    _conn=None,
    batch_size=500,
):
    """Delete page/item receipts for one scope in bounded batches."""
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    pages, items = t["pages"], t["items"]
    base_items = and_(items.c.dataset == dataset, items.c.scope_key == str(scope_key))
    base_pages = and_(pages.c.dataset == dataset, pages.c.scope_key == str(scope_key))
    generation = str(keep_generation or "").strip()
    if generation:
        base_items = and_(base_items, items.c.generation != generation)
        base_pages = and_(base_pages, pages.c.generation != generation)
    size = max(100, min(int(batch_size or 500), 2000))
    deleted_items = 0
    deleted_pages = 0
    with _write(engine, _conn) as conn:
        while True:
            item_keys = conn.execute(
                select(items.c.generation, items.c.source_key)
                .where(base_items)
                .order_by(items.c.generation, items.c.source_key)
                .limit(size)
            ).all()
            if not item_keys:
                break
            key_pairs = [(str(row[0]), str(row[1])) for row in item_keys]
            result = conn.execute(
                delete(items).where(and_(
                    items.c.dataset == dataset,
                    items.c.scope_key == str(scope_key),
                    tuple_(items.c.generation, items.c.source_key).in_(key_pairs),
                ))
            )
            removed = max(0, int(result.rowcount or 0))
            deleted_items += removed
            if removed == 0:
                break
        while True:
            page_keys = conn.execute(
                select(pages.c.generation, pages.c.page_no)
                .where(base_pages)
                .order_by(pages.c.generation, pages.c.page_no)
                .limit(size)
            ).all()
            if not page_keys:
                break
            key_pairs = [(str(row[0]), int(row[1])) for row in page_keys]
            result = conn.execute(
                delete(pages).where(and_(
                    pages.c.dataset == dataset,
                    pages.c.scope_key == str(scope_key),
                    tuple_(pages.c.generation, pages.c.page_no).in_(key_pairs),
                ))
            )
            removed = max(0, int(result.rowcount or 0))
            deleted_pages += removed
            if removed == 0:
                break
    return {
        "deleted_collection_items": deleted_items,
        "deleted_collection_pages": deleted_pages,
    }


def reconcile_complete_fiscal_year(dataset, scope_key, fiscal_year, *, allow_empty=False):
    """Remove current rows absent from one verified complete nationwide snapshot.

    Observation/revision history is preserved. Empty snapshots are fail-safe by
    default so a transient source-side no-data response cannot wipe current state.
    """
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    checkpoints = t["checkpoints"]
    items = t["items"]
    state = t["states"]
    projects = t["projects"]
    classifications = t["classifications"]
    year = int(fiscal_year)

    with engine.begin() as conn:
        checkpoint = conn.execute(
            select(checkpoints).where(and_(
                checkpoints.c.dataset == str(dataset),
                checkpoints.c.scope_key == str(scope_key),
            )).with_for_update()
        ).mappings().first()
        if not checkpoint or str(checkpoint.get("status") or "") != "COMPLETE":
            return {
                "reconciled": False,
                "reason": "CHECKPOINT_NOT_COMPLETE",
                "removed_current_records": 0,
            }

        try:
            meta = json.loads(str(checkpoint.get("cursor_value") or "{}"))
        except (TypeError, ValueError):
            meta = {}
        generation = str(meta.get("generation") or "") if isinstance(meta, dict) else ""
        if not generation:
            return {
                "reconciled": False,
                "reason": "GENERATION_MISSING",
                "removed_current_records": 0,
            }

        fetched = int(checkpoint.get("fetched_count") or 0)
        if fetched <= 0 and not allow_empty:
            return {
                "reconciled": False,
                "reason": "EMPTY_SNAPSHOT_FAILSAFE",
                "removed_current_records": 0,
            }

        seen_keys = select(items.c.source_key).where(and_(
            items.c.dataset == str(dataset),
            items.c.scope_key == str(scope_key),
            items.c.generation == generation,
        ))
        stale_stmt = (
            select(state.c.record_key)
            .select_from(
                state.join(
                    projects,
                    and_(
                        projects.c.dataset == state.c.dataset,
                        projects.c.record_key == state.c.record_key,
                    ),
                )
            )
            .where(and_(
                state.c.dataset == str(dataset),
                projects.c.fiscal_year == year,
            ))
        )
        if fetched > 0:
            stale_stmt = stale_stmt.where(~state.c.record_key.in_(seen_keys))
        # Keep reconciliation bounded. Large complete snapshots can leave
        # many stale current rows; materializing all keys at once recreates the
        # same memory spike we are explicitly avoiding elsewhere.
        removed_current_records = 0
        reconcile_batch_size = 400
        while True:
            stale_keys = [
                str(row[0])
                for row in conn.execute(
                    stale_stmt.limit(reconcile_batch_size)
                ).all()
            ]
            if not stale_keys:
                break

            conn.execute(delete(classifications).where(and_(
                classifications.c.dataset == str(dataset),
                classifications.c.record_key.in_(stale_keys),
            )))
            conn.execute(delete(state).where(and_(
                state.c.dataset == str(dataset),
                state.c.record_key.in_(stale_keys),
            )))
            conn.execute(delete(projects).where(and_(
                projects.c.dataset == str(dataset),
                projects.c.record_key.in_(stale_keys),
                projects.c.fiscal_year == year,
            )))
            removed_current_records += len(stale_keys)

        if not removed_current_records:
            return {
                "reconciled": True,
                "reason": "NO_STALE_CURRENT_ROWS",
                "removed_current_records": 0,
            }

        return {
            "reconciled": True,
            "reason": "COMPLETE_SNAPSHOT_RECONCILED",
            "removed_current_records": removed_current_records,
            "reconcile_batch_size": reconcile_batch_size,
        }



def supersede_older_nationwide_checkpoints(dataset, fiscal_year, complete_scope_key):
    """Mark older unresolved nationwide snapshots obsolete after a newer COMPLETE one."""
    if str(dataset) != "budget":
        return {"superseded_checkpoints": 0, "cleared_receipts": 0}
    parts = str(complete_scope_key or "").split(":")
    if len(parts) != 2:
        return {"superseded_checkpoints": 0, "cleared_receipts": 0}
    try:
        complete_day = dt.date.fromisoformat(parts[1])
    except ValueError:
        return {"superseded_checkpoints": 0, "cleared_receipts": 0}
    year = int(fiscal_year)
    if complete_day.year != year:
        return {"superseded_checkpoints": 0, "cleared_receipts": 0}

    engine, t = _engine_and_tables()
    checkpoints = t["checkpoints"]
    active_statuses = ("RUNNING", "FAILED", "INCOMPLETE")
    superseded = 0
    cleared = 0
    now = _now_iso()

    with engine.begin() as conn:
        rows = conn.execute(
            select(checkpoints.c.scope_key).where(and_(
                checkpoints.c.dataset == "budget",
                checkpoints.c.range_start == str(year),
                checkpoints.c.range_end < complete_day.isoformat(),
                ~checkpoints.c.scope_key.like("history:%"),
                checkpoints.c.status.in_(active_statuses),
            ))
        ).all()
        for row in rows:
            scope = str(row[0])
            removed = clear_collection_receipts(
                "budget", scope, _conn=conn
            )
            cleared += int(removed["deleted_collection_items"]) + int(
                removed["deleted_collection_pages"]
            )
            result = conn.execute(
                update(checkpoints).where(and_(
                    checkpoints.c.dataset == "budget",
                    checkpoints.c.scope_key == scope,
                    checkpoints.c.status.in_(active_statuses),
                )).values(
                    status="SUPERSEDED",
                    last_error=(
                        "SUPERSEDED_BY_COMPLETE_SNAPSHOT:"
                        + complete_day.isoformat()
                    ),
                    updated_at=now,
                )
            )
            superseded += max(0, int(result.rowcount or 0))
    return {
        "superseded_checkpoints": superseded,
        "cleared_receipts": cleared,
    }



def purge_history(
    retention_days=DEFAULT_RETENTION_DAYS,
    *,
    receipt_retention_days=DEFAULT_RECEIPT_RETENTION_DAYS,
    now=None,
):
    """Bound normalized history while protecting future-budget current state.

    Structured revisions follow the one-year window. Future fiscal-year projects are
    never expired merely because their collection timestamp is old. Page/item receipts
    are operational resume evidence and use a short window.
    """
    days = max(30, int(retention_days))
    receipt_days = max(1, min(int(receipt_retention_days), 30))
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=days)).isoformat()
    receipt_cutoff = (now - dt.timedelta(days=receipt_days)).isoformat()
    kst = dt.timezone(dt.timedelta(hours=9))
    source_cutoff_date = (
        now.astimezone(kst).date() - dt.timedelta(days=days)
    ).isoformat()
    batch_size = _retention_batch_size()

    engine, t = _engine_and_tables()
    obs, state = t["observations"], t["states"]
    project_revisions = t["project_revisions"]
    checkpoints = t["checkpoints"]
    classifications, projects = t["classifications"], t["projects"]
    deleted_items = 0
    deleted_pages = 0
    deleted_checkpoints = 0
    expired_current_records = 0
    deleted_observation_count = 0

    # Process one checkpoint per short transaction so retention never holds
    # locks across the entire historical checkpoint set.
    with engine.connect() as conn:
        checkpoint_keys = [
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                select(checkpoints.c.dataset, checkpoints.c.scope_key)
                .order_by(checkpoints.c.dataset, checkpoints.c.scope_key)
            ).all()
        ]

    for dataset, scope_key in checkpoint_keys:
        with engine.begin() as conn:
            checkpoint = conn.execute(
                select(checkpoints)
                .where(and_(
                    checkpoints.c.dataset == dataset,
                    checkpoints.c.scope_key == scope_key,
                ))
                .with_for_update()
            ).mappings().first()
            if not checkpoint:
                continue
            updated_at = str(checkpoint.get("updated_at") or "")
            status = str(checkpoint.get("status") or "").upper()
            active = status in {"RUNNING", "FAILED", "INCOMPLETE"}

            if active and (not updated_at or updated_at >= cutoff):
                try:
                    meta = json.loads(str(checkpoint.get("cursor_value") or "{}"))
                    generation = str(meta.get("generation") or "") if isinstance(meta, dict) else ""
                except (TypeError, ValueError):
                    generation = ""
                removed = clear_collection_receipts(
                    dataset, scope_key, keep_generation=generation, _conn=conn
                )
                deleted_items += removed["deleted_collection_items"]
                deleted_pages += removed["deleted_collection_pages"]
                continue

            daily_budget_complete = False
            scope_day = None
            if dataset == "budget" and status == "COMPLETE":
                parts = scope_key.split(":")
                try:
                    if len(parts) == 2:
                        scope_day = dt.date.fromisoformat(parts[1])
                        daily_budget_complete = str(scope_day.year) == parts[0]
                    elif len(parts) == 3 and parts[0] == "history":
                        scope_day = dt.date.fromisoformat(parts[2])
                        daily_budget_complete = str(scope_day.year) == parts[1]
                except ValueError:
                    daily_budget_complete = False
                    scope_day = None

            if daily_budget_complete and scope_day is not None and scope_day.isoformat() < source_cutoff_date:
                removed = clear_collection_receipts(dataset, scope_key, _conn=conn)
                deleted_items += removed["deleted_collection_items"]
                deleted_pages += removed["deleted_collection_pages"]
                result = conn.execute(delete(checkpoints).where(and_(
                    checkpoints.c.dataset == dataset,
                    checkpoints.c.scope_key == scope_key,
                )))
                deleted_checkpoints += max(0, int(result.rowcount or 0))
                continue

            if daily_budget_complete and updated_at and updated_at < receipt_cutoff:
                removed = clear_collection_receipts(dataset, scope_key, _conn=conn)
                deleted_items += removed["deleted_collection_items"]
                deleted_pages += removed["deleted_collection_pages"]
                continue

            expiry_cutoff = cutoff if (active or daily_budget_complete) else receipt_cutoff
            if updated_at and updated_at < expiry_cutoff:
                removed = clear_collection_receipts(dataset, scope_key, _conn=conn)
                deleted_items += removed["deleted_collection_items"]
                deleted_pages += removed["deleted_collection_pages"]
                result = conn.execute(delete(checkpoints).where(and_(
                    checkpoints.c.dataset == dataset,
                    checkpoints.c.scope_key == scope_key,
                    checkpoints.c.updated_at < expiry_cutoff,
                )))
                deleted_checkpoints += max(0, int(result.rowcount or 0))
                continue

            try:
                meta = json.loads(str(checkpoint.get("cursor_value") or "{}"))
                generation = str(meta.get("generation") or "") if isinstance(meta, dict) else ""
            except (TypeError, ValueError):
                generation = ""
            removed = clear_collection_receipts(
                dataset, scope_key, keep_generation=generation, _conn=conn
            )
            deleted_items += removed["deleted_collection_items"]
            deleted_pages += removed["deleted_collection_pages"]

    # Expired current-state deletes are keyset-batched into short transactions.
    while True:
        with engine.begin() as conn:
            stale_rows = conn.execute(
                select(state.c.dataset, state.c.record_key)
                .select_from(
                    state.outerjoin(
                        projects,
                        and_(
                            state.c.dataset == projects.c.dataset,
                            state.c.record_key == projects.c.record_key,
                        ),
                    )
                )
                .where(and_(
                    state.c.last_seen_at < cutoff,
                    or_(
                        projects.c.fiscal_year.is_(None),
                        projects.c.fiscal_year <= int(now.year),
                    ),
                ))
                .order_by(state.c.last_seen_at, state.c.dataset, state.c.record_key)
                .limit(batch_size)
            ).all()
            if not stale_rows:
                break
            stale_pairs = [(str(row[0]), str(row[1])) for row in stale_rows]
            result = conn.execute(
                delete(state).where(
                    and_(
                        state.c.last_seen_at < cutoff,
                        tuple_(state.c.dataset, state.c.record_key).in_(stale_pairs),
                    )
                )
            )
            removed = max(0, int(result.rowcount or 0))
            expired_current_records += removed
            if removed == 0:
                break

    # Lightweight derivative tables follow current-state membership.
    with engine.begin() as conn:
        active_pairs = select(state.c.dataset, state.c.record_key)
        deleted_classifications = conn.execute(
            delete(classifications).where(
                ~tuple_(classifications.c.dataset, classifications.c.record_key).in_(active_pairs)
            )
        )
        deleted_projects = conn.execute(
            delete(projects).where(
                ~tuple_(projects.c.dataset, projects.c.record_key).in_(active_pairs)
            )
        )

    # Immutable observations can be large; prune them in bounded transactions.
    while True:
        with engine.begin() as conn:
            current_ids = select(state.c.observation_id)
            stale_ids = [
                str(row[0])
                for row in conn.execute(
                    select(obs.c.id)
                    .where(and_(
                        or_(
                            and_(
                                obs.c.dataset == "budget",
                                obs.c.source_date != "",
                                obs.c.source_date < source_cutoff_date,
                            ),
                            and_(
                                or_(
                                    obs.c.dataset != "budget",
                                    obs.c.source_date == "",
                                ),
                                obs.c.observed_at < cutoff,
                            ),
                        ),
                        ~obs.c.id.in_(current_ids),
                    ))
                    .order_by(
                        obs.c.source_date,
                        obs.c.observed_at,
                        obs.c.id,
                    )
                    .limit(batch_size)
                ).all()
            ]
            if not stale_ids:
                break
            conn.execute(
                delete(project_revisions).where(
                    project_revisions.c.observation_id.in_(stale_ids)
                )
            )
            current_ids = select(state.c.observation_id)
            result = conn.execute(
                delete(obs).where(and_(
                    obs.c.observed_at < cutoff,
                    obs.c.id.in_(stale_ids),
                    ~obs.c.id.in_(current_ids),
                ))
            )
            removed = max(0, int(result.rowcount or 0))
            deleted_observation_count += removed
            if removed == 0:
                break

    return {
        "expired_current_records": expired_current_records,
        "deleted_observations": deleted_observation_count,
        "deleted_checkpoints": deleted_checkpoints,
        "deleted_collection_pages": deleted_pages,
        "deleted_collection_items": deleted_items,
        "deleted_classifications": max(0, int(deleted_classifications.rowcount or 0)),
        "deleted_projects": max(0, int(deleted_projects.rowcount or 0)),
        "retention_days": days,
        "receipt_retention_days": receipt_days,
        "cutoff": cutoff,
        "source_cutoff_date": source_cutoff_date,
        "receipt_cutoff": receipt_cutoff,
        "retention_batch_size": batch_size,
    }


def storage_status():
    if not postgres_configured():
        return {
            "configured": False,
            "backend": "POSTGRESQL",
            "schema": _safe_schema(),
            "observations": 0,
            "current_records": 0,
        }
    engine, t = _engine_and_tables()
    with engine.connect() as conn:
        observations = int(conn.execute(select(func.count()).select_from(t["observations"])).scalar_one())
        states = int(conn.execute(select(func.count()).select_from(t["states"])).scalar_one())
    return {
        "configured": True,
        "backend": "POSTGRESQL",
        "schema": _safe_schema() if not make_url(resolve_database_url()).drivername.startswith("sqlite") else "",
        "observations": observations,
        "current_records": states,
    }



def dataset_counts_all(datasets=None):
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")

    engine, t = _engine_and_tables()
    obs, state = t["observations"], t["states"]
    observations = {name: 0 for name in selected}
    currents = {name: 0 for name in selected}
    last_seen = {name: "" for name in selected}

    with engine.connect() as conn:
        for row in conn.execute(
            select(obs.c.dataset, func.count())
            .where(obs.c.dataset.in_(selected))
            .group_by(obs.c.dataset)
        ).all():
            observations[str(row[0])] = int(row[1] or 0)

        for row in conn.execute(
            select(
                state.c.dataset,
                func.count(),
                func.max(state.c.last_seen_at),
            )
            .where(state.c.dataset.in_(selected))
            .group_by(state.c.dataset)
        ).all():
            name = str(row[0])
            currents[name] = int(row[1] or 0)
            last_seen[name] = str(row[2] or "")

    return {
        name: {
            "dataset": name,
            "current_records": currents[name],
            "observations": observations[name],
            "superseded_observations": max(
                0, observations[name] - currents[name]
            ),
            "last_seen_at": last_seen[name],
        }
        for name in selected
    }


def dataset_counts(dataset):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    obs, state = t["observations"], t["states"]
    with engine.connect() as conn:
        observations = int(conn.execute(
            select(func.count()).select_from(obs).where(obs.c.dataset == dataset)
        ).scalar_one())
        current = int(conn.execute(
            select(func.count()).select_from(state).where(state.c.dataset == dataset)
        ).scalar_one())
        last_seen_at = str(conn.execute(
            select(func.max(state.c.last_seen_at)).where(state.c.dataset == dataset)
        ).scalar_one() or "")
    return {
        "dataset": dataset,
        "current_records": current,
        "observations": observations,
        "superseded_observations": max(0, observations - current),
        "last_seen_at": last_seen_at,
    }



def list_checkpoints(dataset):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    engine, t = _engine_and_tables()
    cp = t["checkpoints"]
    with engine.connect() as conn:
        rows = conn.execute(
            select(cp).where(cp.c.dataset == dataset).order_by(cp.c.scope_key)
        ).mappings().all()
    return [dict(row) for row in rows]
