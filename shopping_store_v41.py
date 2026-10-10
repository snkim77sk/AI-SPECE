"""Normalized shopping/business storage for G2B 4.1.

Production persists only fields used by the G2B read model plus a source payload
hash. The original source JSON is intentionally not stored.
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import os
from contextlib import contextmanager

from db import connect
import admin_geography_v41
import shopping_scope_v4

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS shopping_records(
    source_key TEXT PRIMARY KEY,
    source_system TEXT NOT NULL DEFAULT '',
    source_operation TEXT NOT NULL DEFAULT '',
    source_date TEXT NOT NULL DEFAULT '',
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload_sha256 TEXT NOT NULL DEFAULT '',
    primary_category TEXT NOT NULL DEFAULT '',
    subcategory TEXT NOT NULL DEFAULT '',
    classification_confidence REAL NOT NULL DEFAULT 0,
    classification_reason TEXT NOT NULL DEFAULT '',
    delivery_req_no TEXT NOT NULL DEFAULT '',
    detail_seq TEXT NOT NULL DEFAULT '',
    delivery_req_name TEXT NOT NULL DEFAULT '',
    delivery_change_order TEXT NOT NULL DEFAULT '',
    is_final_delivery_request TEXT NOT NULL DEFAULT '',
    detail_item_no TEXT NOT NULL DEFAULT '',
    detail_item_name TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    item_name TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    demand_org TEXT NOT NULL DEFAULT '',
    demand_region TEXT NOT NULL DEFAULT '',
    vendor_name TEXT NOT NULL DEFAULT '',
    vendor_bizno TEXT NOT NULL DEFAULT '',
    contract_no TEXT NOT NULL DEFAULT '',
    quantity REAL NOT NULL DEFAULT 0,
    unit_price INTEGER NOT NULL DEFAULT 0,
    amount INTEGER NOT NULL DEFAULT 0,
    delivery_req_total_amount INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    inactive_reason TEXT NOT NULL DEFAULT '',
    inactive_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_shopping_records_date
    ON shopping_records(source_date);
CREATE INDEX IF NOT EXISTS ix_shopping_records_category
    ON shopping_records(primary_category,source_date);
CREATE INDEX IF NOT EXISTS ix_shopping_records_region
    ON shopping_records(demand_region,source_date);
CREATE INDEX IF NOT EXISTS ix_shopping_records_org
    ON shopping_records(demand_org,source_date);
CREATE INDEX IF NOT EXISTS ix_shopping_records_vendor
    ON shopping_records(vendor_name,source_date);
"""

DEFAULT_RETENTION_DAYS = 365
MAX_RETENTION_DAYS = 365
MIN_RETENTION_DAYS = 30
DEFAULT_RETENTION_MONTHS = 27
MAX_RETENTION_MONTHS = 27
MIN_RETENTION_MONTHS = 1
DEFAULT_RETENTION_BATCH_SIZE = 2000
MIN_RETENTION_BATCH_SIZE = 100
MAX_RETENTION_BATCH_SIZE = 10000


REGIONS = admin_geography_v41.REGIONS


