"""Read-only analysis projection on top of vNext RAW + current projections."""
from __future__ import annotations

import json

from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

TARGET_CATEGORIES = ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")


def classification_coverage(*, datasets=None, classifier_version=None):
    version = classifier_version or CLASSIFIER_VERSION
    selected = set(str(x) for x in datasets) if datasets is not None else None
    with connect() as conn:
        ensure_vnext_schema(conn)
        raw_rows = conn.execute("SELECT dataset,COUNT(*) AS n FROM raw_records GROUP BY dataset ORDER BY dataset").fetchall()
        class_rows = conn.execute(
            """SELECT r.dataset AS entity_type,c.primary_category,COUNT(*) AS n
               FROM raw_records r JOIN classifications c
                 ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                AND c.classifier_version=?
                AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')
               GROUP BY r.dataset,c.primary_category ORDER BY r.dataset,c.primary_category""",
            (version,)).fetchall()
    raw_counts = {str(r["dataset"]): int(r["n"]) for r in raw_rows
                  if selected is None or str(r["dataset"]) in selected}
    by_dataset = {name: {"raw": count, "classified": 0, "missing": count, "categories": {}}
                  for name, count in raw_counts.items()}
    for row in class_rows:
        dataset = str(row["entity_type"])
        if dataset not in by_dataset:
            continue
        category, count = str(row["primary_category"]), int(row["n"])
        by_dataset[dataset]["categories"][category] = count
        by_dataset[dataset]["classified"] += count
    for data in by_dataset.values():
        data["missing"] = max(0, data["raw"] - data["classified"])
        data["complete"] = data["missing"] == 0
    raw_total = sum(item["raw"] for item in by_dataset.values())
    classified_total = sum(item["classified"] for item in by_dataset.values())
    return {"classifier_version": version, "raw_total": raw_total,
            "classified_total": classified_total,
            "missing_total": max(0, raw_total - classified_total),
            "complete": raw_total == classified_total, "datasets": by_dataset}


def _payload(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _pick(payload, *names):
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return value
    return ""


def service_lifecycle_rows(*, categories=None, classifier_version=None, limit=1000, offset=0):
    version = classifier_version or CLASSIFIER_VERSION
    params = [version]
    where = ["c.entity_type='bid_notice_service'", "c.classifier_version=?",
             "COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')"]
    if categories is not None:
        cats = [str(x) for x in categories]
        if not cats:
            return []
        where.append("c.primary_category IN (%s)" % ",".join("?" for _ in cats))
        params.extend(cats)
    params.extend([max(1, int(limit)), max(0, int(offset))])
    sql = f"""
        SELECT r.source_key,r.source_date,r.payload_json,
               c.primary_category,c.subcategory,c.confidence,c.reason,c.classifier_version,
               a.source_key AS award_summary_key,
               a.opening_raw_key,a.final_award_raw_key,a.contract_raw_key,
               ro.normalized_at AS opening_normalized_at,
               rf.normalized_at AS final_normalized_at,
               rc.normalized_at AS contract_normalized_at,
               a.opening_date,a.participant_count,a.first_rank_vendor,a.first_rank_bizno,
               a.first_rank_amount,a.final_vendor,a.final_vendor_bizno,a.final_award_amount,
               a.award_rate,a.contract_no,a.contract_vendor,a.contract_vendor_bizno,a.contract_amount
        FROM classifications c
        JOIN raw_records r ON r.dataset=c.entity_type AND r.source_key=c.entity_key
        LEFT JOIN lifecycle_links l
          ON l.from_type='bid_notice' AND l.from_key=r.source_key
         AND l.to_type='award_summary' AND l.link_type='HAS_AWARD_EXECUTION' AND l.confidence>0
         AND EXISTS (SELECT 1 FROM award_results ax WHERE ax.source_key=l.to_key
                     AND (ax.opening_raw_key<>'' OR ax.final_award_raw_key<>'' OR ax.contract_raw_key<>''))
        LEFT JOIN award_results a ON a.source_key=l.to_key
        LEFT JOIN raw_records ro ON ro.dataset='opening_result_service' AND ro.source_key=a.opening_raw_key
        LEFT JOIN raw_records rf ON rf.dataset='award_result_service' AND rf.source_key=a.final_award_raw_key
        LEFT JOIN raw_records rc ON rc.dataset='contract_service' AND rc.source_key=a.contract_raw_key
        WHERE {' AND '.join(where)}
        ORDER BY r.source_date DESC,r.id DESC,a.id ASC LIMIT ? OFFSET ?
    """
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = conn.execute(sql, tuple(params)).fetchall()

    out = []
    for row in rows:
        payload = _payload(row["payload_json"])
        opening_current = bool(row["opening_raw_key"] and row["opening_normalized_at"])
        final_current = bool(row["final_award_raw_key"] and row["final_normalized_at"])
        contract_current = bool(row["contract_raw_key"] and row["contract_normalized_at"])
        out.append({
            "source_key": row["source_key"], "award_summary_key": row["award_summary_key"] or "",
            "source_date": row["source_date"], "classifier_version": row["classifier_version"],
            "primary_category": row["primary_category"], "subcategory": row["subcategory"],
            "classification_confidence": float(row["confidence"] or 0),
            "classification_reason": row["reason"],
            "notice_name": _pick(payload, "bidNtceNm", "bidNoticeName"),
            "notice_org": _pick(payload, "ntceInsttNm", "noticeInsttNm", "noticeOrgName"),
            "demand_org": _pick(payload, "dminsttNm", "demandInsttNm", "demandOrgName"),
            "notice_date": _pick(payload, "bidNtceDt", "bidNoticeDate"),
            "opening_current": opening_current, "final_award_current": final_current,
            "contract_current": contract_current,
            "opening_date": (row["opening_date"] or "") if opening_current else "",
            "participant_count": int(row["participant_count"] or 0) if opening_current else 0,
            "first_rank_vendor": (row["first_rank_vendor"] or "") if opening_current else "",
            "first_rank_bizno": (row["first_rank_bizno"] or "") if opening_current else "",
            "first_rank_amount": int(row["first_rank_amount"] or 0) if opening_current else 0,
            "final_vendor": (row["final_vendor"] or "") if final_current else "",
            "final_vendor_bizno": (row["final_vendor_bizno"] or "") if final_current else "",
            "final_award_amount": int(row["final_award_amount"] or 0) if final_current else 0,
            "award_rate": float(row["award_rate"] or 0) if final_current else 0.0,
            "contract_no": (row["contract_no"] or "") if contract_current else "",
            "contract_vendor": (row["contract_vendor"] or "") if contract_current else "",
            "contract_vendor_bizno": (row["contract_vendor_bizno"] or "") if contract_current else "",
            "contract_amount": int(row["contract_amount"] or 0) if contract_current else 0,
        })
    return out


def target_service_lifecycle_rows(**kwargs):
    kwargs.pop("categories", None)
    return service_lifecycle_rows(categories=TARGET_CATEGORIES, **kwargs)
