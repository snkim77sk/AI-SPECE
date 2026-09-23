"""Read-only candidate links from organized budget projects to stored G2B notices.

This module does not collect source data and does not persist lifecycle links.  It
only compares already-stored/current RAW + classification state and returns
conservative candidates for analyst/UI review.

Default candidate rule:
    same fiscal year
    + exact organization evidence
    + same current post-RAW category
    + at least one distinctive project/title token in common
    + for institution-scoped education rows, direct institution/school evidence

The result is a candidate, not a claim that the budget row caused or funded the
notice.
"""
from __future__ import annotations

import json
import re

from db import connect
import budget_targets_vnext
from vnext_schema import CLASSIFIER_VERSION, ensure_vnext_schema

NOTICE_DATASETS = ("bid_notice_goods", "bid_notice_service")
TARGET_CATEGORIES = budget_targets_vnext.TARGET_CATEGORIES
PROCUREMENT_PROJECT_LAYERS = ("DETAIL_EXECUTION", "EDUCATION")


def is_procurement_project_row(row):
    """Return whether an organized budget row has project-level procurement identity."""
    if str(row.get("source_layer") or "") not in PROCUREMENT_PROJECT_LAYERS:
        return False
    return bool(
        str(row.get("project_code") or "").strip()
        or str(row.get("project_name") or "").strip()
    )

_GENERIC_TOKENS = frozenset({
    "사업", "공사", "용역", "구매", "물품", "설치", "교체", "개선", "정비",
    "제작", "납품", "공급", "발주", "입찰", "시행", "추진", "연간", "노후",
    "시설", "장비", "관련", "일원", "일식", "관급", "관급자재", "기타",
})

_DEMAND_ORG_CODE_FIELDS = (
    "dminsttCd", "demandInsttCd", "demandInsttCode",
)
_NOTICE_ORG_CODE_FIELDS = (
    "ntceInsttCd", "noticeInsttCd", "noticeInsttCode",
)
_DEMAND_ORG_NAME_FIELDS = (
    "dminsttNm", "demandInsttNm", "demandOrgName",
)
_NOTICE_ORG_NAME_FIELDS = (
    "ntceInsttNm", "noticeInsttNm", "noticeOrgName",
)
_ORG_CODE_FIELDS = _DEMAND_ORG_CODE_FIELDS + _NOTICE_ORG_CODE_FIELDS
_ORG_NAME_FIELDS = _DEMAND_ORG_NAME_FIELDS + _NOTICE_ORG_NAME_FIELDS
_NOTICE_NAME_FIELDS = ("bidNtceNm", "bidNoticeName")