def _test_mode():
    return str(os.getenv("G2B_TEST_MODE", "0") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _canonical(payload):
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _pick(row, *names):
    if not isinstance(row, dict):
        return ""
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _number(value):
    try:
        text = str(value or "").replace(",", "").replace("원", "").strip()
        return int(round(float(text))) if text else 0
    except (TypeError, ValueError):
        return 0


def _float(value):
    try:
        text = str(value or "").replace(",", "").strip()
        return float(text) if text else 0.0
    except (TypeError, ValueError):
        return 0.0


def _validated_source_date(value, *, match_backfill=False):
    text = str(value or "").strip()
    try:
        parsed = dt.date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError("SHOPPING_SOURCE_DATE_ISO_REQUIRED") from None
    if text != parsed.isoformat():
        raise ValueError("SHOPPING_SOURCE_DATE_ISO_REQUIRED")
    if parsed < shopping_scope_v4.START_DATE:
        if not bool(match_backfill):
            raise ValueError("SHOPPING_SOURCE_DATE_BEFORE_BOOTSTRAP")
        shopping_scope_v4.validate_match_backfill_date(parsed)
    return text


def _canonical_region(value):
    return admin_geography_v41.canonical_region(value)

def _region_name(payload, demand_org):
    explicit = _pick(
        payload, "dminsttRgnNm", "demandRegion", "demandRegionName",
        "regionName", "areaNm", "sidoNm",
    )
    canonical = _canonical_region(explicit)
    if canonical:
        return canonical
    return _canonical_region(demand_org)


def _normalize(payload):
    row = payload if isinstance(payload, dict) else {}
    demand_org = _pick(
        row, "dminsttNm", "demandInsttNm", "demandOrgNm", "demandOrgName",
        "orderInsttNm", "insttNm",
    )
    quantity = _float(_pick(row, "prdctQty", "dlvrReqQty", "reqQty", "quantity", "qty"))
    unit_price = _number(
        _pick(row, "prdctUprc", "unitPric", "unitPrice", "cntrctUnitPric", "cntrctPrce", "prc")
    )
    source_amount = _number(
        _pick(row, "prdctAmt", "supplyAmount", "amount", "dlvrReqDtlAmt")
    )
    calculated = int(round(unit_price * quantity)) if unit_price and quantity else 0
    return {
        "delivery_req_no": _pick(row, "dlvrReqNo", "deliveryReqNo", "reqNo"),
        "detail_seq": _pick(
            row, "prdctSno", "dlvrReqDtlSeq", "dlvrReqDtlSn", "detailSeq", "seq"
        ),
        "delivery_req_name": _pick(row, "dlvrReqNm", "deliveryReqNm", "dlvrReqSj"),
        "delivery_change_order": _pick(row, "dlvrReqChgOrd", "deliveryReqChangeOrder"),
        "is_final_delivery_request": _pick(row, "fnlDlvrReqYn", "finalDeliveryReqYn"),
        "detail_item_no": _pick(
            row, "dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo", "dtlPrdctClsfcNo"
        ),
        "detail_item_name": _pick(
            row, "dtilPrdctClsfcNoNm", "dtilPrdctClsfcNm", "detailPrdctNm", "detailItemName"
        ),
        "item_id": _pick(row, "prdctIdntNo", "itemId", "productId"),
        "item_name": _pick(
            row, "prdctIdntNoNm", "prdctIdntNm", "prdctNm", "itemName"
        ),
        "model_name": _pick(row, "modelNm", "modelName", "prdctSpecNm", "specNm"),
        "demand_org": demand_org,
        "demand_region": _region_name(row, demand_org),
        "vendor_name": _pick(
            row, "corpNm", "cntrctCorpNm", "entrpsNm", "vendorNm", "vendorName",
            "supplierNm", "supplierName", "cntrctCorpName",
        ),
        "vendor_bizno": _pick(
            row, "cntrctCorpBizno", "corpBizno", "vendorBizno", "bizno", "bizrno"
        ),
        "contract_no": _pick(row, "cntrctNo", "contractNo"),
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": source_amount or calculated,
        "delivery_req_total_amount": _number(_pick(row, "dlvrReqAmt", "reqAmt")),
    }


def ensure_schema():
    with connect() as conn:
        conn.executescript(SCHEMA)
        columns = {
            str(row["name"])
            for row in conn.execute(
                "PRAGMA table_info(shopping_records)"
            ).fetchall()
        }
        if "is_active" not in columns:
            conn.execute(
                "ALTER TABLE shopping_records "
                "ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
        if "inactive_reason" not in columns:
            conn.execute(
                "ALTER TABLE shopping_records "
                "ADD COLUMN inactive_reason TEXT NOT NULL DEFAULT ''"
            )
        if "inactive_at" not in columns:
            conn.execute(
                "ALTER TABLE shopping_records "
                "ADD COLUMN inactive_at TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_shopping_records_active_date "
            "ON shopping_records(is_active,source_date)"
        )
        # Very old compatibility fixtures may predate normalized request columns.
        # Re-read the migrated shape and only install request-read indexes when all
        # columns needed by those indexes are present. Current 4.1 production
        # shopping_records always satisfies this contract.
        migrated_columns = {
            str(row["name"])
            for row in conn.execute(
                "PRAGMA table_info(shopping_records)"
            ).fetchall()
        }
        request_index_columns = {
            "delivery_req_no",
            "is_active",
            "primary_category",
            "source_date",
        }
        if request_index_columns <= migrated_columns:
            # Request-level historical matching first selects request keys from an
            # active category/date window, then re-reads every target detail for
            # selected delivery_req_no values. Keep one index per access shape.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "ix_shopping_records_active_category_date_request "
                "ON shopping_records("
                "is_active,primary_category,source_date,delivery_req_no)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "ix_shopping_records_request_active_category_date "
                "ON shopping_records("
                "delivery_req_no,is_active,primary_category,source_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "ix_shopping_records_request_latest "
                "ON shopping_records("
                "delivery_req_no,source_date DESC,updated_at DESC,source_key DESC) "
                "WHERE is_active=1 "
                "AND primary_category IN ('LIGHTING','POLE') "
                "AND delivery_req_no<>''"
            )


@contextmanager
def _write(existing=None):
    if existing is not None:
        yield existing
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def preserve_record(dataset, source_key, payload, *, source_system="",
                    source_operation="", source_date="", _conn=None,
                    match_backfill=False):
    if str(dataset) != "shopping_delivery":
        raise ValueError("UNSUPPORTED_SHOPPING_DATASET")
    if not isinstance(payload, dict):
        raise ValueError("SHOPPING_PAYLOAD_OBJECT_REQUIRED")
    if not shopping_scope_v4.should_store(payload):
        raise ValueError("SHOPPING_RECORD_OUTSIDE_TARGET_SCOPE")
    key = str(source_key or "").strip()
    if not key:
        raise ValueError("SHOPPING_SOURCE_KEY_REQUIRED")
    source_date = _validated_source_date(
        source_date,
        match_backfill=bool(match_backfill),
    )

    # Classification is deterministic and operates on the transient response only.
    import classification_vnext
    classification = classification_vnext.classify_payload(dataset, payload)
    category = shopping_scope_v4.target_group(payload) or str(
        classification.get("primary_category") or ""
    )
    normalized = _normalize(payload)
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    values = (
        key, str(source_system or ""), str(source_operation or ""),
        source_date, now, digest, category,
        str(classification.get("subcategory") or ""),
        float(classification.get("confidence") or 0),
        str(classification.get("reason") or ""),
        normalized["delivery_req_no"], normalized["detail_seq"],
        normalized["delivery_req_name"], normalized["delivery_change_order"],
        normalized["is_final_delivery_request"], normalized["detail_item_no"],
        normalized["detail_item_name"], normalized["item_id"],
        normalized["item_name"], normalized["model_name"],
        normalized["demand_org"], normalized["demand_region"],
        normalized["vendor_name"], normalized["vendor_bizno"],
        normalized["contract_no"], normalized["quantity"],
        normalized["unit_price"], normalized["amount"],
        normalized["delivery_req_total_amount"],
        1, "", "", now,
    )
    sql = """INSERT INTO shopping_records(
        source_key,source_system,source_operation,source_date,fetched_at,payload_sha256,
        primary_category,subcategory,classification_confidence,classification_reason,
        delivery_req_no,detail_seq,delivery_req_name,delivery_change_order,
        is_final_delivery_request,detail_item_no,detail_item_name,item_id,item_name,
        model_name,demand_org,demand_region,vendor_name,vendor_bizno,contract_no,
        quantity,unit_price,amount,delivery_req_total_amount,
        is_active,inactive_reason,inactive_at,updated_at
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(source_key) DO UPDATE SET
        source_system=excluded.source_system,
        source_operation=excluded.source_operation,
        source_date=excluded.source_date,
        fetched_at=excluded.fetched_at,
        payload_sha256=excluded.payload_sha256,
        primary_category=excluded.primary_category,
        subcategory=excluded.subcategory,
        classification_confidence=excluded.classification_confidence,
        classification_reason=excluded.classification_reason,
        delivery_req_no=excluded.delivery_req_no,
        detail_seq=excluded.detail_seq,
        delivery_req_name=excluded.delivery_req_name,
        delivery_change_order=excluded.delivery_change_order,
        is_final_delivery_request=excluded.is_final_delivery_request,
        detail_item_no=excluded.detail_item_no,
        detail_item_name=excluded.detail_item_name,
        item_id=excluded.item_id,
        item_name=excluded.item_name,
        model_name=excluded.model_name,
        demand_org=excluded.demand_org,
        demand_region=excluded.demand_region,
        vendor_name=excluded.vendor_name,
        vendor_bizno=excluded.vendor_bizno,
        contract_no=excluded.contract_no,
        quantity=excluded.quantity,
        unit_price=excluded.unit_price,
        amount=excluded.amount,
        delivery_req_total_amount=excluded.delivery_req_total_amount,
        is_active=1,
        inactive_reason='',
        inactive_at='',
        updated_at=excluded.updated_at"""

    with _write(_conn) as conn:
        conn.execute(sql, values)
        # Keep the old RAW path only inside isolated tests so the large legacy
        # regression suite remains useful while production 4.1 stores no source JSON.
        if _test_mode():
            from vnext_store import preserve_raw
            preserve_raw(
                dataset, key, payload,
                source_system=source_system,
                source_operation=source_operation,
                source_date=source_date,
                _conn=conn,
            )
    return digest


def reconcile_complete_scope(
    *,
    dataset,
    scope_key,
    generation,
    source_date,
    fetched_count,
    _conn=None,
):
    """Mark stale normalized rows inactive after one non-empty COMPLETE day scan.

    The row itself is retained as change-order/history evidence. A source identity
    observed in the COMPLETE generation with stored=0 became non-target; an
    identity absent from the generation disappeared from the authoritative day
    result. Empty COMPLETE scans are fail-safe and never deactivate a whole day.
    """
    if str(dataset) != "shopping_delivery":
        raise ValueError("UNSUPPORTED_SHOPPING_DATASET")
    try:
        date_text = _validated_source_date(source_date)
    except ValueError as exc:
        raise ValueError("SHOPPING_RECONCILE_SOURCE_DATE_INVALID") from exc
    fetched = max(0, int(fetched_count or 0))
    if fetched == 0:
        return {
            "status": "SKIPPED_EMPTY_FAILSAFE",
            "source_date": date_text,
            "observed": 0,
            "target_observed": 0,
            "deactivated": 0,
        }

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with _write(_conn) as conn:
        receipt = conn.execute(
            """SELECT COUNT(*) AS observed,
                      SUM(CASE WHEN stored=1 THEN 1 ELSE 0 END) AS target_observed
               FROM vnext_collection_items
               WHERE dataset=? AND scope_key=? AND generation=?""",
            (str(dataset), str(scope_key), str(generation)),
        ).fetchone()
        observed = int(receipt["observed"] or 0)
        target_observed = int(receipt["target_observed"] or 0)
        if observed != fetched:
            raise RuntimeError("SHOPPING_RECONCILE_RECEIPT_COUNT_MISMATCH")

        result = conn.execute(
            """UPDATE shopping_records
               SET is_active=0,
                   inactive_reason=CASE
                     WHEN EXISTS (
                       SELECT 1
                       FROM vnext_collection_items i
                       WHERE i.dataset=? AND i.scope_key=? AND i.generation=?
                         AND i.source_key=shopping_records.source_key
                         AND i.stored=0
                     )
                     THEN 'OUTSIDE_TARGET_SCOPE'
                     ELSE 'MISSING_FROM_COMPLETE_SOURCE'
                   END,
                   inactive_at=?,
                   updated_at=?
               WHERE source_date=?
                 AND is_active=1
                 AND NOT EXISTS (
                   SELECT 1
                   FROM vnext_collection_items i
                   WHERE i.dataset=? AND i.scope_key=? AND i.generation=?
                     AND i.source_key=shopping_records.source_key
                     AND i.stored=1
                 )""",
            (
                str(dataset), str(scope_key), str(generation),
                now, now, date_text,
                str(dataset), str(scope_key), str(generation),
            ),
        )
        deactivated = max(0, int(result.rowcount or 0))

    return {
        "status": "COMPLETE",
        "source_date": date_text,
        "observed": observed,
        "target_observed": target_observed,
        "deactivated": deactivated,
    }


def _retention_batch_size(value=None):
    raw = (
        os.getenv(
            "G2B_SHOPPING_RETENTION_BATCH_SIZE",
            str(DEFAULT_RETENTION_BATCH_SIZE),
        )
        if value is None
        else value
    )
    try:
        parsed = int(str(raw or DEFAULT_RETENTION_BATCH_SIZE).strip())
    except (TypeError, ValueError):
        parsed = DEFAULT_RETENTION_BATCH_SIZE
    return max(
        MIN_RETENTION_BATCH_SIZE,
        min(parsed, MAX_RETENTION_BATCH_SIZE),
    )


def _subtract_calendar_months(day, months):
    """Subtract whole calendar months while clamping to the target month end."""
    count = max(0, int(months))
    index = day.year * 12 + (day.month - 1) - count
    year, month_index = divmod(index, 12)
    month = month_index + 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(day.day, last_day))


