"""Storage helpers for the additive G2B vNext foundation."""
import hashlib
import json

from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema


def ensure_foundation():
    with connect() as conn:
        ensure_vnext_schema(conn)


def _canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def preserve_raw(dataset, source_key, payload, *, source_system="", source_operation="", source_date=""):
    """Idempotently preserve a source row before any business classification."""
    if not dataset or not source_key:
        raise ValueError("dataset and source_key are required")
    text = _canonical_json(payload)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with connect() as conn:
        ensure_vnext_schema(conn)
        conn.execute(
            """
            INSERT INTO raw_records(dataset,source_system,source_operation,source_key,source_date,payload_json,payload_sha256)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(dataset,source_key) DO UPDATE SET
                source_system=excluded.source_system,
                source_operation=excluded.source_operation,
                source_date=excluded.source_date,
                payload_json=excluded.payload_json,
                payload_sha256=excluded.payload_sha256,
                fetched_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            """,
            (dataset, source_system, source_operation, source_key, source_date, text, digest),
        )
    return digest


def save_classification(entity_type, entity_key, primary_category, *, subcategory="", confidence=0.0, reason="", classifier_version=None):
    version = classifier_version or CLASSIFIER_VERSION
    with connect() as conn:
        ensure_vnext_schema(conn)
        conn.execute(
            """
            INSERT INTO classifications(entity_type,entity_key,primary_category,subcategory,confidence,reason,classifier_version)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(entity_type,entity_key,classifier_version) DO UPDATE SET
                primary_category=excluded.primary_category,
                subcategory=excluded.subcategory,
                confidence=excluded.confidence,
                reason=excluded.reason,
                classified_at=CURRENT_TIMESTAMP
            """,
            (entity_type, entity_key, primary_category, subcategory, float(confidence or 0), reason, version),
        )


def save_checkpoint(dataset, scope_key="default", **values):
    allowed = {
        "cursor_value", "range_start", "range_end", "page_no", "source_total",
        "fetched_count", "saved_count", "status", "last_error",
    }
    unknown = set(values) - allowed
    if unknown:
        raise ValueError("unknown checkpoint fields: " + ", ".join(sorted(unknown)))
    defaults = {
        "cursor_value": "", "range_start": "", "range_end": "", "page_no": 0,
        "source_total": 0, "fetched_count": 0, "saved_count": 0,
        "status": "IDLE", "last_error": "",
    }
    defaults.update(values)
    with connect() as conn:
        ensure_vnext_schema(conn)
        conn.execute(
            """
            INSERT INTO collection_checkpoints(
                dataset,scope_key,cursor_value,range_start,range_end,page_no,source_total,
                fetched_count,saved_count,status,last_error,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(dataset,scope_key) DO UPDATE SET
                cursor_value=excluded.cursor_value,range_start=excluded.range_start,range_end=excluded.range_end,
                page_no=excluded.page_no,source_total=excluded.source_total,fetched_count=excluded.fetched_count,
                saved_count=excluded.saved_count,status=excluded.status,last_error=excluded.last_error,
                updated_at=CURRENT_TIMESTAMP
            """,
            (dataset, scope_key, defaults["cursor_value"], defaults["range_start"], defaults["range_end"],
             int(defaults["page_no"] or 0), int(defaults["source_total"] or 0), int(defaults["fetched_count"] or 0),
             int(defaults["saved_count"] or 0), defaults["status"], defaults["last_error"]),
        )


def get_checkpoint(dataset, scope_key="default"):
    with connect() as conn:
        ensure_vnext_schema(conn)
        row = conn.execute(
            "SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?",
            (dataset, scope_key),
        ).fetchone()
        return dict(row) if row else None