def _payload(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _pick(payload, *names):
    for name in names:
        value = payload.get(name) if isinstance(payload, dict) else None
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _norm_org(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _tokens(value):
    result = set()
    for token in re.findall(r"[0-9A-Za-z가-힣]+", str(value or "").casefold()):
        token = token.strip()
        if len(token) < 2:
            continue
        if token in _GENERIC_TOKENS:
            continue
        if len(token) == 4 and token.isdigit() and token.startswith("20"):
            continue
        result.add(token)
    return result


def _notice_year(payload, source_date):
    value = _pick(payload, "bidNtceDt", "bidNoticeDate") or str(source_date or "")
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits[:4]) if len(digits) >= 4 else 0


def _org_match(budget_row, payload):
    """Prefer the notice demand institution; use notice institution only as fallback."""
    budget_code = str(budget_row.get("org_code") or "").strip()
    budget_name = _norm_org(budget_row.get("org_name"))

    demand_codes = {
        str(payload.get(name) or "").strip()
        for name in _DEMAND_ORG_CODE_FIELDS
        if str(payload.get(name) or "").strip()
    }
    demand_names = {
        _norm_org(payload.get(name))
        for name in _DEMAND_ORG_NAME_FIELDS
        if _norm_org(payload.get(name))
    }
    notice_codes = {
        str(payload.get(name) or "").strip()
        for name in _NOTICE_ORG_CODE_FIELDS
        if str(payload.get(name) or "").strip()
    }
    notice_names = {
        _norm_org(payload.get(name))
        for name in _NOTICE_ORG_NAME_FIELDS
        if _norm_org(payload.get(name))
    }

    source_codes, source_names = (
        (demand_codes, demand_names)
        if (demand_codes or demand_names)
        else (notice_codes, notice_names)
    )
    if budget_code and source_codes:
        if budget_code in source_codes:
            return "EXACT_ORG_CODE"
        return ""
    if budget_name and budget_name in source_names:
        return "EXACT_ORG_NAME"
    return ""


def _education_institution_match(budget_row, payload, notice_name):
    """Require direct school/institution evidence for institution-scoped education rows."""
    if str(budget_row.get("source_layer") or "") != "EDUCATION":
        return "NOT_APPLICABLE"

    institution_code = str(budget_row.get("institution_code") or "").strip()
    institution_name = _norm_org(budget_row.get("institution_name"))
    if not institution_code and not institution_name:
        return "NOT_APPLICABLE"

    source_codes = {
        str(payload.get(name) or "").strip()
        for name in _ORG_CODE_FIELDS
        if str(payload.get(name) or "").strip()
    }
    if institution_code and institution_code in source_codes:
        return "EXACT_INSTITUTION_CODE"

    source_names = {
        _norm_org(payload.get(name))
        for name in _ORG_NAME_FIELDS
        if _norm_org(payload.get(name))
    }
    if institution_name and institution_name in source_names:
        return "EXACT_INSTITUTION_NAME"

    normalized_notice = _norm_org(notice_name)
    if institution_name and institution_name in normalized_notice:
        return "INSTITUTION_NAME_IN_NOTICE"

    return ""


def _current_notice_rows(*, categories, classifier_version, minimum_confidence=0.0):
    selected = tuple(str(x).upper() for x in categories)
    if not selected:
        return []
    placeholders = ",".join("?" for _ in selected)
    dataset_placeholders = ",".join("?" for _ in NOTICE_DATASETS)
    params = [str(classifier_version), *NOTICE_DATASETS, *selected, float(minimum_confidence or 0.0)]
    with connect() as conn:
        ensure_vnext_schema(conn)
        rows = conn.execute(
            f"""SELECT r.dataset,r.source_key,r.source_date,r.payload_json,r.payload_sha256,
                       c.primary_category,c.subcategory,c.confidence,c.reason,c.classifier_version
                FROM raw_records r
                JOIN classifications c
                  ON c.entity_type=r.dataset AND c.entity_key=r.source_key
                 AND c.classifier_version=?
                 AND COALESCE(c.source_payload_sha256,'')=COALESCE(r.payload_sha256,'')
                WHERE r.dataset IN ({dataset_placeholders})
                  AND c.primary_category IN ({placeholders})
                  AND c.confidence>=?
                ORDER BY r.source_date DESC,r.id DESC""",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def budget_notice_candidates(*, fiscal_year=None, categories=None,
                             minimum_classification_confidence=0.0,
                             minimum_match_confidence=0.92,
                             classifier_version=None, limit=1000,
                             one_per_project=False):
    """Return conservative project-level budget -> bid-notice candidates.

    Only DETAIL_EXECUTION and EDUCATION rows are project-level procurement inputs.
    APPROPRIATION rows remain structural budget context and are never directly
    presented as a notice relation. Nothing is written.
    """
    version = classifier_version or CLASSIFIER_VERSION
    selected = tuple(str(x).upper() for x in (
        TARGET_CATEGORIES if categories is None else categories
    ) if str(x).strip())
    if not selected:
        return []

    budgets = [
        row for row in budget_targets_vnext.target_candidates(
            fiscal_year=fiscal_year,
            categories=selected,
            minimum_confidence=minimum_classification_confidence,
            classifier_version=version,
        )
        if is_procurement_project_row(row)
    ]
    notices = _current_notice_rows(
        categories=selected,
        classifier_version=version,
        minimum_confidence=minimum_classification_confidence,
    )

    # School/institution names are not globally unique. Preserve the existing exact-
    # name fallback when a name is unique in current stored budget rows, but require
    # parent education-office evidence when the same normalized name spans multiple
    # offices.
    education_name_parents = {}
    for row in budgets:
        if str(row.get("source_layer") or "") != "EDUCATION":
            continue
        institution_name = _norm_org(row.get("institution_name"))
        if not institution_name:
            continue
        parent = (
            str(row.get("org_code") or "").strip()
            or _norm_org(row.get("org_name"))
            or "UNKNOWN:" + str(row.get("raw_source_key") or "")
        )
        education_name_parents.setdefault(institution_name, set()).add(parent)
    ambiguous_education_names = {
        name for name, parents in education_name_parents.items()
        if len(parents) > 1
    }

    result = []
    for budget in budgets:
        budget_year = int(budget.get("fiscal_year") or 0)
        budget_tokens = _tokens(budget.get("project_name"))
        if not budget_year or not budget_tokens:
            continue
        for notice in notices:
            if str(notice.get("primary_category") or "").upper() != str(
                budget.get("primary_category") or ""
            ).upper():
                continue
            payload = _payload(notice.get("payload_json"))
            notice_year = _notice_year(payload, notice.get("source_date"))
            if notice_year != budget_year:
                continue

            notice_name = _pick(payload, *_NOTICE_NAME_FIELDS)
            institution_basis = _education_institution_match(
                budget, payload, notice_name
            )
            has_institution_identity = (
                str(budget.get("source_layer") or "") == "EDUCATION"
                and bool(
                    str(budget.get("institution_code") or "").strip()
                    or str(budget.get("institution_name") or "").strip()
                )
            )
            org_basis = _org_match(budget, payload)
            if has_institution_identity:
                if not institution_basis:
                    continue
                institution_name = _norm_org(budget.get("institution_name"))
                if institution_basis == "INSTITUTION_NAME_IN_NOTICE" and not org_basis:
                    continue
                if (
                    institution_basis == "EXACT_INSTITUTION_NAME"
                    and institution_name in ambiguous_education_names
                    and not org_basis
                ):
                    continue
            elif not org_basis:
                continue

            budget_subcategory = str(budget.get("subcategory") or "")
            notice_subcategory = str(notice.get("subcategory") or "")
            if budget_subcategory and notice_subcategory and budget_subcategory != notice_subcategory:
                continue

            shared = sorted(budget_tokens & _tokens(notice_name))
            if not shared:
                continue

            same_subcategory = (
                budget_subcategory
                and budget_subcategory == notice_subcategory
            )
            evidence_basis = org_basis or institution_basis
            confidence = 0.94 if evidence_basis in (
                "EXACT_ORG_CODE", "EXACT_INSTITUTION_CODE"
            ) else 0.92
            if len(shared) >= 2:
                confidence += 0.02
            if same_subcategory:
                confidence += 0.01
            confidence = min(confidence, 0.99)
            if confidence < float(minimum_match_confidence or 0):
                continue

            result.append({
                "budget_project_identity": str(budget.get("project_identity") or ""),
                "budget_raw_dataset": str(budget.get("raw_dataset") or ""),
                "budget_raw_source_key": str(budget.get("raw_source_key") or ""),
                "budget_source_layer": str(budget.get("source_layer") or ""),
                "budget_project_name": str(budget.get("project_name") or ""),
                "budget_org_code": str(budget.get("org_code") or ""),
                "budget_org_name": str(budget.get("org_name") or ""),
                "budget_institution_code": str(budget.get("institution_code") or ""),
                "budget_institution_name": str(budget.get("institution_name") or ""),
                "fiscal_year": budget_year,
                "budget_amount": int(budget.get("budget_amount") or 0),
                "remaining_amount": int(budget.get("remaining_amount") or 0),
                "primary_category": str(budget.get("primary_category") or ""),
                "budget_subcategory": str(budget.get("subcategory") or ""),
                "notice_dataset": str(notice.get("dataset") or ""),
                "notice_source_key": str(notice.get("source_key") or ""),
                "notice_name": notice_name,
                "notice_date": str(notice.get("source_date") or ""),
                "notice_org_name": _pick(payload, *_ORG_NAME_FIELDS),
                "notice_subcategory": str(notice.get("subcategory") or ""),
                "shared_project_tokens": shared,
                "organization_match": org_basis,
                "institution_match": institution_basis,
                "match_basis": (
                    f"{org_basis or institution_basis}"
                    "+EXACT_YEAR+EXACT_CATEGORY+PROJECT_TOKEN_OVERLAP"
                ),
                "match_confidence": confidence,
                "candidate_only": True,
                "persisted_link": False,
                "source_traffic": False,
            })
            if one_per_project:
                break

    result.sort(key=lambda row: (
        float(row["match_confidence"]),
        str(row["notice_date"]),
        int(row["remaining_amount"]),
        str(row["budget_project_name"]),
        str(row["notice_name"]),
    ), reverse=True)
    return result if limit is None else result[:max(1, int(limit))]


def budget_notice_link_summary(**kwargs):
    """Summarize current read-only candidate links without asserting causality."""
    rows = budget_notice_candidates(**kwargs)
    return {
        "candidate_links": len(rows),
        "budget_projects": len({row["budget_project_identity"] for row in rows}),
        "notices": len({(row["notice_dataset"], row["notice_source_key"]) for row in rows}),
        "candidate_only": True,
        "persisted_links": 0,
        "source_traffic": False,
        "source_collection_completeness_verified": False,
    }
