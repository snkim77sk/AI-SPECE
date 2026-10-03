"""Normalized shopping/business storage for G2B 4.1.

Production persists only fields used by the G2B read model plus a source payload
hash. The original source JSON is intentionally not stored.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from contextlib import contextmanager

from db import connect
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


REGIONS = (
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
    "경상남도", "제주특별자치도",
)


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


def _canonical_region(value):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    for region in REGIONS:
        short = (
            region.replace("특별자치도", "")
            .replace("특별자치시", "")
            .replace("광역시", "")
            .replace("특별시", "")
            .replace("도", "")
        )
        if text == region or text.startswith(region + " "):
            return region
        if short and (text == short or text.startswith(short + " ")):
            return region
    return ""


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


@contextmanager
def _write(existing=None):
    if existing is not None:
        yield existing
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def preserve_record(dataset, source_key, payload, *, source_system="",
                    source_operation="", source_date="", _conn=None):
    if str(dataset) != "shopping_delivery":
        raise ValueError("UNSUPPORTED_SHOPPING_DATASET")
    if not isinstance(payload, dict):
        raise ValueError("SHOPPING_PAYLOAD_OBJECT_REQUIRED")
    if not shopping_scope_v4.should_store(payload):
        raise ValueError("SHOPPING_RECORD_OUTSIDE_TARGET_SCOPE")
    key = str(source_key or "").strip()
    if not key:
        raise ValueError("SHOPPING_SOURCE_KEY_REQUIRED")

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
        str(source_date or ""), now, digest, category,
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
    date_text = str(source_date or "").strip()
    if not date_text:
        raise ValueError("SHOPPING_RECONCILE_SOURCE_DATE_REQUIRED")
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


def retention_cutoff_date(retention_days=DEFAULT_RETENTION_DAYS, *, now=None):
    """Return the inclusive oldest source date kept by shopping retention."""
    days = max(
        MIN_RETENTION_DAYS,
        min(int(retention_days), MAX_RETENTION_DAYS),
    )
    stamp = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(stamp, dt.date) and not isinstance(stamp, dt.datetime):
        current_day = stamp
    else:
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        kst = dt.timezone(dt.timedelta(hours=9))
        current_day = stamp.astimezone(kst).date()
    return current_day - dt.timedelta(days=days)


def purge_history(retention_days=DEFAULT_RETENTION_DAYS, *, now=None):
    """Keep only the rolling one-year shopping business-data window.

    Normalized shopping rows older than the retention floor are deleted. Matching
    shopping checkpoints and any residual page/item receipts are removed too.
    The operational collector clamps its baseline/recheck windows to the same
    retention floor, so purged dates are never fetched again.
    """
    ensure_schema()
    from vnext_collection import ensure_collection_storage
    ensure_collection_storage()
    cutoff = retention_cutoff_date(retention_days, now=now).isoformat()
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

        old_scopes = conn.execute(
            """SELECT COUNT(*) AS n
               FROM collection_checkpoints
               WHERE dataset='shopping_delivery'
                 AND range_end<>'' AND range_end<?""",
            (cutoff,),
        ).fetchone()
        expired_scopes = int(old_scopes["n"] or 0)

        # Receipts can outlive a checkpoint after an interrupted legacy cleanup.
        # Delete by the one-day scope's end date directly so orphan page/item rows
        # cannot escape the rolling retention window.
        item_result = conn.execute(
            """DELETE FROM vnext_collection_items
               WHERE dataset='shopping_delivery'
                 AND LENGTH(scope_key)=21
                 AND SUBSTR(scope_key,11,1)=':'
                 AND SUBSTR(scope_key,12,10)<?""",
            (cutoff,),
        )
        page_result = conn.execute(
            """DELETE FROM vnext_collection_pages
               WHERE dataset='shopping_delivery'
                 AND LENGTH(scope_key)=21
                 AND SUBSTR(scope_key,11,1)=':'
                 AND SUBSTR(scope_key,12,10)<?""",
            (cutoff,),
        )
        checkpoint_result = conn.execute(
            """DELETE FROM collection_checkpoints
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
        )
        record_result = conn.execute(
            """DELETE FROM shopping_records
               WHERE source_date<>'' AND source_date<?""",
            (cutoff,),
        )

    return {
        "retention_days": max(
            MIN_RETENTION_DAYS,
            min(int(retention_days), MAX_RETENTION_DAYS),
        ),
        "cutoff_date": cutoff,
        "expired_records": expired_records,
        "expired_active_records": expired_active,
        "expired_inactive_records": expired_inactive,
        "expired_scopes": expired_scopes,
        "deleted_records": max(0, int(record_result.rowcount or 0)),
        "deleted_checkpoints": max(0, int(checkpoint_result.rowcount or 0)),
        "deleted_collection_pages": max(0, int(page_result.rowcount or 0)),
        "deleted_collection_items": max(0, int(item_result.rowcount or 0)),
    }


def count():
    """Return current active rows separately from preserved shopping history."""
    ensure_schema()
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
