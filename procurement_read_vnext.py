"""Read-only procurement views for the clean G2B 4.1 runtime.

Shopping rows come from normalized records; this module never calls external sources.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

TARGET_CATEGORIES = ("LIGHTING", "POLE")
REGIONS = (
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도", "전라남도", "경상북도",
    "경상남도", "제주특별자치도",
)


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
    """Build a literal contains-pattern portable across SQLite and PostgreSQL."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("!", "!!").replace("%", "!%").replace("_", "!_")
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
        where.append("(r.source_key LIKE ? ESCAPE '!' OR r.payload_json LIKE ? ESCAPE '!')")
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
    return _canonical_region(explicit) or _canonical_region(demand_org)


def _legacy_test_shopping_rows(*, categories=TARGET_CATEGORIES, query="", region="",
                               start_date="", end_date="", limit=200, offset=0):
    """Read old SQLite RAW fixtures only when G2B_TEST_MODE is explicitly enabled."""
    local_filtering = bool(region or start_date or end_date)
    source = _query_current(
        "shopping_delivery",
        categories=categories,
        query=query,
        limit=None if local_filtering else limit,
        offset=0 if local_filtering else offset,
    )
    out = []
    for raw in source:
        p = _payload(raw["payload_json"])
        demand_org = _pick(
            p, "dminsttNm", "demandInsttNm", "demandOrgNm", "demandOrgName",
            "orderInsttNm", "insttNm",
        )
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
            "delivery_change_order": _pick(p, "dlvrReqChgOrd", "deliveryReqChangeOrder"),
            "is_final_delivery_request": _pick(p, "fnlDlvrReqYn", "finalDeliveryReqYn"),
            "detail_item_no": _pick(p, "dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo", "dtlPrdctClsfcNo"),
            "detail_item_name": _pick(p, "dtilPrdctClsfcNoNm", "dtilPrdctClsfcNm", "detailPrdctNm", "detailItemName"),
            "item_id": _pick(p, "prdctIdntNo", "itemId", "productId"),
            "item_name": _pick(p, "prdctIdntNoNm", "prdctIdntNm", "prdctNm", "itemName"),
            "model_name": _pick(p, "modelNm", "modelName", "prdctSpecNm", "specNm"),
            "demand_org": demand_org,
            "demand_region": _region_name(p, demand_org),
            "vendor_name": _pick(
                p, "corpNm", "cntrctCorpNm", "entrpsNm", "vendorNm", "vendorName",
                "supplierNm", "supplierName", "cntrctCorpName",
            ),
            "vendor_bizno": _pick(
                p, "cntrctCorpBizno", "corpBizno", "vendorBizno", "bizno", "bizrno"
            ),
            "contract_no": _pick(p, "cntrctNo", "contractNo"),
            "quantity": _float(_pick(p, "prdctQty", "dlvrReqQty", "reqQty", "quantity", "qty")),
            "unit_price": _number(_pick(p, "prdctUprc", "unitPric", "unitPrice", "cntrctUnitPric", "cntrctPrce", "prc")),
            "amount": 0,
            "delivery_req_total_amount": _number(_pick(p, "dlvrReqAmt", "reqAmt")),
        }
        calculated = (
            int(round(row["unit_price"] * row["quantity"]))
            if row["unit_price"] and row["quantity"] else 0
        )
        source_amount = _number(
            _pick(p, "prdctAmt", "supplyAmount", "amount", "dlvrReqDtlAmt")
        )
        row["amount"] = source_amount or calculated
        if not row["unit_price"] and row["amount"] and row["quantity"]:
            row["unit_price"] = int(round(row["amount"] / row["quantity"]))
            row["unit_price_basis"] = "CALCULATED_AMOUNT_DIV_QUANTITY"
        else:
            row["unit_price_basis"] = "SOURCE" if row["unit_price"] else "UNAVAILABLE"
        if region and str(row["demand_region"]) != str(region):
            continue
        source_day = str(row.get("source_date") or "")
        if start_date and source_day < str(start_date):
            continue
        if end_date and source_day > str(end_date):
            continue
        out.append(row)

    if local_filtering:
        start = max(0, int(offset))
        if limit is None:
            return out[start:]
        size = max(1, min(int(limit), 5000))
        return out[start:start + size]
    return out


