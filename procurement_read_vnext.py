"""Read-only procurement views for the clean G2B 4.1 runtime.

Shopping rows come from normalized records; this module never calls external sources.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from db import backend_name, connect
import admin_geography_v41
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

TARGET_CATEGORIES = ("LIGHTING", "POLE")
MAX_REQUEST_PAGE_OFFSET = 50000
REGIONS = admin_geography_v41.REGIONS


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
    return admin_geography_v41.canonical_region(value)

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


def _normalized_shopping_bound(value, name):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{name}_INVALID") from None


def _shopping_filter_parts(
    *,
    categories=TARGET_CATEGORIES,
    query="",
    region="",
    start_date="",
    end_date="",
    include_inactive=False,
):
    selected = [str(x).upper() for x in categories if str(x).strip()]
    if not selected:
        return [], []

    params = list(selected)
    where = ["primary_category IN (%s)" % ",".join("?" for _ in selected)]
    if not include_inactive:
        where.append("is_active=1")

    start_date = _normalized_shopping_bound(start_date, "start_date")
    end_date = _normalized_shopping_bound(end_date, "end_date")
    if start_date and end_date and start_date > end_date:
        raise ValueError("SHOPPING_DATE_RANGE_INVALID")
    if start_date:
        where.append("source_date>=?")
        params.append(start_date)
    if end_date:
        where.append("source_date<=?")
        params.append(end_date)

    if region:
        members = admin_geography_v41.region_history_members(region)
        if not members:
            return [], []
        # Current-region filters include predecessor region names for historical
        # continuity while preserving each stored row's original demand_region.
        region_clauses = []
        for member in members:
            region_clauses.append("(demand_region=? OR demand_region LIKE ?)")
            params.extend([str(member), str(member) + " %"])
        where.append("(" + " OR ".join(region_clauses) + ")")

    pattern = _like_pattern(query)
    if pattern:
        searchable = (
            "source_key", "delivery_req_no", "delivery_req_name",
            "detail_item_name", "item_name", "model_name",
            "demand_org", "vendor_name", "contract_no",
        )
        where.append(
            "("
            + " OR ".join(
                f"{name} LIKE ? ESCAPE '!'" for name in searchable
            )
            + ")"
        )
        params.extend([pattern] * len(searchable))
    return where, params


def _normalize_shopping_output(rows):
    out = [dict(row) for row in rows]
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
                "SOURCE"
                if int(row.get("unit_price") or 0)
                else "UNAVAILABLE"
            )
    return out


def _uses_normalized_shopping_store():
    test_mode = str(
        os.getenv("G2B_TEST_MODE", "0") or ""
    ).lower() in {"1", "true", "yes", "on"}
    if not test_mode:
        return True
    import runtime_role
    return runtime_role.is_local_collector()


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

    where, params = _shopping_filter_parts(
        categories=categories,
        query=query,
        region=region,
        start_date=start_date,
        end_date=end_date,
        include_inactive=include_inactive,
    )
    if not where:
        return []

    page_clause = ""
    if limit is not None:
        page_clause = "LIMIT ? OFFSET ?"
        params.extend([
            max(1, min(int(limit), 5000)),
            max(0, int(offset)),
        ])

    with connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM shopping_records
                WHERE {' AND '.join(where)}
                ORDER BY source_date DESC,updated_at DESC,source_key DESC
                {page_clause}""",
            tuple(params),
        ).fetchall()

    out = _normalize_shopping_output(rows)
    if limit is None and int(offset or 0) > 0:
        out = out[max(0, int(offset)):]
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


def _request_group_key(row):
    request_no = str(row.get("delivery_req_no") or "").strip()
    if request_no:
        return ("REQUEST", request_no)
    return ("SOURCE", str(row.get("source_key") or "").strip())


def _joined_distinct(rows, field):
    values = {
        str(row.get(field) or "").strip()
        for row in rows
        if str(row.get(field) or "").strip()
    }
    return " | ".join(sorted(values))


def _normalized_identity_text(value):
    return "".join(
        ch
        for ch in str(value or "").casefold()
        if ch.isalnum()
    )


def _distinct_nonempty(rows, field, *, digits_only=False):
    values = {}
    for row in rows:
        raw = str(row.get(field) or "").strip()
        if not raw:
            continue
        normalized = (
            "".join(ch for ch in raw if ch.isdigit())
            if digits_only
            else _normalized_identity_text(raw)
        )
        if normalized:
            values.setdefault(normalized, raw)
    return values


