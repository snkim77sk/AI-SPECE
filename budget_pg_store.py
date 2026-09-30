"""PostgreSQL-backed budget RAW/state foundation for G2B 4.x.

Only budget datasets use this store.  Auth/settings and the lightweight web serving
database remain independent.  Runtime connection is lazy so the HTTP process can boot
before PostgreSQL credentials are configured.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import uuid

from sqlalchemy import (
    BigInteger, Column, Float, Index, Integer, JSON, MetaData, String, Table, Text,
    UniqueConstraint, and_, create_engine, delete, func, insert, select, text, update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL, make_url

BUDGET_DATASETS = frozenset({"budget", "budget_appropriation", "education_budget"})
DEFAULT_SCHEMA = "g2b_budget"
DEFAULT_RETENTION_DAYS = 365

_ENGINE = None
_TABLES = None
_ENGINE_URL = None


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _flag(name, default=False):
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _safe_schema():
    value = str(os.getenv("G2B_BUDGET_SCHEMA", DEFAULT_SCHEMA) or DEFAULT_SCHEMA).strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value) is None:
        raise RuntimeError("G2B_BUDGET_SCHEMA_INVALID")
    return value


def _url_candidates():
    for name in ("G2B_BUDGET_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        value = str(os.getenv(name, "") or "").strip()
        if value:
            yield name, value


def resolve_database_url():
    """Return normalized SQLAlchemy URL text without logging credentials."""
    for source, raw in _url_candidates():
        try:
            url = make_url(raw)
        except Exception:
            raise RuntimeError(f"{source}_INVALID") from None
        if url.drivername in {"postgres", "postgresql", "postgresql+psycopg"}:
            return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
        if _flag("G2B_TEST_MODE") and url.drivername in {"sqlite", "sqlite+pysqlite"}:
            return url.render_as_string(hide_password=False)
        raise RuntimeError(f"{source}_POSTGRESQL_REQUIRED")
    return ""


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
        Column("payload", payload_type, nullable=False),
        Column("quality", String(20), nullable=False, default="RAW"),
        Column("issues", payload_type, nullable=False, default=list),
        Column("observed_at", String(40), nullable=False),
        UniqueConstraint("dataset", "record_key", "sha256", name="uq_budget_observation_payload"),
    )
    Index("ix_budget_observation_dataset_date", observations.c.dataset, observations.c.source_date)
    Index("ix_budget_observation_record", observations.c.dataset, observations.c.record_key)

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

    return {
        "metadata": metadata,
        "observations": observations,
        "states": states,
        "checkpoints": checkpoints,
        "pages": pages,
        "items": items,
        "classifications": classifications,
        "projects": projects,
    }


def _engine_and_tables():
    global _ENGINE, _TABLES, _ENGINE_URL
    url_text = resolve_database_url()
    if not url_text:
        raise RuntimeError("BUDGET_POSTGRES_NOT_CONFIGURED")
    if _ENGINE is not None and _ENGINE_URL == url_text:
        return _ENGINE, _TABLES

    url = make_url(url_text)
    schema = None if url.drivername.startswith("sqlite") else _safe_schema()
    engine = create_engine(url_text, pool_pre_ping=True, future=True)
    if schema:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    tables = _build_tables(schema)
    tables["metadata"].create_all(engine)
    _ENGINE, _TABLES, _ENGINE_URL = engine, tables, url_text
    return engine, tables


def reset_engine_cache():
    """Tests/config reload only; does not drop data."""
    global _ENGINE, _TABLES, _ENGINE_URL
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = _TABLES = _ENGINE_URL = None


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


def preserve_observation(dataset, record_key, payload, *, source_system="", source_operation="",
                         source_date="", quality="RAW", issues=None, _conn=None):
    if dataset not in BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    if not record_key:
        raise ValueError("BUDGET_RECORD_KEY_REQUIRED")
    if not isinstance(payload, dict):
        raise ValueError("BUDGET_PAYLOAD_OBJECT_REQUIRED")

    digest = observation_digest(dataset, payload)
    engine, t = _engine_and_tables()
    now = _now_iso()
    obs = t["observations"]
    state = t["states"]

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
                payload=payload,
                quality=str(quality or "RAW"),
                issues=list(issues or []),
                observed_at=now,
            ))

        current = conn.execute(
            select(state.c.observation_id).where(
                and_(state.c.dataset == dataset, state.c.record_key == record_key)
            )
        ).first()
        values = dict(
            observation_id=observation_id,
            payload_sha256=digest,
            source_date=str(source_date or ""),
            last_seen_at=now,
        )
        if current:
            conn.execute(
                update(state).where(
                    and_(state.c.dataset == dataset, state.c.record_key == record_key)
                ).values(**values)
            )
        else:
            conn.execute(insert(state).values(dataset=dataset, record_key=record_key, **values))

    return {
        "sha256": digest,
        "observation_id": observation_id,
        "new_observation": existing is None,
    }


def current_rows(datasets=None):
    engine, t = _engine_and_tables()
    obs, state = t["observations"], t["states"]
    selected = tuple(datasets or BUDGET_DATASETS)
    unknown = set(selected) - BUDGET_DATASETS
    if unknown:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    stmt = (
        select(
            state.c.dataset,
            state.c.record_key,
            state.c.source_date,
            state.c.last_seen_at,
            state.c.payload_sha256,
            obs.c.source_system,
            obs.c.source_operation,
            obs.c.observed_at,
            obs.c.payload,
            obs.c.quality,
            obs.c.issues,
        )
        .select_from(state.join(obs, state.c.observation_id == obs.c.id))
        .where(state.c.dataset.in_(selected))
        .order_by(state.c.dataset, state.c.record_key)
    )
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings().all()]


def revision_rows(dataset, record_key):
    engine, t = _engine_and_tables()
    obs = t["observations"]
    stmt = select(obs).where(
        and_(obs.c.dataset == dataset, obs.c.record_key == record_key)
    ).order_by(obs.c.observed_at, obs.c.id)
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(stmt).mappings().all()]


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
                        subcategory="", confidence=0.0, reason="", source_payload_sha256=""):
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
    with engine.begin() as conn:
        exists = conn.execute(select(table.c.dataset).where(key)).first()
        if exists:
            conn.execute(update(table).where(key).values(**values))
        else:
            conn.execute(insert(table).values(
                dataset=dataset, record_key=record_key,
                classifier_version=classifier_version, **values
            ))


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


def purge_history(retention_days=DEFAULT_RETENTION_DAYS, *, now=None):
    """Delete old superseded observations only; current state is never deleted."""
    days = max(30, int(retention_days))
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=days)).isoformat()
    engine, t = _engine_and_tables()
    obs, state = t["observations"], t["states"]
    with engine.begin() as conn:
        current_ids = select(state.c.observation_id)
        result = conn.execute(
            delete(obs).where(
                and_(obs.c.observed_at < cutoff, ~obs.c.id.in_(current_ids))
            )
        )
        return int(result.rowcount or 0)


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
    return {
        "dataset": dataset,
        "current_records": current,
        "observations": observations,
        "superseded_observations": max(0, observations - current),
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
