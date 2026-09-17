"""Read-only analysis projection on top of vNext RAW + versioned classifications.

This layer does not create a second source of truth. RAW remains authoritative,
classification remains versioned, and normalized award/contract facts are joined at
query time. ``categories=None`` means *all* classified rows, including ``OTHER``.
Multiple official executions/rebids under one notice remain separate analysis rows.
"""
from __future__ import annotations

import json

from db import connect
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

TARGET_CATEGORIES = ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")


def classification_coverage(*, datasets=None, classifier_version=None):
    """Measure classification coverage only for RAW already present in vNext storage.

    This function deliberately does not assert that an official source was fully
    collected. Source-collection completeness requires receipt/stability proof and is
    owned by the collection layer, not by analysis/classification coverage.
    """
    version = classifier_version or CLASSIFIER_VERSION
    selected = set(str(x) for x in datasets) if datasets is not None else None
    with connect() as conn:
        ensure_vnext_schema(conn)
        raw_rows = conn.execute(
            "SELECT dataset,COUNT(*) AS n FROM raw_records GROUP BY dataset ORDER BY dataset"
        ).fetchall()
        class_rows = conn.execute(
            """SELECT r.dataset AS entity_type,c.primary_category,COUNT(*) AS n
               FROM raw_records r
               JOIN classifications c
                 ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                AND c.classifier_version=?
                AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')
               GROUP BY r.dataset,c.primary_category
               ORDER BY r.dataset,c.primary_category""",
            (version,),
        ).fetchall()

    raw_counts = {str(r["dataset"]): int(r["n"]) for r in raw_rows
                  if selected is None or str(r["dataset"]) in selected}
    by_dataset = {
        name: {
            "raw": count,
            "classified": 0,
            "missing": count,
            "has_raw_records": count > 0,
            "categories": {},
        }
        for name, count in raw_counts.items()
    }
    for row in class_rows:
        dataset = str(row["entity_type"])
        if dataset not in by_dataset:
            continue
        category = str(row["primary_category"])
        count = int(row["n"])
        by_dataset[dataset]["categories"][category] = count
        by_dataset[dataset]["classified"] += count
    for data in by_dataset.values():
        data["missing"] = max(0, int(data["raw"]) - int(data["classified"]))
        data["classification_complete_for_current_raw"] = data["missing"] == 0

    raw_total = sum(item["raw"] for item in by_dataset.values())
    classified_total = sum(item["classified"] for item in by_dataset.values())
    return {
        "classifier_version": version,
        "coverage_scope": "CURRENT_STORED_RAW_ONLY",
        "raw_total": raw_total,
        "classified_total": classified_total,
        "missing_total": max(0, raw_total - classified_total),
        "has_raw_records": raw_total > 0,
        "classification_complete_for_current_raw": raw_total == classified_total,
        "source_collection_completeness_verified": False,
        "source_collection_completeness_reason": "NOT_EVALUATED_BY_CLASSIFICATION_COVERAGE",
        "datasets": by_dataset,
    }


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