def _request_integrity_issues(rows):
    issues = []

    demand_orgs = _distinct_nonempty(rows, "demand_org")
    if len(demand_orgs) > 1:
        issues.append("DEMAND_ORG_CONFLICT")

    vendor_biznos = _distinct_nonempty(
        rows,
        "vendor_bizno",
        digits_only=True,
    )
    vendor_names = _distinct_nonempty(rows, "vendor_name")
    if len(vendor_biznos) > 1:
        issues.append("VENDOR_BIZNO_CONFLICT")
    elif not vendor_biznos and len(vendor_names) > 1:
        issues.append("VENDOR_NAME_CONFLICT")

    contract_nos = _distinct_nonempty(rows, "contract_no")
    if len(contract_nos) > 1:
        issues.append("CONTRACT_NO_CONFLICT")

    return sorted(set(issues))


def _single_raw_value(rows, field, *, digits_only=False):
    values = _distinct_nonempty(
        rows,
        field,
        digits_only=digits_only,
    )
    if len(values) == 1:
        return next(iter(values.values()))
    return ""


def shopping_request_rows_from_rows(rows):
    """Aggregate current target detail rows into one row per delivery request.

    Each detail sequence first resolves to its latest change order. The request
    amount is the exact sum of those latest target-detail amounts; the repeated
    request-total field is intentionally not used because non-target items may be
    present in the same request.
    """
    latest = _latest_shopping_change_rows(list(rows or []))
    grouped = {}
    for row in latest:
        grouped.setdefault(_request_group_key(row), []).append(dict(row))

    result = []
    for key, request_rows in grouped.items():
        representative = max(
            request_rows,
            key=lambda row: (
                _change_order_rank(row.get("delivery_change_order")),
                1
                if str(row.get("is_final_delivery_request") or "").upper() == "Y"
                else 0,
                str(row.get("source_date") or ""),
                str(row.get("fetched_at") or ""),
                str(row.get("source_key") or ""),
            ),
        )
        item = dict(representative)
        request_no = (
            str(representative.get("delivery_req_no") or "").strip()
            if key[0] == "REQUEST"
            else ""
        )
        categories = sorted({
            str(row.get("primary_category") or "").upper()
            for row in request_rows
            if str(row.get("primary_category") or "").upper() in TARGET_CATEGORIES
        })
        dates = sorted({
            str(row.get("source_date") or "").strip()
            for row in request_rows
            if str(row.get("source_date") or "").strip()
        })
        integrity_issues = (
            _request_integrity_issues(request_rows)
            if request_no
            else []
        )
        item["source_key"] = (
            f"REQUEST:{request_no}"
            if request_no
            else str(representative.get("source_key") or "")
        )
        item["delivery_req_no"] = request_no
        item["source_date"] = dates[0] if dates else ""
        item["request_integrity_valid"] = not integrity_issues
        item["request_integrity_status"] = (
            "VALID"
            if not integrity_issues
            else "EXCLUDED_CONFLICT"
        )
        item["request_integrity_issues"] = integrity_issues
        item["demand_org"] = (
            _single_raw_value(request_rows, "demand_org")
            or str(representative.get("demand_org") or "")
        )
        item["vendor_bizno"] = (
            _single_raw_value(
                request_rows,
                "vendor_bizno",
                digits_only=True,
            )
            or str(representative.get("vendor_bizno") or "")
        )
        item["contract_no"] = (
            _single_raw_value(request_rows, "contract_no")
            or str(representative.get("contract_no") or "")
        )
        item["primary_categories"] = categories
        item["primary_category"] = (
            categories[0] if len(categories) == 1 else "MIXED_TARGET"
        )
        item["detail_seq"] = ""
        item["detail_item_name"] = _joined_distinct(
            request_rows, "detail_item_name"
        )
        item["item_name"] = _joined_distinct(request_rows, "item_name")
        item["model_name"] = _joined_distinct(request_rows, "model_name")
        item["vendor_name"] = _joined_distinct(request_rows, "vendor_name")
        item["amount"] = sum(
            int(row.get("amount") or 0)
            for row in request_rows
        )
        item["quantity"] = sum(
            float(row.get("quantity") or 0)
            for row in request_rows
        )
        item["unit_price"] = 0
        item["unit_price_basis"] = "REQUEST_AGGREGATE_NOT_APPLICABLE"
        item["request_detail_rows"] = len(request_rows)
        item["amount_basis"] = "SUM_LATEST_TARGET_DETAIL_ITEM_AMOUNT"
        item["delivery_req_total_amount"] = max(
            (int(row.get("delivery_req_total_amount") or 0) for row in request_rows),
            default=0,
        )
        result.append(item)

    result.sort(
        key=lambda row: (
            str(row.get("source_date") or ""),
            str(row.get("delivery_req_no") or ""),
            str(row.get("source_key") or ""),
        ),
        reverse=True,
    )
    return result


