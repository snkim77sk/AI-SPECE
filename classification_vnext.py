"""Versioned deterministic classifier for G2B vNext.

Production 4.1 shopping applies these rules directly to the transient official
response while writing normalized ``shopping_records``; source JSON is not stored.
Legacy/test RAW-backed datasets may still run post-RAW classification. Classification
does not expand shopping storage eligibility: the exact lighting/pole storage scope
is enforced separately before normalized persistence.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from contextlib import nullcontext

import budget_pg_store
import budget_storage

from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema
from vnext_store import save_classification as save_compat_classification

# Backward-compatible symbol for legacy/test callers. Budget PostgreSQL writes use
# budget_pg_store.save_classification explicitly below.
save_classification = save_compat_classification

_TRUE_ENV = {"1", "true", "yes", "on"}


def _memory_checkpoint():
    if str(os.getenv("G2B_TEST_MODE", "0") or "0").strip().lower() in _TRUE_ENV:
        return
    import memory_guard

    memory_guard.cooperative_batch_checkpoint(timeout=5.0)


LIGHTING_DETAIL_ITEM_NOS = frozenset({
    "3911151502", "3911160302", "3911160304", "3911160501",
    "3911160802", "3911161102", "3911210201",
})
POLE_DETAIL_ITEM_NOS = frozenset({"3911152601", "3911152602", "3911152607"})
SOLAR_DETAIL_ITEM_NOS = frozenset({"2611160701", "3912110101"})

TEXT_FIELDS = {
    "budget": (
        "dbiz_nm", "dbizNm", "project_name", "projectName", "사업명", "세부사업명",
    ),
    "budget_appropriation": (
        "dbiz_nm", "dbizNm", "biz_nm", "bizNm", "project_name", "projectName",
        "fld_nm", "part_nm", "sect_nm", "acnt_dv_nm", "사업명", "세부사업명",
    ),
    "education_budget": (
        "project_name", "projectName", "business_name", "businessName", "bizNm", "bsnsNm",
        "dbiz_nm", "SAUP_NM", "사업명", "세부사업명", "단위사업명", "정책사업명",
        "programName", "policyBusinessName", "accountName", "itemName", "세목명", "과목명",
    ),
    "shopping_delivery": (
        "dtilPrdctClsfcNoNm", "dtilPrdctClsfcNm", "detailPrdctNm", "detailItemName",
        "prdctClsfcNoNm", "prdctClsfcNm", "prdctIdntNoNm", "prdctIdntNm",
        "itemName", "prdctNm", "modelNm", "modelName", "prdctSpecNm", "specNm",
        "dlvrReqNm", "cntrctNm", "contractNm", "deliveryReqNm", "bizNm", "dlvrReqSj",
    ),
    "bid_notice_goods": (
        "bidNtceNm", "bidNoticeName", "prdctClsfcNoNm", "dtilPrdctClsfcNoNm",
        "dtlPrdctClsfcNoNm", "mtrlClsfcNoNm",
    ),
    "bid_notice_service": (
        "bidNtceNm", "bidNoticeName", "bsnsDivNm", "sucsfbidMthdNm",
    ),
    "opening_result_service": ("bidNtceNm", "bidNoticeName"),
    "award_result_service": ("bidNtceNm", "bidNoticeName"),
    "contract_service": (
        "cntrctNm", "contractNm", "pubPrcrmntClsfcNm", "pubPrcrmntMidclsfcNm",
        "pubPrcrmntLrgclsfcNm", "bsnsDivNm",
    ),
}

FALLBACK_TEXT_FIELDS = (
    "bidNtceNm", "bidNoticeName", "cntrctNm", "contractNm", "dbiz_nm",
    "project_name", "prdctNm", "itemName", "detailItemName",
)

POLE_TERMS = (
    "가로등주", "보안등주", "조명등주", "도로조명등주", "스테인리스등주", "스텐등주",
)
LIGHTING_TERMS = (
    "led", "조명", "가로등", "보안등", "투광등", "터널등", "다운라이트", "경관등",
    "경관조명", "평판등", "실내등", "보행등", "도로조명", "조명기구", "등기구",
)
SOLAR_TERMS = ("태양광", "태양전지", "photovoltaic", "solar panel", "pv module")
ELECTRICAL_DESIGN_TERMS = ("전기설계", "전기 설계")
ELECTRICAL_SUPERVISION_TERMS = ("전기감리", "전기 감리")
ELECTRICAL_CONSTRUCTION_TERMS = ("전기공사", "전기 공사", "전기시설공사", "전기 시설공사")
ELECTRICAL_GENERAL_TERMS = ("전기설비", "전기 설비", "전기시설", "전기 시설")


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _text(payload, dataset=""):
    fields = TEXT_FIELDS.get(str(dataset or ""), FALLBACK_TEXT_FIELDS)
    parts = []
    for field in fields:
        value = payload.get(field) if isinstance(payload, dict) else None
        if value not in (None, ""):
            parts.append(str(value).strip())
    return " | ".join(part for part in parts if part).casefold()


def _has(text, terms):
    return any(str(term).casefold() in text for term in terms)


def _detail_item_no(payload):
    for key in ("dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo", "dtlPrdctClsfcNo"):
        value = _digits(payload.get(key) if isinstance(payload, dict) else "")
        if value:
            return value
    return ""


def _lighting_subcategory(text):
    if _has(text, SOLAR_TERMS):
        return "SOLAR_LIGHTING"
    checks = (
        (("가로등", "도로조명"), "STREET_LIGHT"),
        (("보안등",), "SECURITY_LIGHT"),
        (("투광등",), "FLOOD_LIGHT"),
        (("터널등",), "TUNNEL_LIGHT"),
        (("다운라이트",), "DOWNLIGHT"),
        (("경관등", "경관조명"), "LANDSCAPE_LIGHT"),
        (("평판등", "실내등"), "INDOOR_LIGHT"),
    )
    for terms, name in checks:
        if _has(text, terms):
            return name
    return "LIGHTING_GENERAL"


def classify_payload(dataset, payload):
    """Return a deterministic classification dict without mutating storage."""
    payload = payload if isinstance(payload, dict) else {}
    detail_no = _detail_item_no(payload)
    text = _text(payload, dataset)

    if detail_no in POLE_DETAIL_ITEM_NOS:
        return {"primary_category": "POLE", "subcategory": "LIGHTING_POLE", "confidence": 1.0,
                "reason": f"exact detail item {detail_no}"}
    if detail_no in LIGHTING_DETAIL_ITEM_NOS:
        return {"primary_category": "LIGHTING", "subcategory": _lighting_subcategory(text), "confidence": 1.0,
                "reason": f"exact detail item {detail_no}"}
    if detail_no in SOLAR_DETAIL_ITEM_NOS:
        return {"primary_category": "SOLAR", "subcategory": "SOLAR_PRODUCT", "confidence": 1.0,
                "reason": f"exact detail item {detail_no}"}

    if _has(text, POLE_TERMS):
        return {"primary_category": "POLE", "subcategory": "LIGHTING_POLE", "confidence": 0.96,
                "reason": "post-RAW pole terminology"}

    lighting = _has(text, LIGHTING_TERMS)
    solar = _has(text, SOLAR_TERMS)
    if lighting:
        return {"primary_category": "LIGHTING", "subcategory": _lighting_subcategory(text), "confidence": 0.90,
                "reason": "post-RAW lighting terminology"}
    if solar:
        return {"primary_category": "SOLAR", "subcategory": "SOLAR_GENERAL", "confidence": 0.90,
                "reason": "post-RAW solar terminology"}

    if _has(text, ELECTRICAL_DESIGN_TERMS):
        return {"primary_category": "ELECTRICAL", "subcategory": "ELECTRICAL_DESIGN", "confidence": 0.90,
                "reason": "post-RAW electrical design terminology"}
    if _has(text, ELECTRICAL_SUPERVISION_TERMS):
        return {"primary_category": "ELECTRICAL", "subcategory": "ELECTRICAL_SUPERVISION", "confidence": 0.90,
                "reason": "post-RAW electrical supervision terminology"}
    if _has(text, ELECTRICAL_CONSTRUCTION_TERMS):
        return {"primary_category": "ELECTRICAL", "subcategory": "ELECTRICAL_CONSTRUCTION", "confidence": 0.90,
                "reason": "post-RAW electrical construction terminology"}
    if _has(text, ELECTRICAL_GENERAL_TERMS):
        return {"primary_category": "ELECTRICAL", "subcategory": "ELECTRICAL_GENERAL", "confidence": 0.80,
                "reason": "post-RAW electrical terminology"}

    return {"primary_category": "OTHER", "subcategory": "", "confidence": 0.55,
            "reason": "no post-RAW target-domain rule matched"}


def _batch_rows(dataset, version, last_id, size, force=False):
    with connect() as conn:
        ensure_vnext_schema(conn)
        if force:
            return conn.execute(
                "SELECT id,source_key,payload_json,payload_sha256 FROM raw_records "
                "WHERE dataset=? AND id>? ORDER BY id LIMIT ?",
                (str(dataset), int(last_id), int(size)),
            ).fetchall()
        return conn.execute(
            "SELECT r.id,r.source_key,r.payload_json,r.payload_sha256 FROM raw_records r "
            "LEFT JOIN classifications c ON c.entity_type=r.dataset "
            "AND c.entity_key=r.source_key AND c.classifier_version=? "
            "WHERE r.dataset=? AND r.id>? "
            "AND (c.id IS NULL OR COALESCE(c.source_payload_sha256,'') <> COALESCE(r.payload_sha256,'')) "
            "ORDER BY r.id LIMIT ?",
            (str(version), str(dataset), int(last_id), int(size)),
        ).fetchall()


def _pending_classification_keys(dataset, version, current_hashes, *, force=False):
    name = str(dataset)
    pending = {
        source_key
        for (row_dataset, source_key), _digest in current_hashes.items()
        if row_dataset == name
    }
    if force or not pending:
        return pending

    with connect() as conn:
        ensure_vnext_schema(conn)
        cursor = conn.execute(
            """SELECT entity_key,source_payload_sha256
               FROM classifications
               WHERE entity_type=? AND classifier_version=?
               ORDER BY id""",
            (name, str(version)),
        )
        while True:
            rows = cursor.fetchmany(2000)
            if not rows:
                break
            for row in rows:
                source_key = str(row["entity_key"])
                if current_hashes.get((name, source_key), "") == str(
                    row["source_payload_sha256"] or ""
                ):
                    pending.discard(source_key)
    return pending

def classify_dataset(
    dataset,
    *,
    classifier_version=None,
    batch_size=1000,
    force=False,
    max_batches=None,
):
    # Production and LOCAL_COLLECTOR 4.1 shopping are classified from the
    # transient source row while normalized into shopping_records. Only ordinary
    # test fixtures retain the legacy post-RAW shopping classification path.
    test_mode = str(os.getenv("G2B_TEST_MODE", "0") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    local_collector = False
    if test_mode and str(dataset) == "shopping_delivery":
        import runtime_role
        local_collector = runtime_role.is_local_collector()
    if (
        str(dataset) == "shopping_delivery"
        and (not test_mode or local_collector)
    ):
        return {
            "dataset": "shopping_delivery",
            "classifier_version": classifier_version or CLASSIFIER_VERSION,
            "classified": 0,
            "counts": {},
            "storage": "NORMALIZED_AT_INGEST",
            "payload_rows_loaded": 0,
        }
    """Classify new/changed RAW; budget RAW may live in PostgreSQL."""
    version = classifier_version or CLASSIFIER_VERSION

    if str(dataset) in budget_storage.BUDGET_DATASETS and budget_storage.using_postgres():
        size = max(1, min(int(batch_size), 5000))
        classified = 0
        counts = Counter()
        batches_done = 0
        batch_limit = (
            None
            if max_batches is None
            else max(1, int(max_batches))
        )
        with connect() as conn:
            ensure_vnext_schema(conn)

        current_rows_scanned = budget_pg_store.current_state_count(
            str(dataset)
        )
        for pending_keys in budget_pg_store.pending_classification_key_batches(
            str(dataset),
            version,
            batch_size=size,
            force=bool(force),
        ):
            if batch_limit is not None and batches_done >= batch_limit:
                break
            _memory_checkpoint()
            for batch in budget_storage.current_raw_for_keys(
                str(dataset), pending_keys, batch_size=size
            ):
                if batch_limit is not None and batches_done >= batch_limit:
                    break
                prepared = []
                for raw in batch:
                    source_key = str(raw["source_key"])
                    payload_sha256 = str(raw["payload_sha256"] or "")
                    payload = raw.get("payload")
                    if not isinstance(payload, dict):
                        try:
                            payload = json.loads(raw.get("payload_json") or "{}")
                        except (TypeError, ValueError):
                            payload = {}
                    result = classify_payload(dataset, payload)
                    prepared.append((source_key, payload_sha256, result))

                if not prepared:
                    continue
                pg_engine, _pg_tables = budget_pg_store._engine_and_tables()
                # The worker already holds an advisory-lease connection.
                # App and budget share one PostgreSQL 1+1 pool, so nested
                # independent PG + app checkouts would exhaust it.
                # Reuse the app adapter's PostgreSQL connection for both
                # classification writes within one atomic transaction.
                # SQLite test fixtures retain their separate budget engine.
                with connect() as conn:
                    shared_pg_conn = getattr(conn, "_conn", None)
                    with (
                        nullcontext(shared_pg_conn)
                        if shared_pg_conn is not None
                        else pg_engine.begin()
                    ) as pg_conn:
                        conn.execute("BEGIN IMMEDIATE")
                        for source_key, payload_sha256, result in prepared:
                            budget_pg_store.save_classification(
                                str(dataset),
                                source_key,
                                result["primary_category"],
                                subcategory=result["subcategory"],
                                confidence=result["confidence"],
                                reason=result["reason"],
                                classifier_version=version,
                                source_payload_sha256=payload_sha256,
                                _conn=pg_conn,
                            )
                            # Keep the application-side compatibility classification
                            # synchronized until all older analysis readers are migrated
                            # to the budget PostgreSQL classification table.
                            save_compat_classification(
                                str(dataset),
                                source_key,
                                result["primary_category"],
                                subcategory=result["subcategory"],
                                confidence=result["confidence"],
                                reason=result["reason"],
                                classifier_version=version,
                                source_payload_sha256=payload_sha256,
                                _conn=conn,
                            )
                            counts[result["primary_category"]] += 1
                            classified += 1
                batches_done += 1

        return {
            "dataset": str(dataset),
            "classifier_version": version,
            "classified": classified,
            "counts": dict(sorted(counts.items())),
            "raw_backend": "POSTGRESQL",
            "batch_size": size,
            "batches_done": batches_done,
            "max_batches": batch_limit,
            "batch_limit_reached": bool(
                batch_limit is not None and batches_done >= batch_limit
            ),
            "current_rows_scanned": current_rows_scanned,
            "payload_rows_loaded": classified,
            "pending_key_materialization": "BOUNDED_KEYSET_BATCHES",
            "classification_storage":
                "POSTGRESQL_PRIMARY_PLUS_APP_COMPATIBILITY",
        }

    version = classifier_version or CLASSIFIER_VERSION
    size = max(1, int(batch_size))
    last_id = 0
    classified = 0
    counts = Counter()
    batches_done = 0
    batch_limit = (
        None
        if max_batches is None
        else max(1, int(max_batches))
    )

    while True:
        if batch_limit is not None and batches_done >= batch_limit:
            break
        _memory_checkpoint()
        rows = _batch_rows(dataset, version, last_id, size, force=bool(force))
        if not rows:
            break

        prepared = []
        for row in rows:
            last_id = int(row["id"])
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            result = classify_payload(dataset, payload)
            prepared.append((
                str(row["source_key"]),
                result,
                str(row["payload_sha256"] or ""),
            ))

        # One transaction per batch instead of one connection/commit per RAW row.
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for source_key, result, payload_sha256 in prepared:
                save_classification(
                    str(dataset), source_key, result["primary_category"],
                    subcategory=result["subcategory"], confidence=result["confidence"],
                    reason=result["reason"], classifier_version=version,
                    source_payload_sha256=payload_sha256,
                    _conn=conn,
                )
                counts[result["primary_category"]] += 1
                classified += 1
        batches_done += 1

    return {"dataset": str(dataset), "classifier_version": version,
            "classified": classified, "counts": dict(sorted(counts.items())),
            "batches_done": batches_done, "max_batches": batch_limit,
            "batch_limit_reached": bool(
                batch_limit is not None and batches_done >= batch_limit
            )}


def raw_datasets():
    with connect() as conn:
        ensure_vnext_schema(conn)
        return [str(row["dataset"]) for row in conn.execute(
            "SELECT DISTINCT dataset FROM raw_records ORDER BY dataset"
        ).fetchall()]


def classify_all(*, datasets=None, classifier_version=None, batch_size=1000, force=False):
    selected = list(datasets) if datasets is not None else raw_datasets()
    results = [classify_dataset(
        dataset, classifier_version=classifier_version, batch_size=batch_size, force=force,
    ) for dataset in selected]
    return {
        "classifier_version": classifier_version or CLASSIFIER_VERSION,
        "dataset_count": len(results),
        "classified": sum(item["classified"] for item in results),
        "datasets": results,
    }
