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
        normalized["delivery_req_total_amount"], now,
    )
    sql = """INSERT INTO shopping_records(
        source_key,source_system,source_operation,source_date,fetched_at,payload_sha256,
        primary_category,subcategory,classification_confidence,classification_reason,
        delivery_req_no,detail_seq,delivery_req_name,delivery_change_order,
        is_final_delivery_request,detail_item_no,detail_item_name,item_id,item_name,
        model_name,demand_org,demand_region,vendor_name,vendor_bizno,contract_no,
        quantity,unit_price,amount,delivery_req_total_amount,updated_at
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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


def count():
    ensure_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n,MAX(updated_at) AS last_at FROM shopping_records"
        ).fetchone()
    return {
        "records": int(row["n"] or 0),
        "last_at": str(row["last_at"] or ""),
    }