def _request_page_detail_rows(
    *,
    categories=TARGET_CATEGORIES,
    query="",
    region="",
    start_date="",
    end_date="",
    limit=200,
    offset=0,
):
    """Read complete detail sets for a request-level page from normalized storage."""
    selection_where, selection_params = _shopping_filter_parts(
        categories=categories,
        query=query,
        region=region,
        start_date=start_date,
        end_date=end_date,
        include_inactive=False,
    )
    if not selection_where:
        return [], {
            "pagination_basis": "DELIVERY_REQUEST",
            "request_boundary_complete": True,
            "request_selection_strategy": "EMPTY",
            "detail_rows_scanned": 0,
            "request_keys_selected": 0,
        }

    page_offset = max(0, int(offset or 0))
    if page_offset > MAX_REQUEST_PAGE_OFFSET:
        raise ValueError("SHOPPING_REQUEST_OFFSET_TOO_LARGE")

    page_clause = ""
    if limit is not None:
        page_clause = "LIMIT ? OFFSET ?"

    db_backend = backend_name()
    if db_backend == "POSTGRESQL":
        # PostgreSQL can choose one latest representative row per delivery request
        # directly from the request-first index. This avoids GROUP BY CASE + MAX
        # across the entire yearly detail population. Empty request numbers remain
        # source-key identities and are unioned back before the small final sort.
        request_where = " AND ".join(selection_where)
        source_where = " AND ".join(selection_where)
        key_sql = f"""
            WITH request_latest AS (
                SELECT DISTINCT ON (delivery_req_no)
                       'REQUEST:' || delivery_req_no AS request_key,
                       source_date AS sort_date,
                       updated_at AS sort_updated_at,
                       source_key AS sort_source_key
                FROM shopping_records
                WHERE {request_where}
                  AND delivery_req_no<>''
                ORDER BY delivery_req_no,
                         source_date DESC,
                         updated_at DESC,
                         source_key DESC
            ),
            source_rows AS (
                SELECT 'SOURCE:' || source_key AS request_key,
                       source_date AS sort_date,
                       updated_at AS sort_updated_at,
                       source_key AS sort_source_key
                FROM shopping_records
                WHERE {source_where}
                  AND delivery_req_no=''
            )
            SELECT request_key,sort_date,sort_updated_at,sort_source_key
            FROM (
                SELECT * FROM request_latest
                UNION ALL
                SELECT * FROM source_rows
            ) selected
            ORDER BY sort_date DESC,sort_updated_at DESC,sort_source_key DESC
            {page_clause}
        """
        page_params = list(selection_params) + list(selection_params)
        selection_strategy = "POSTGRES_DISTINCT_ON"
    else:
        logical_key = (
            "CASE WHEN COALESCE(TRIM(delivery_req_no),'')<>'' "
            "THEN 'REQUEST:' || TRIM(delivery_req_no) "
            "ELSE 'SOURCE:' || source_key END"
        )
        key_sql = f"""
            SELECT {logical_key} AS request_key,
                   MAX(source_date) AS sort_date,
                   MAX(updated_at) AS sort_updated_at,
                   MAX(source_key) AS sort_source_key
            FROM shopping_records
            WHERE {' AND '.join(selection_where)}
            GROUP BY {logical_key}
            ORDER BY sort_date DESC,sort_updated_at DESC,sort_source_key DESC
            {page_clause}
        """
        page_params = list(selection_params)
        selection_strategy = "PORTABLE_GROUP_BY"

    if limit is not None:
        page_params.extend([
            max(1, min(int(limit), 5000)),
            page_offset,
        ])

    with connect() as conn:
        key_rows = conn.execute(
            key_sql,
            tuple(page_params),
        ).fetchall()

    keys = [str(row["request_key"] or "") for row in key_rows]
    if limit is None and page_offset > 0:
        keys = keys[page_offset:]
    if not keys:
        return [], {
            "pagination_basis": "DELIVERY_REQUEST",
            "request_boundary_complete": True,
            "request_selection_strategy": selection_strategy,
            "detail_rows_scanned": 0,
            "request_keys_selected": 0,
        }

    # Once a request key is selected, fetch every target detail row belonging to
    # it. Do not reapply query text here: a query may match only one detail but the
    # request amount must include all target details.
    detail_where, detail_params = _shopping_filter_parts(
        categories=categories,
        query="",
        region=region,
        start_date=start_date,
        end_date=end_date,
        include_inactive=False,
    )
    detail_rows = []
    chunk_size = 400
    with connect() as conn:
        for chunk_start in range(0, len(keys), chunk_size):
            chunk = keys[chunk_start:chunk_start + chunk_size]
            request_nos = [
                key[len("REQUEST:"):]
                for key in chunk
                if key.startswith("REQUEST:")
            ]
            source_keys = [
                key[len("SOURCE:"):]
                for key in chunk
                if key.startswith("SOURCE:")
            ]
            key_where = []
            key_params = []
            if request_nos:
                key_where.append(
                    "delivery_req_no IN ("
                    + ",".join("?" for _ in request_nos)
                    + ")"
                )
                key_params.extend(request_nos)
            if source_keys:
                key_where.append(
                    "(delivery_req_no='' AND source_key IN ("
                    + ",".join("?" for _ in source_keys)
                    + "))"
                )
                key_params.extend(source_keys)
            if not key_where:
                continue
            rows = conn.execute(
                f"""SELECT * FROM shopping_records
                    WHERE {' AND '.join(detail_where)}
                      AND ({' OR '.join(key_where)})
                    ORDER BY source_date DESC,updated_at DESC,source_key DESC""",
                tuple(list(detail_params) + key_params),
            ).fetchall()
            detail_rows.extend(rows)

    return _normalize_shopping_output(detail_rows), {
        "pagination_basis": "DELIVERY_REQUEST",
        "request_boundary_complete": True,
        "request_selection_strategy": selection_strategy,
        "detail_rows_scanned": len(detail_rows),
        "request_keys_selected": len(keys),
    }