def retention_cutoff_date(
    retention_days=DEFAULT_RETENTION_DAYS,
    *,
    retention_months=0,
    now=None,
):
    """Return the inclusive oldest source date kept by shopping retention.

    Production uses an exact calendar-month window. retention_days remains for
    backward-compatible callers/tests and is ignored when retention_months is set.
    """
    stamp = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(stamp, dt.date) and not isinstance(stamp, dt.datetime):
        current_day = stamp
    else:
        kst = dt.timezone(dt.timedelta(hours=9))
        # The shopping collector's calendar is KST. Treat a naive datetime as a
        # KST wall-clock value too, so retention and collection cannot disagree by
        # one day around 00:00 KST.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=kst)
        current_day = stamp.astimezone(kst).date()

    months = max(0, min(int(retention_months or 0), MAX_RETENTION_MONTHS))
    if months > 0:
        months = max(MIN_RETENTION_MONTHS, months)
        return _subtract_calendar_months(current_day, months)

    days = max(
        MIN_RETENTION_DAYS,
        min(int(retention_days), MAX_RETENTION_DAYS),
    )
    return current_day - dt.timedelta(days=days)


def purge_history(
    retention_days=DEFAULT_RETENTION_DAYS,
    *,
    retention_months=0,
    now=None,
    batch_size=None,
):
    """Purge shopping history using the requested compatibility window.

    Normalized shopping rows are keyset-batched into short transactions. Shopping
    checkpoint/receipt cleanup runs one source-day scope per transaction. The
    operational collector uses the same retention floor, so purged dates cannot be
    fetched again.
    """
    ensure_schema()
    from vnext_collection import ensure_collection_storage
    ensure_collection_storage()

    days = max(
        MIN_RETENTION_DAYS,
        min(int(retention_days), MAX_RETENTION_DAYS),
    )
    months = max(0, min(int(retention_months or 0), MAX_RETENTION_MONTHS))
    if months > 0:
        months = max(MIN_RETENTION_MONTHS, months)
    batch = _retention_batch_size(batch_size)
    cutoff = retention_cutoff_date(
        days,
        retention_months=months,
        now=now,
    ).isoformat()

    # Defensive cleanup for any pre-contract rows created before source-date
    # validation existed. Distinct dates use the indexed source_date column; rows
    # sharing one invalid value are deleted in bounded transactions.
    with connect() as conn:
        source_dates = [
            str(row["source_date"] or "")
            for row in conn.execute(
                "SELECT DISTINCT source_date FROM shopping_records"
            ).fetchall()
        ]
    invalid_source_dates = []
    for value in source_dates:
        try:
            _validated_source_date(value)
        except ValueError:
            invalid_source_dates.append(value)

    invalid_records = 0
    for invalid_date in invalid_source_dates:
        while True:
            with connect() as conn:
                result = conn.execute(
                    """DELETE FROM shopping_records
                       WHERE source_key IN (
                         SELECT source_key
                         FROM shopping_records
                         WHERE source_date=?
                         ORDER BY source_key
                         LIMIT ?
                       )""",
                    (invalid_date, batch),
                )
                removed = max(0, int(result.rowcount or 0))
            invalid_records += removed
            if removed < batch:
                break

    with connect() as conn:
        expired = conn.execute(
            """SELECT COUNT(*) AS n,
                      SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS active_n,
                      SUM(CASE WHEN is_active=0 THEN 1 ELSE 0 END) AS inactive_n
               FROM shopping_records
               WHERE source_date<>'' AND source_date<?""",
            (cutoff,),
        ).fetchone()
        expired_records = int(expired["n"] or 0)
        expired_active = int(expired["active_n"] or 0)
        expired_inactive = int(expired["inactive_n"] or 0)

        old_scopes_row = conn.execute(
            """SELECT COUNT(*) AS n
               FROM collection_checkpoints
               WHERE dataset='shopping_delivery'
                 AND (
                   (range_end<>'' AND range_end<?)
                   OR (
                     range_end=''
                     AND LENGTH(scope_key)=21
                     AND SUBSTR(scope_key,11,1)=':'
                     AND SUBSTR(scope_key,12,10)<?
                   )
                 )""",
            (cutoff, cutoff),
        ).fetchone()
        expired_scopes = int(old_scopes_row["n"] or 0)

        # Include orphan page/item receipt scopes whose checkpoint is already gone.
        scope_rows = conn.execute(
            """SELECT scope_key FROM collection_checkpoints
               WHERE dataset='shopping_delivery'
                 AND (
                   (range_end<>'' AND range_end<?)
                   OR (
                     range_end=''
                     AND LENGTH(scope_key)=21
                     AND SUBSTR(scope_key,11,1)=':'
                     AND SUBSTR(scope_key,12,10)<?
                   )
                 )
               UNION
               SELECT scope_key FROM vnext_collection_pages
               WHERE dataset='shopping_delivery'
                 AND LENGTH(scope_key)=21
                 AND SUBSTR(scope_key,11,1)=':'
                 AND SUBSTR(scope_key,12,10)<?
               UNION
               SELECT scope_key FROM vnext_collection_items
               WHERE dataset='shopping_delivery'
                 AND LENGTH(scope_key)=21
                 AND SUBSTR(scope_key,11,1)=':'
                 AND SUBSTR(scope_key,12,10)<?""",
            (cutoff, cutoff, cutoff, cutoff),
        ).fetchall()
    expired_scope_keys = sorted({
        str(row["scope_key"] or "")
        for row in scope_rows
        if str(row["scope_key"] or "")
    })

    deleted_items = 0
    deleted_pages = 0
    deleted_checkpoints = 0
    for scope_key in expired_scope_keys:
        with connect() as conn:
            item_result = conn.execute(
                """DELETE FROM vnext_collection_items
                   WHERE dataset='shopping_delivery' AND scope_key=?""",
                (scope_key,),
            )
            page_result = conn.execute(
                """DELETE FROM vnext_collection_pages
                   WHERE dataset='shopping_delivery' AND scope_key=?""",
                (scope_key,),
            )
            checkpoint_result = conn.execute(
                """DELETE FROM collection_checkpoints
                   WHERE dataset='shopping_delivery' AND scope_key=?""",
                (scope_key,),
            )
            deleted_items += max(0, int(item_result.rowcount or 0))
            deleted_pages += max(0, int(page_result.rowcount or 0))
            deleted_checkpoints += max(
                0, int(checkpoint_result.rowcount or 0)
            )

    deleted_records = 0
    record_batches = 0
    while True:
        with connect() as conn:
            result = conn.execute(
                """DELETE FROM shopping_records
                   WHERE source_key IN (
                     SELECT source_key
                     FROM shopping_records
                     WHERE source_date<>'' AND source_date<?
                     ORDER BY source_date,source_key
                     LIMIT ?
                   )""",
                (cutoff, batch),
            )
            removed = max(0, int(result.rowcount or 0))
        if removed <= 0:
            break
        deleted_records += removed
        record_batches += 1
        if removed < batch:
            break

    return {
        "retention_days": days,
        "retention_months": months,
        "retention_batch_size": batch,
        "cutoff_date": cutoff,
        "invalid_source_dates": len(invalid_source_dates),
        "deleted_invalid_source_date_records": invalid_records,
        "expired_records": expired_records,
        "expired_active_records": expired_active,
        "expired_inactive_records": expired_inactive,
        "expired_scopes": expired_scopes,
        "expired_scope_keys": len(expired_scope_keys),
        "record_delete_batches": record_batches,
        "deleted_records": deleted_records,
        "deleted_checkpoints": deleted_checkpoints,
        "deleted_collection_pages": deleted_pages,
        "deleted_collection_items": deleted_items,
    }


def count():
    """Read passive shopping counts without PostgreSQL DDL/index inspection."""
    if _test_mode():
        ensure_schema()
    # Production schema is installed by the boot/collector owner.  A dashboard
    # refresh must not run PRAGMA, CREATE/ALTER INDEX, or other write operations.
    with connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS history_n,
                      SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS active_n,
                      SUM(CASE WHEN is_active=0 THEN 1 ELSE 0 END) AS inactive_n,
                      MAX(updated_at) AS last_at,
                      MAX(CASE WHEN is_active=1 THEN updated_at ELSE '' END)
                        AS active_last_at
               FROM shopping_records"""
        ).fetchone()
    history = int(row["history_n"] or 0)
    active = int(row["active_n"] or 0)
    inactive = int(row["inactive_n"] or 0)
    return {
        # Compatibility: records remains the total preserved history count.
        "records": history,
        "history_records": history,
        "active_records": active,
        "inactive_records": inactive,
        "last_at": str(row["last_at"] or ""),
        "active_last_at": str(row["active_last_at"] or ""),
    }