def service_lifecycle_rows(*, categories=None, source_keys=None, classifier_version=None, limit=1000, offset=0):
    """Project classified service notices with normalized execution-level lifecycle facts.

    Passing no categories returns all current-version classifications. Target-domain
    filtering is therefore always explicit and happens only in this analysis layer.
    A notice with multiple official executions/rebids may appear in multiple rows.
    """
    version = classifier_version or CLASSIFIER_VERSION
    params = [version]
    where = [
        "c.entity_type='bid_notice_service'",
        "c.classifier_version=?",
        "COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')",
    ]
    if categories is not None:
        cats = [str(x) for x in categories]
        if not cats:
            return []
        where.append("c.primary_category IN (%s)" % ",".join("?" for _ in cats))
        params.extend(cats)
    if source_keys is not None:
        keys = [str(x) for x in source_keys if str(x)]
        if not keys:
            return []
        where.append("r.source_key IN (%s)" % ",".join("?" for _ in keys))
        params.extend(keys)
    params.extend([max(1, int(limit)), max(0, int(offset))])

    sql = f"""
        SELECT r.source_key,r.source_date,r.payload_json,
               c.primary_category,c.subcategory,c.confidence,c.reason,c.classifier_version,
               a.source_key AS award_summary_key,
               a.opening_date,a.participant_count,a.first_rank_vendor,a.first_rank_bizno,
               a.first_rank_amount,a.final_vendor,a.final_vendor_bizno,a.final_award_amount,
               a.award_rate,a.contract_no,a.contract_vendor,a.contract_vendor_bizno,a.contract_amount,
               a.opening_raw_key,a.final_award_raw_key,a.contract_raw_key,
               (ro.payload_sha256=a.opening_payload_sha256 AND a.opening_payload_sha256<>'') AS opening_current,
               (rf.payload_sha256=a.final_award_payload_sha256 AND a.final_award_payload_sha256<>'') AS final_award_current,
               (rc.payload_sha256=a.contract_payload_sha256 AND a.contract_payload_sha256<>'') AS contract_current
        FROM classifications c
        JOIN raw_records r
          ON r.dataset=c.entity_type AND r.source_key=c.entity_key
        LEFT JOIN lifecycle_links l
          ON l.from_type='bid_notice' AND l.from_key=r.source_key
         AND l.to_type='award_summary' AND l.link_type='HAS_AWARD_EXECUTION' AND l.confidence>0
        LEFT JOIN award_results a ON a.source_key=l.to_key
        LEFT JOIN raw_records ro ON ro.dataset='opening_result_service' AND ro.source_key=a.opening_raw_key
        LEFT JOIN raw_records rf ON rf.dataset='award_result_service' AND rf.source_key=a.final_award_raw_key
        LEFT JOIN raw_records rc ON rc.dataset='contract_service' AND rc.source_key=a.contract_raw_key
        WHERE {' AND '.join(where)}
        ORDER BY r.source_date DESC,r.id DESC,a.id ASC
        LIMIT ? OFFSET ?
    """
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = conn.execute(sql, tuple(params)).fetchall()
        contract_map = {}
        from contract_projection import resolve_unique_final_award_key, resolve_notice_key
        for row in rows:
            notice = row['source_key']
            if notice in contract_map:
                continue
            records = conn.execute("""SELECT p.*,r.payload_json FROM vnext_contract_projection p
                JOIN raw_records r ON r.dataset='contract_service' AND r.source_key=p.raw_source_key
                  AND r.payload_sha256=p.payload_sha256
                JOIN lifecycle_links l ON l.from_type='bid_notice' AND l.from_key=p.notice_key
                  AND l.to_type='contract' AND l.to_key=p.contract_no AND l.link_type='HAS_CONTRACT' AND l.confidence>0
                WHERE p.notice_key=? ORDER BY p.contract_no,p.raw_source_key""", (notice,)).fetchall()
            execution = resolve_unique_final_award_key(notice, conn)
            contract_map[notice] = []
            for rec in records:
                if resolve_notice_key(_payload(rec['payload_json']), conn) != notice:
                    continue
                fact = json.loads(rec['facts_json'])
                fact.update(raw_source_key=rec['raw_source_key'], notice_key=notice,
                            award_summary_key=execution if rec['award_summary_key']==execution else '',
                            parties_valid=bool(rec['parties_valid']))
                contract_map[notice].append(fact)

    out = []
    for row in rows:
        payload = _payload(row["payload_json"])
        row = dict(row)
        groups = {
            'opening': {'opening_date': '', 'participant_count': 0, 'first_rank_vendor': '', 'first_rank_bizno': '', 'first_rank_amount': 0},
            'final_award': {'final_vendor': '', 'final_vendor_bizno': '', 'final_award_amount': 0, 'award_rate': 0.0},
            'contract': {'contract_no': '', 'contract_vendor': '', 'contract_vendor_bizno': '', 'contract_amount': 0},
        }
        stale = {}
        for group, defaults in groups.items():
            stale[group + '_stale'] = not bool(row[group + '_current']) and bool(
                row[group + '_raw_key'] or any(row[k] for k in defaults))
            if not row[group + '_current']:
                row.update(defaults)
        contracts = contract_map.get(row['source_key'], [])
        assigned = [c for c in contracts if c['award_summary_key'] and c['award_summary_key']==row['award_summary_key']]
        if len(assigned) != 1:
            stale['contract_stale'] = stale['contract_stale'] or bool(row['contract_no'])
            row.update(groups['contract'])
        else:
            row.update({k: assigned[0][k] for k in groups['contract']})
        out.append({
            **stale,
            'contracts': contracts,
            'contract_count': len(contracts),
            'multiple_contracts': len(contracts) > 1,
            "source_key": row["source_key"],
            "award_summary_key": row["award_summary_key"] or "",
            "source_date": row["source_date"],
            "classifier_version": row["classifier_version"],
            "primary_category": row["primary_category"],
            "subcategory": row["subcategory"],
            "classification_confidence": float(row["confidence"] or 0),
            "classification_reason": row["reason"],
            "notice_name": _pick(payload, "bidNtceNm", "bidNoticeName"),
            "notice_org": _pick(payload, "ntceInsttNm", "noticeInsttNm", "noticeOrgName"),
            "demand_org": _pick(payload, "dminsttNm", "demandInsttNm", "demandOrgName"),
            "notice_date": _pick(payload, "bidNtceDt", "bidNoticeDate"),
            "opening_date": row["opening_date"] or "",
            "participant_count": int(row["participant_count"] or 0),
            "first_rank_vendor": row["first_rank_vendor"] or "",
            "first_rank_bizno": row["first_rank_bizno"] or "",
            "first_rank_amount": int(row["first_rank_amount"] or 0),
            "final_vendor": row["final_vendor"] or "",
            "final_vendor_bizno": row["final_vendor_bizno"] or "",
            "final_award_amount": int(row["final_award_amount"] or 0),
            "award_rate": float(row["award_rate"] or 0),
            "contract_no": row["contract_no"] or "",
            "contract_vendor": row["contract_vendor"] or "",
            "contract_vendor_bizno": row["contract_vendor_bizno"] or "",
            "contract_amount": int(row["contract_amount"] or 0),
        })
    return out


def target_service_lifecycle_rows(**kwargs):
    """Convenience analysis view for the explicit post-classified target domains."""
    kwargs.pop("categories", None)
    return service_lifecycle_rows(categories=TARGET_CATEGORIES, **kwargs)