def shopping_request_rows(
    *,
    categories=TARGET_CATEGORIES,
    query="",
    region="",
    start_date="",
    end_date="",
    limit=200,
    offset=0,
    with_meta=False,
):
    """Read one boundary-safe page of actual shopping delivery requests."""
    if _uses_normalized_shopping_store():
        detail_rows, meta = _request_page_detail_rows(
            categories=categories,
            query=query,
            region=region,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        requests = shopping_request_rows_from_rows(detail_rows)
    else:
        # Legacy regression fixtures do not expose normalized request-key SQL.
        # Read the bounded date/region/category population first, aggregate complete
        # requests, then paginate at request level.
        detail_rows = shopping_rows(
            categories=categories,
            query="",
            region=region,
            start_date=start_date,
            end_date=end_date,
            limit=None,
            offset=0,
        )
        requests = shopping_request_rows_from_rows(detail_rows)
        if query:
            requests = [
                row
                for row in requests
                if _match_query(
                    (
                        row.get("source_key"),
                        row.get("delivery_req_no"),
                        row.get("delivery_req_name"),
                        row.get("detail_item_name"),
                        row.get("item_name"),
                        row.get("model_name"),
                        row.get("demand_org"),
                        row.get("vendor_name"),
                        row.get("contract_no"),
                    ),
                    query,
                )
            ]
        start = max(0, int(offset))
        if limit is None:
            requests = requests[start:]
        else:
            size = max(1, min(int(limit), 5000))
            requests = requests[start:start + size]
        meta = {
            "pagination_basis": "DELIVERY_REQUEST",
            "request_boundary_complete": True,
            "detail_rows_scanned": len(detail_rows),
            "request_keys_selected": len(requests),
        }

    meta = dict(meta)
    meta["requests_returned"] = len(requests)
    if with_meta:
        return requests, meta
    return requests

def _new_vendor(name, bizno):
    return {
        "vendor_name": str(name or "").strip(),
        "vendor_bizno": _bizno(bizno),
        "shopping_rows": 0,
        "shopping_amount": 0,
        "demand_orgs": set(),
        "categories": set(),
    }


def _iter_latest_normalized_vendor_rows(*, region=""):
    """Stream latest active target rows without materializing shopping history."""
    where, params = _shopping_filter_parts(
        categories=TARGET_CATEGORIES,
        query="",
        region=region,
        include_inactive=False,
    )
    if not where:
        return

    sql = f"""SELECT source_key,source_date,fetched_at,primary_category,
                     delivery_req_no,detail_seq,delivery_change_order,
                     is_final_delivery_request,demand_org,vendor_name,
                     vendor_bizno,amount,delivery_req_total_amount
              FROM shopping_records
              WHERE {' AND '.join(where)}
              ORDER BY delivery_req_no,detail_seq,source_key"""

    current_logical = None
    best_row = None
    best_rank = None
    with connect() as conn:
        cursor = conn.execute_streaming(
            sql,
            tuple(params),
            max_row_buffer=250,
        )
        while True:
            batch = cursor.fetchmany(250)
            if not batch:
                break
            for row in batch:
                request_no = str(row.get("delivery_req_no") or "")
                detail_seq = str(row.get("detail_seq") or "")
                logical = (
                    ("REQUEST", request_no, detail_seq)
                    if request_no and detail_seq
                    else ("SOURCE", str(row.get("source_key") or ""))
                )
                if current_logical is not None and logical != current_logical:
                    if best_row is not None:
                        yield dict(best_row)
                    best_row = None
                    best_rank = None
                rank = (
                    _change_order_rank(row.get("delivery_change_order")),
                    1
                    if str(
                        row.get("is_final_delivery_request") or ""
                    ).upper() == "Y"
                    else 0,
                    str(row.get("source_date") or ""),
                    str(row.get("fetched_at") or ""),
                    str(row.get("source_key") or ""),
                )
                if best_row is None or rank > best_rank:
                    best_row = row
                    best_rank = rank
                current_logical = logical
    if best_row is not None:
        yield dict(best_row)


def _aggregate_vendor_rows(shopping):
    vendors = {}
    requests_with_item_amount = set()
    request_fallback_amount = {}

    for row in shopping:
        name = str(row.get("vendor_name") or "").strip()
        if not name:
            continue
        key = _vendor_identity(name, row.get("vendor_bizno"))
        item = vendors.setdefault(
            key,
            _new_vendor(name, row.get("vendor_bizno")),
        )
        if not item["vendor_bizno"] and row.get("vendor_bizno"):
            item["vendor_bizno"] = _bizno(row["vendor_bizno"])
        item["shopping_rows"] += 1

        amount = int(row.get("amount") or 0)
        request_no = str(row.get("delivery_req_no") or "")
        request_key = (key, request_no)
        if amount > 0:
            item["shopping_amount"] += amount
            if request_no:
                requests_with_item_amount.add(request_key)
        elif request_no:
            # Preserve the old first-row fallback rule without adding it until
            # the request is known to have no item-level amount anywhere.
            request_fallback_amount.setdefault(
                request_key,
                int(row.get("delivery_req_total_amount") or 0),
            )

        if row.get("demand_org"):
            item["demand_orgs"].add(str(row["demand_org"]))
        if row.get("primary_category"):
            item["categories"].add(str(row["primary_category"]))

    for request_key, fallback_amount in request_fallback_amount.items():
        if request_key in requests_with_item_amount:
            continue
        vendor_key, _request_no = request_key
        item = vendors.get(vendor_key)
        if item is not None:
            item["shopping_amount"] += int(fallback_amount or 0)
    return vendors


def _finalize_vendor_rows(vendors, *, query="", limit=200, offset=0):
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
        if q and q not in (
            item["vendor_name"] + " " + item["vendor_bizno"]
        ).casefold():
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


def vendor_rows(*, query="", region="", limit=200, offset=0):
    if _uses_normalized_shopping_store() and backend_name() == "POSTGRESQL":
        # Production web reads stream current normalized rows in bounded cursor
        # batches. Never build an unbounded shopping row list just to aggregate
        # vendors.
        shopping = _iter_latest_normalized_vendor_rows(region=region)
    else:
        # Tiny isolated SQLite/legacy fixtures retain the compatibility path.
        shopping = _latest_shopping_change_rows(
            shopping_rows(
                categories=TARGET_CATEGORIES,
                region=region,
                limit=None,
            )
        )
    vendors = _aggregate_vendor_rows(shopping)
    return _finalize_vendor_rows(
        vendors,
        query=query,
        limit=limit,
        offset=offset,
    )

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
