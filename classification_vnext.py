"""Versioned post-RAW classifier for the independent G2B vNext data lake.

Nothing in this module participates in collection eligibility. Every source row is
already preserved in ``raw_records`` before these rules run. Rows that do not match
a target domain are classified as ``OTHER`` rather than deleted or skipped.
"""
from __future__ import annotations

import json
from collections import Counter

from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema
from vnext_store import save_classification

# Existing production detail-item knowledge is reused only *after* RAW preservation.
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
                "SELECT id,source_key,payload_json FROM raw_records "
                "WHERE dataset=? AND id>? ORDER BY id LIMIT ?",
                (str(dataset), int(last_id), int(size)),
            ).fetchall()
        return conn.execute(
            "SELECT r.id,r.source_key,r.payload_json FROM raw_records r "
            "LEFT JOIN classifications c ON c.entity_type=r.dataset "
            "AND c.entity_key=r.source_key AND c.classifier_version=? "
            "WHERE r.dataset=? AND r.id>? AND c.id IS NULL "
            "ORDER BY r.id LIMIT ?",
            (str(version), str(dataset), int(last_id), int(size)),
        ).fetchall()


def classify_dataset(dataset, *, classifier_version=None, batch_size=1000, force=False):
    """Classify RAW rows without deleting them; skip current-version rows by default."""
    version = classifier_version or CLASSIFIER_VERSION
    size = max(1, int(batch_size))
    last_id = 0
    classified = 0
    counts = Counter()

    while True:
        rows = _batch_rows(dataset, version, last_id, size, force=bool(force))
        if not rows:
            break
        for row in rows:
            last_id = int(row["id"])
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            result = classify_payload(dataset, payload)
            save_classification(
                str(dataset), str(row["source_key"]), result["primary_category"],
                subcategory=result["subcategory"], confidence=result["confidence"],
                reason=result["reason"], classifier_version=version,
            )
            counts[result["primary_category"]] += 1
            classified += 1
    return {"dataset": str(dataset), "classifier_version": version,
            "classified": classified, "counts": dict(sorted(counts.items()))}


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
