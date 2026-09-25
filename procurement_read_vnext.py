"""Read-only procurement views for the clean G2B vNext runtime.

All rows come from current RAW payloads plus the current classifier version. This
module never calls external sources and never writes serving tables.
"""
from __future__ import annotations

import json

import analysis_vnext
from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

TARGET_CATEGORIES = ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")


def _payload(value):
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


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


def _like_pattern(value):
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + text + "%"


def _query_current(dataset, *, categories=None, query="", limit=200, offset=0):
    params = [CLASSIFIER_VERSION, str(dataset)]
    where = [
        "c.classifier_version=?",
        "r.dataset=?",
        "c.entity_type=r.dataset",
        "c.entity_key=r.source_key",
        "COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')",
    ]
    if categories is not None:
        selected = [str(x).upper() for x in categories if str(x).strip()]
        if not selected:
            return []
        where.append("c.primary_category IN (%s)" % ",".join("?" for _ in selected))
        params.extend(selected)
    pattern = _like_pattern(query)
    if pattern:
        where.append("(r.source_key LIKE ? ESCAPE '\\' OR r.payload_json LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])
    page_clause = ""
    if limit is None:
        if int(offset or 0) > 0:
            page_clause = "LIMIT -1 OFFSET ?"
            params.append(max(0, int(offset)))
    else:
        page_clause = "LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 5000)), max(0, int(offset))])
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = conn.execute(
            f"""SELECT r.id,r.source_key,r.source_date,r.fetched_at,r.payload_json,
                       c.primary_category,c.subcategory,c.confidence,c.reason
                FROM raw_records r
                JOIN classifications c
                  ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                WHERE {' AND '.join(where)}
                ORDER BY r.source_date DESC,r.id DESC
                {page_clause}""",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def _match_query(values, query):
    q = str(query or "").casefold().strip()
    if not q:
        return True
    return q in " | ".join(str(v or "") for v in values).casefold()


def shopping_rows(*, categories=TARGET_CATEGORIES, query="", limit=200, offset=0):
    # Filter in the read model only; RAW collection remains unfiltered.
    source_limit = None if limit is None or str(query or "").strip() else min(
        5000, max(500, int(limit) + int(offset) + 500)
    )
    source = _query_current(
        "shopping_delivery",
        categories=categories,
        limit=source_limit,
        offset=0,
    )
    out = []
    for raw in source:
        p = _payload(raw["payload_json"])
        row = {
            "source_key": raw["source_key"],
            "source_date": raw["source_date"],
            "fetched_at": raw["fetched_at"],
            "primary_category": raw["primary_category"],
            "subcategory": raw["subcategory"],
            "classification_confidence": float(raw["confidence"] or 0),
            "delivery_req_no": _pick(p, "dlvrReqNo", "deliveryReqNo", "reqNo"),
            "detail_seq": _pick(p, "prdctSno", "dlvrReqDtlSeq", "dlvrReqDtlSn", "detailSeq", "seq"),
            "delivery_req_name": _pick(p, "dlvrReqNm", "deliveryReqNm", "dlvrReqSj"),
            "detail_item_no": _pick(p, "dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo", "dtlPrdctClsfcNo"),
            "detail_item_name": _pick(p, "dtilPrdctClsfcNoNm", "dtilPrdctClsfcNm", "detailPrdctNm", "detailItemName"),
            "item_id": _pick(p, "prdctIdntNo", "itemId", "productId"),
            "item_name": _pick(p, "prdctIdntNoNm", "prdctIdntNm", "prdctNm", "itemName"),
            "model_name": _pick(p, "modelNm", "modelName", "prdctSpecNm", "specNm"),
            "demand_org": _pick(p, "dminsttNm", "demandInsttNm", "demandOrgNm", "demandOrgName", "orderInsttNm", "insttNm"),
            "vendor_name": _pick(p, "corpNm", "cntrctCorpNm", "entrpsNm", "vendorNm", "vendorName", "supplierNm", "supplierName", "cntrctCorpName"),
            "vendor_bizno": _pick(p, "cntrctCorpBizno", "corpBizno", "vendorBizno", "bizno", "bizrno"),
            "contract_no": _pick(p, "cntrctNo", "contractNo"),
            "quantity": _float(_pick(p, "prdctQty", "dlvrReqQty", "reqQty", "quantity", "qty")),
            "unit_price": _number(_pick(p, "prdctUprc", "unitPric", "unitPrice", "cntrctUnitPric", "cntrctPrce", "prc")),
            "amount": 0,
        }
        calculated = int(round(row["unit_price"] * row["quantity"])) if row["unit_price"] and row["quantity"] else 0
        source_amount = _number(_pick(p, "prdctAmt", "supplyAmount", "amount", "dlvrAmt", "dlvrReqAmt", "reqAmt", "dlvrReqDtlAmt"))
        row["amount"] = calculated or source_amount
        if _match_query(
            (
                row["delivery_req_no"], row["delivery_req_name"], row["detail_item_no"],
                row["detail_item_name"], row["item_id"], row["item_name"], row["model_name"],
                row["demand_org"], row["vendor_name"],
            ),
            query,
        ):
            out.append(row)
    start = max(0, int(offset))
    if limit is None:
        return out[start:]
    size = max(1, min(int(limit), 1000))
    return out[start:start + size]


def goods_notice_rows(*, categories=TARGET_CATEGORIES, query="", limit=200, offset=0):
    source_limit = None if limit is None or str(query or "").strip() else min(
        5000, max(500, int(limit) + int(offset) + 500)
    )
    source = _query_current(
        "bid_notice_goods",
        categories=categories,
        limit=source_limit,
        offset=0,
    )
    out = []
    for raw in source:
        p = _payload(raw["payload_json"])
        row = {
            "source_key": raw["source_key"],
            "source_date": raw["source_date"],
            "fetched_at": raw["fetched_at"],
            "primary_category": raw["primary_category"],
            "subcategory": raw["subcategory"],
            "classification_confidence": float(raw["confidence"] or 0),
            "notice_no": _pick(p, "bidNtceNo", "bidNoticeNo"),
            "notice_order": _pick(p, "bidNtceOrd", "bidNoticeOrd") or "000",
            "notice_name": _pick(p, "bidNtceNm", "bidNoticeName"),
            "notice_org": _pick(p, "ntceInsttNm", "noticeInsttNm", "noticeOrgName"),
            "demand_org": _pick(p, "dminsttNm", "demandInsttNm", "demandOrgName"),
            "notice_date": _pick(p, "bidNtceDt", "bidNoticeDate"),
            "close_date": _pick(p, "bidClseDt", "bidCloseDate"),
            "open_date": _pick(p, "opengDt", "openDate"),
            "budget_amount": _number(_pick(p, "asignBdgtAmt", "budgetAmount", "bdgtAmt")),
            "estimated_price": _number(_pick(p, "presmptPrce", "estimatedPrice", "estmtPrce")),
            "detail_item_no": _pick(p, "dtilPrdctClsfcNo", "dtlPrdctClsfcNo", "detailItemNo"),
            "detail_item_name": _pick(p, "dtilPrdctClsfcNoNm", "dtlPrdctClsfcNoNm", "detailItemName"),
        }
        if _match_query(
            (
                row["notice_no"], row["notice_name"], row["notice_org"], row["demand_org"],
                row["detail_item_no"], row["detail_item_name"],
            ),
            query,
        ):
            out.append(row)
    start = max(0, int(offset))
    if limit is None:
        return out[start:]
    size = max(1, min(int(limit), 1000))
    return out[start:start + size]


def vendor_rows(*, query="", limit=200, offset=0):
    vendors = {}

    for row in shopping_rows(categories=TARGET_CATEGORIES, limit=None):
        name = str(row.get("vendor_name") or "").strip()
        if not name:
            continue
        item = vendors.setdefault(
            name,
            {
                "vendor_name": name,
                "vendor_bizno": str(row.get("vendor_bizno") or ""),
                "shopping_rows": 0,
                "service_contracts": 0,
                "shopping_amount": 0,
                "contract_amount": 0,
                "demand_orgs": set(),
                "categories": set(),
            },
        )
        if not item["vendor_bizno"] and row.get("vendor_bizno"):
            item["vendor_bizno"] = str(row["vendor_bizno"])
        item["shopping_rows"] += 1
        item["shopping_amount"] += int(row.get("amount") or 0)
        if row.get("demand_org"):
            item["demand_orgs"].add(str(row["demand_org"]))
        if row.get("primary_category"):
            item["categories"].add(str(row["primary_category"]))

    seen_contracts = set()
    for row in analysis_vnext.target_service_lifecycle_rows(limit=None):
        name = str(row.get("contract_vendor") or "").strip()
        number = str(row.get("contract_no") or "").strip()
        if not name or not number:
            continue
        identity = (number, name, str(row.get("contract_vendor_bizno") or ""))
        if identity in seen_contracts:
            continue
        seen_contracts.add(identity)
        item = vendors.setdefault(
            name,
            {
                "vendor_name": name,
                "vendor_bizno": str(row.get("contract_vendor_bizno") or ""),
                "shopping_rows": 0,
                "service_contracts": 0,
                "shopping_amount": 0,
                "contract_amount": 0,
                "demand_orgs": set(),
                "categories": set(),
            },
        )
        if not item["vendor_bizno"] and row.get("contract_vendor_bizno"):
            item["vendor_bizno"] = str(row["contract_vendor_bizno"])
        item["service_contracts"] += 1
        item["contract_amount"] += int(row.get("contract_amount") or 0)
        if row.get("demand_org"):
            item["demand_orgs"].add(str(row["demand_org"]))
        if row.get("primary_category"):
            item["categories"].add(str(row["primary_category"]))

    out = []
    q = str(query or "").casefold().strip()
    for item in vendors.values():
        if q and q not in (item["vendor_name"] + " " + item["vendor_bizno"]).casefold():
            continue
        row = dict(item)
        row["demand_org_count"] = len(item["demand_orgs"])
        row["categories"] = sorted(item["categories"])
        row["total_amount"] = item["shopping_amount"] + item["contract_amount"]
        row.pop("demand_orgs", None)
        out.append(row)
    out.sort(key=lambda row: (-int(row["total_amount"]), row["vendor_name"]))
    start = max(0, int(offset))
    size = max(1, min(int(limit), 1000))
    return out[start:start + size]


def procurement_summary():
    shopping = shopping_rows(limit=5000)
    goods = goods_notice_rows(limit=5000)
    vendors = vendor_rows(limit=5000)
    return {
        "shopping_target_rows": len(shopping),
        "goods_target_notices": len(goods),
        "vendors": len(vendors),
        "shopping_amount": sum(int(row.get("amount") or 0) for row in shopping),
        "vendor_total_amount": sum(int(row.get("total_amount") or 0) for row in vendors),
        "read_only": True,
        "source_traffic": False,
        "selection_stage": "POST_RAW_ANALYSIS_ONLY",
        "source_collection_completeness_verified": False,
    }