def shopping_rows(*, categories=TARGET_CATEGORIES, query="", region="",
                  start_date="", end_date="", limit=200, offset=0,
                  include_inactive=False):
    """Read normalized 2026-01-01+ lighting/pole business records.

    start_date/end_date are inclusive ISO dates and filter the indexed source_date
    column without source API traffic. Production defaults to currently active source
    identities. Historical inactive change orders remain queryable with
    include_inactive=True.
    """
    test_mode = str(
        os.getenv("G2B_TEST_MODE", "0") or ""
    ).lower() in {"1", "true", "yes", "on"}
    if test_mode:
        import runtime_role
        if not runtime_role.is_local_collector():
            return _legacy_test_shopping_rows(
                categories=categories,
                query=query,
                region=region,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
                offset=offset,
            )
    # Production schema is installed once during backend initialization.
    # Read-only web requests must never run DDL/index checks because a collector
    # may be writing the same table and managed PostgreSQL can otherwise block.
    if test_mode:
        import shopping_store_v41
        shopping_store_v41.ensure_schema()

    selected = [str(x).upper() for x in categories if str(x).strip()]
    if not selected:
        return []
    params = list(selected)
    where = ["primary_category IN (%s)" % ",".join("?" for _ in selected)]
    if not include_inactive:
        where.append("is_active=1")
    def normalized_bound(value, name):
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return dt.date.fromisoformat(text).isoformat()
        except ValueError:
            raise ValueError(f"{name}_INVALID") from None

    start_date = normalized_bound(start_date, "start_date")
    end_date = normalized_bound(end_date, "end_date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("SHOPPING_DATE_RANGE_INVALID")
    if start_date:
        where.append("source_date>=?")
        params.append(start_date)
    if end_date:
        where.append("source_date<=?")
        params.append(end_date)

    if region:
        # 4.1.8 reads both new canonical rows ("인천광역시") and pre-fix rows
        # that retained district detail ("인천광역시 미추홀구") without a resync.
        where.append("(demand_region=? OR demand_region LIKE ?)")
        params.extend([str(region), str(region) + " %"])
    pattern = _like_pattern(query)
    if pattern:
        searchable = (
            "source_key", "delivery_req_name", "detail_item_name", "item_name",
            "model_name", "demand_org", "vendor_name", "contract_no",
        )
        where.append(
            "(" + " OR ".join(f"{name} LIKE ? ESCAPE '!'" for name in searchable) + ")"
        )
        params.extend([pattern] * len(searchable))

    page_clause = ""
    if limit is not None:
        page_clause = "LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 5000)), max(0, int(offset))])

    with connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM shopping_records
                WHERE {' AND '.join(where)}
                ORDER BY source_date DESC,updated_at DESC,source_key DESC
                {page_clause}""",
            tuple(params),
        ).fetchall()
    out = [dict(row) for row in rows]
    if limit is None and int(offset or 0) > 0:
        out = out[max(0, int(offset)):]
    for row in out:
        row["demand_region"] = (
            _canonical_region(row.get("demand_region"))
            or str(row.get("demand_region") or "")
        )
        row["classification_confidence"] = float(
            row.get("classification_confidence") or 0
        )
        if (
            not int(row.get("unit_price") or 0)
            and int(row.get("amount") or 0)
            and float(row.get("quantity") or 0)
        ):
            row["unit_price"] = int(round(
                int(row["amount"]) / float(row["quantity"])
            ))
            row["unit_price_basis"] = "CALCULATED_AMOUNT_DIV_QUANTITY"
        else:
            row["unit_price_basis"] = (
                "SOURCE" if int(row.get("unit_price") or 0) else "UNAVAILABLE"
            )
    return out

def _bizno(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _vendor_identity(name, bizno):
    normalized_name = " ".join(str(name or "").casefold().split())
    return (_bizno(bizno), normalized_name)


def _change_order_rank(value):
    text="".join(ch for ch in str(value or "") if ch.isdigit())
    try:
        return int(text) if text else 0
    except ValueError:
        return 0


def _latest_shopping_change_rows(rows):
    """Select the latest change order per delivery-request item for amount analysis."""
    selected={}
    for row in rows:
        request_no=str(row.get("delivery_req_no") or "")
        detail_seq=str(row.get("detail_seq") or "")
        logical=(request_no,detail_seq) if request_no and detail_seq else ("SOURCE",str(row.get("source_key") or ""))
        rank=(
            _change_order_rank(row.get("delivery_change_order")),
            1 if str(row.get("is_final_delivery_request") or "").upper()=="Y" else 0,
            str(row.get("source_date") or ""),
            str(row.get("fetched_at") or ""),
            str(row.get("source_key") or ""),
        )
        current=selected.get(logical)
        if current is None or rank > current[0]:
            selected[logical]=(rank,row)
    return [value[1] for value in selected.values()]


def _new_vendor(name, bizno):
    return {
        "vendor_name": str(name or "").strip(),
        "vendor_bizno": _bizno(bizno),
        "shopping_rows": 0,
        "shopping_amount": 0,
        "demand_orgs": set(),
        "categories": set(),
    }


def vendor_rows(*, query="", region="", limit=200, offset=0):
    vendors = {}
    shopping = _latest_shopping_change_rows(
        shopping_rows(categories=TARGET_CATEGORIES, region=region, limit=None)
    )
    # Use request-level totals only as a fallback when that request has no item-level
    # amounts at all. Count the fallback once per vendor/request to prevent a
    # multi-item delivery request from multiplying dlvrReqAmt by its item count.
    requests_with_item_amount = {
        (_vendor_identity(row.get("vendor_name"), row.get("vendor_bizno")),
         str(row.get("delivery_req_no") or ""))
        for row in shopping
        if int(row.get("amount") or 0) > 0 and str(row.get("delivery_req_no") or "")
    }
    request_total_counted = set()

    for row in shopping:
        name = str(row.get("vendor_name") or "").strip()
        if not name:
            continue
        key = _vendor_identity(name, row.get("vendor_bizno"))
        item = vendors.setdefault(key, _new_vendor(name, row.get("vendor_bizno")))
        if not item["vendor_bizno"] and row.get("vendor_bizno"):
            item["vendor_bizno"] = _bizno(row["vendor_bizno"])
        item["shopping_rows"] += 1
        amount = int(row.get("amount") or 0)
        request_no = str(row.get("delivery_req_no") or "")
        request_key = (key, request_no)
        if amount > 0:
            item["shopping_amount"] += amount
        elif (
            request_no
            and request_key not in requests_with_item_amount
            and request_key not in request_total_counted
        ):
            item["shopping_amount"] += int(row.get("delivery_req_total_amount") or 0)
            request_total_counted.add(request_key)
        if row.get("demand_org"):
            item["demand_orgs"].add(str(row["demand_org"]))
        if row.get("primary_category"):
            item["categories"].add(str(row["primary_category"]))


    # If a row had no business number, merge it into a numbered vendor only when
    # that normalized name maps to exactly one known business number. If two
    # different business numbers share a name, never guess.
    numbered_by_name = {}
    for key in vendors:
        number, normalized_name = key
        if number:
            numbered_by_name.setdefault(normalized_name, []).append(key)
    for key in list(vendors):
        number, normalized_name = key
        if number:
            continue
        candidates = numbered_by_name.get(normalized_name, [])
        if len(candidates) != 1:
            continue
        source = vendors.pop(key)
        target = vendors[candidates[0]]
        target["shopping_rows"] += source["shopping_rows"]
        target["shopping_amount"] += source["shopping_amount"]
        target["demand_orgs"].update(source["demand_orgs"])
        target["categories"].update(source["categories"])

    out = []
    q = str(query or "").casefold().strip()
    for item in vendors.values():
        if q and q not in (item["vendor_name"] + " " + item["vendor_bizno"]).casefold():
            continue
        row = dict(item)
        row["demand_org_count"] = len(item["demand_orgs"])
        row["categories"] = sorted(item["categories"])
        row["total_amount"] = item["shopping_amount"]
        row.pop("demand_orgs", None)
        out.append(row)

    out.sort(
        key=lambda row: (
            -int(row["total_amount"]),
            row["vendor_name"],
            row["vendor_bizno"],
        )
    )
    start = max(0, int(offset))
    if limit is None:
        return out[start:]
    size = max(1, min(int(limit), 1000))
    return out[start:start + size]

def procurement_summary():
    shopping_history = shopping_rows(limit=None, include_inactive=True)
    shopping_active_history = [
        row
        for row in shopping_history
        if int(row.get("is_active", 1) or 0) == 1
    ]
    shopping = _latest_shopping_change_rows(shopping_active_history)
    vendors = vendor_rows(limit=None)
    return {
        "shopping_target_rows": len(shopping),
        "shopping_active_history_rows": len(shopping_active_history),
        "shopping_inactive_history_rows": (
            len(shopping_history) - len(shopping_active_history)
        ),
        "shopping_history_rows": len(shopping_history),
        "vendors": len(vendors),
        "shopping_amount": sum(int(row.get("amount") or 0) for row in shopping),
        "vendor_total_amount": sum(int(row.get("total_amount") or 0) for row in vendors),
        "read_only": True,
        "source_traffic": False,
        "selection_stage": "NORMALIZED_READ_MODEL",
        "source_collection_completeness_verified": False,
    }
