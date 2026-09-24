"""Storage helpers for the additive G2B vNext foundation."""
import hashlib
import json
from contextlib import contextmanager

from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema


def ensure_foundation():
    with connect() as conn:
        ensure_vnext_schema(conn)


@contextmanager
def _write_connection(existing=None):
    # Schema setup uses executescript, so never run it inside a caller transaction.
    if existing is not None:
        yield existing
    else:
        with connect() as conn:
            ensure_vnext_schema(conn)
            yield conn


def _canonical_json(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def preserve_raw(dataset, source_key, payload, *, source_system="", source_operation="", source_date="", _conn=None):
    """Preserve immutable payload revisions while keeping a latest-row index.

    ``raw_record_revisions`` is append-only by unique payload digest. ``raw_records``
    remains the compatibility/latest snapshot keyed by ``(dataset, source_key)`` so
    existing projection and classification queries do not need to change. Re-fetching
    identical content keeps normalization state; changed content clears it.
    """
    if not dataset or not source_key:
        raise ValueError("dataset and source_key are required")
    text = _canonical_json(payload)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with _write_connection(_conn) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_record_revisions(
                dataset,source_system,source_operation,source_key,source_date,payload_json,payload_sha256
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (dataset, source_system, source_operation, source_key, source_date, text, digest),
        )
        conn.execute(
            """
            INSERT INTO raw_records(dataset,source_system,source_operation,source_key,source_date,payload_json,payload_sha256)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(dataset,source_key) DO UPDATE SET
                source_system=excluded.source_system,
                source_operation=excluded.source_operation,
                source_date=excluded.source_date,
                normalized_at=CASE
                    WHEN raw_records.payload_sha256<>excluded.payload_sha256 THEN ''
                    ELSE raw_records.normalized_at
                END,
                payload_json=excluded.payload_json,
                payload_sha256=excluded.payload_sha256,
                fetched_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            """,
            (dataset, source_system, source_operation, source_key, source_date, text, digest),
        )
    return digest


def save_classification(entity_type, entity_key, primary_category, *, subcategory="", confidence=0.0,
                        reason="", classifier_version=None, source_payload_sha256=""):
    version = classifier_version or CLASSIFIER_VERSION
    with connect() as conn:
        ensure_vnext_schema(conn)
        conn.execute(
            """
            INSERT INTO classifications(
                entity_type,entity_key,primary_category,subcategory,confidence,reason,
                classifier_version,source_payload_sha256
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_type,entity_key,classifier_version) DO UPDATE SET
                primary_category=excluded.primary_category,
                subcategory=excluded.subcategory,
                confidence=excluded.confidence,
                reason=excluded.reason,
                source_payload_sha256=excluded.source_payload_sha256,
                classified_at=CURRENT_TIMESTAMP
            """,
            (entity_type, entity_key, primary_category, subcategory, float(confidence or 0), reason,
             version, str(source_payload_sha256 or "")),
        )


def save_checkpoint(dataset, scope_key="default", _conn=None, **values):
    allowed = {
        "cursor_value", "range_start", "range_end", "page_no", "page_size",
        "last_page_fingerprint", "source_total", "fetched_count", "saved_count",
        "status", "last_error",
    }
    unknown = set(values) - allowed
    if unknown:
        raise ValueError("unknown checkpoint fields: " + ", ".join(sorted(unknown)))
    defaults = {
        "cursor_value": "", "range_start": "", "range_end": "", "page_no": 0,
        "page_size": 0, "last_page_fingerprint": "", "source_total": 0,
        "fetched_count": 0, "saved_count": 0, "status": "IDLE", "last_error": "",
    }
    defaults.update(values)
    with _write_connection(_conn) as conn:
        conn.execute(
            """
            INSERT INTO collection_checkpoints(
                dataset,scope_key,cursor_value,range_start,range_end,page_no,page_size,
                last_page_fingerprint,source_total,fetched_count,saved_count,status,last_error,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(dataset,scope_key) DO UPDATE SET
                cursor_value=excluded.cursor_value,range_start=excluded.range_start,range_end=excluded.range_end,
                page_no=excluded.page_no,page_size=excluded.page_size,
                last_page_fingerprint=excluded.last_page_fingerprint,
                source_total=excluded.source_total,fetched_count=excluded.fetched_count,
                saved_count=excluded.saved_count,status=excluded.status,last_error=excluded.last_error,
                updated_at=CURRENT_TIMESTAMP
            """,
            (dataset, scope_key, defaults["cursor_value"], defaults["range_start"], defaults["range_end"],
             int(defaults["page_no"] or 0), int(defaults["page_size"] or 0),
             str(defaults["last_page_fingerprint"] or ""), int(defaults["source_total"] or 0),
             int(defaults["fetched_count"] or 0), int(defaults["saved_count"] or 0),
             defaults["status"], defaults["last_error"]),
        )


def get_checkpoint(dataset, scope_key="default"):
    with connect() as conn:
        ensure_vnext_schema(conn)
        row = conn.execute(
            "SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?",
            (dataset, scope_key),
        ).fetchone()
        return dict(row) if row else None


def save_lifecycle_link(from_type, from_key, to_type, to_key, link_type, *, confidence=1.0, reason=""):
    """Idempotently link two normalized lifecycle identities."""
    if not all((from_type, from_key, to_type, to_key,link_type)):
        raise ValueError("lifecycle link requires non-empty types, keys, and link_type")
    with connect() as conn:
        ensure_vnext_schema(conn)
        conn.execute(
            """
            INSERT INTO lifecycle_links(from_type,from_key,to_type,to_key,link_type,confidence,reason)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(from_type,from_key,to_type,to_key,link_type) DO UPDATE SET
                confidence=excluded.confidence,
                reason=excluded.reason
            """,
            (from_type, from_key, to_type, to_key, link_type, float(confidence or 0), reason),
        )



_AWARD_DEFAULTS = {
    "notice_no": "",
    "notice_order": "",
    "business_type": "",
    "opening_date": "",
    "participant_count": 0,
    "first_rank_vendor": "",
    "first_rank_bizno": "",
    "first_rank_amount": 0,
    "final_vendor": "",
    "final_vendor_bizno": "",
    "final_award_amount": 0,
    "award_rate": 0.0,
    "contract_no": "",
    "contract_vendor": "",
    "contract_vendor_bizno": "",
    "contract_amount": 0,
}


def upsert_award_result(source_key, **values):
    """Merge non-empty lifecycle facts into one award_results row.

    RAW rows remain the source of truth. This projection is deliberately conservative:
    blank/zero values from a later source never erase already-known facts.
    """
    if not source_key:
        raise ValueError("source_key is required")
    unknown = set(values) - set(_AWARD_DEFAULTS)
    if unknown:
        raise ValueError("unknown award fields: " + ", ".join(sorted(unknown)))

    incoming = dict(_AWARD_DEFAULTS)
    incoming.update(values)
    with connect() as conn:
        ensure_vnext_schema(conn)
        existing = conn.execute(
            "SELECT * FROM award_results WHERE source_key=? ORDER BY id DESC LIMIT 1",
            (source_key,),
        ).fetchone()
        merged = dict(_AWARD_DEFAULTS)
        if existing:
            for key in merged:
                merged[key] = existing[key]
        for key, value in incoming.items():
            if value not in (None, "", 0, 0.0):
                merged[key] = value
        if existing:
            conn.execute(
                """
                UPDATE award_results SET
                    notice_no=?,notice_order=?,business_type=?,opening_date=?,participant_count=?,
                    first_rank_vendor=?,first_rank_bizno=?,first_rank_amount=?,final_vendor=?,final_vendor_bizno=?,
                    final_award_amount=?,award_rate=?,contract_no=?,contract_vendor=?,contract_vendor_bizno=?,
                    contract_amount=?,updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                tuple(merged[key] for key in _AWARD_DEFAULTS) + (existing["id"],),
            )
        else:
            conn.execute(
                """
                INSERT INTO award_results(
                    notice_no,notice_order,business_type,opening_date,participant_count,
                    first_rank_vendor,first_rank_bizno,first_rank_amount,final_vendor,final_vendor_bizno,
                    final_award_amount,award_rate,contract_no,contract_vendor,contract_vendor_bizno,
                    contract_amount,source_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                tuple(merged[key] for key in _AWARD_DEFAULTS) + (source_key,),
            )