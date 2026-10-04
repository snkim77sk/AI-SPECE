"""Source-free compact evidence for budget -> service/work execution outcomes.

This module deliberately does NOT collect G2B sources. It accepts already-obtained
award rows (for example a future isolated export/import path), keeps only the small
official fields needed to prove execution, matches them conservatively to organized
budget projects, and persists only high-confidence compact evidence.

It is not an auction/bid prediction model. Participant lists, bid-rate distributions,
preliminary prices and other NO1-style prediction inputs are intentionally excluded.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

import admin_geography_v41
import budget_shopping_match_vnext
import budget_targets_vnext
from db import connect

SERVICE_AWARD = "SERVICE_AWARD"
ELECTRICAL_WORK_AWARD = "ELECTRICAL_WORK_AWARD"
LIGHTING_WORK_AWARD = "LIGHTING_WORK_AWARD"
EVIDENCE_TYPES = (
    SERVICE_AWARD,
    ELECTRICAL_WORK_AWARD,
    LIGHTING_WORK_AWARD,
)
BUSINESS_TYPES = ("service", "construction")
MIN_MATCH_CONFIDENCE = 0.92

_LIGHTING_TERMS = (
    "led", "엘이디", "조명", "가로등", "보안등", "방범등", "경관조명",
    "경관등", "투광등", "공원등", "보행등", "산책로조명", "등기구",
    "가로등주", "보안등주", "조명주", "등주",
)
_ELECTRICAL_TERMS = (
    "전기공사", "전기 공사", "전기설비", "전기 설비", "전력", "배전",
    "수배전", "전기", "전원",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_execution_evidence(
    evidence_key TEXT PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    source_business_type TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    notice_no TEXT NOT NULL,
    notice_order TEXT NOT NULL,
    bid_clsfc_no TEXT NOT NULL DEFAULT '',
    rebid_no TEXT NOT NULL DEFAULT '',
    award_date TEXT NOT NULL,
    award_org_code TEXT NOT NULL DEFAULT '',
    award_org_name TEXT NOT NULL DEFAULT '',
    award_title TEXT NOT NULL DEFAULT '',
    vendor_name TEXT NOT NULL DEFAULT '',
    vendor_bizno TEXT NOT NULL DEFAULT '',
    award_amount INTEGER NOT NULL DEFAULT 0,
    award_rate REAL NOT NULL DEFAULT 0,
    budget_project_identity TEXT NOT NULL,
    budget_raw_source_key TEXT NOT NULL DEFAULT '',
    budget_org_code TEXT NOT NULL DEFAULT '',
    budget_org_name TEXT NOT NULL DEFAULT '',
    budget_project_code TEXT NOT NULL DEFAULT '',
    budget_project_name TEXT NOT NULL DEFAULT '',
    budget_category TEXT NOT NULL DEFAULT '',
    organization_basis TEXT NOT NULL DEFAULT '',
    shared_identity_json TEXT NOT NULL DEFAULT '[]',
    match_confidence REAL NOT NULL DEFAULT 0,
    match_basis_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_budget_execution_evidence_year_type
    ON budget_execution_evidence(fiscal_year,evidence_type);
CREATE INDEX IF NOT EXISTS ix_budget_execution_evidence_project
    ON budget_execution_evidence(budget_project_identity,fiscal_year);
CREATE INDEX IF NOT EXISTS ix_budget_execution_evidence_source
    ON budget_execution_evidence(source_reference,evidence_type);
"""


def ensure_schema():
    with connect() as conn:
        conn.executescript(_SCHEMA)


def _pick(row, *names):
    if not isinstance(row, dict):
        return ""
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _integer(value):
    try:
        return int(round(float(str(value or 0).replace(",", "").strip())))
    except (TypeError, ValueError):
        return 0


def _number(value):
    try:
        return float(str(value or 0).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _date(value):
    digits = _digits(value)
    if len(digits) >= 8:
        try:
            day = dt.date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return ""
        return day.isoformat()
    text = str(value or "").strip()
    try:
        return dt.date.fromisoformat(text[:10]).isoformat()
    except (TypeError, ValueError):
        return ""


def _norm(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _merged_value(row, notice, *names):
    value = _pick(row, *names)
    if value:
        return value
    return _pick(notice or {}, *names)


def _award_org(row, notice):
    demand_code = _merged_value(
        row, notice, "dminsttCd", "demandInsttCd", "demandInsttCode",
        "award_org_code",
    )
    demand_name = _merged_value(
        row, notice, "dminsttNm", "demandInsttNm", "demandOrgName",
        "award_org_name",
    )
    if demand_code or demand_name:
        return demand_code, demand_name
    return (
        _merged_value(
            row, notice, "ntceInsttCd", "noticeInsttCd", "noticeInsttCode"
        ),
        _merged_value(
            row, notice, "ntceInsttNm", "noticeInsttNm", "noticeOrgName"
        ),
    )


def _title(row, notice):
    return _merged_value(
        row,
        notice,
        "bidNtceNm",
        "bidNoticeName",
        "notice_name",
        "ntceNm",
        "project_name",
    )


def _business_type(value):
    text = str(value or "").strip().lower()
    aliases = {
        "service": "service",
        "servc": "service",
        "용역": "service",
        "construction": "construction",
        "cnstwk": "construction",
        "work": "construction",
        "공사": "construction",
    }
    result = aliases.get(text, "")
    if result not in BUSINESS_TYPES:
        raise ValueError("business_type must be service or construction")
    return result


def _evidence_type(business_type, title):
    kind = _business_type(business_type)
    if kind == "service":
        return SERVICE_AWARD
    normalized = str(title or "").casefold().replace(" ", "")
    if any(str(term).casefold().replace(" ", "") in normalized for term in _LIGHTING_TERMS):
        return LIGHTING_WORK_AWARD
    if any(str(term).casefold().replace(" ", "") in normalized for term in _ELECTRICAL_TERMS):
        return ELECTRICAL_WORK_AWARD
    return ""


def compact_award_fact(row, *, business_type, notice=None):
    """Extract only execution-proof fields; raw payload is never returned or stored."""
    item = dict(row or {})
    notice_row = dict(notice or {})
    notice_no = _merged_value(item, notice_row, "bidNtceNo", "bidNoticeNo")
    notice_order = (
        _merged_value(item, notice_row, "bidNtceOrd", "bidNoticeOrd") or "000"
    )
    bid_clsfc_no = _merged_value(item, notice_row, "bidClsfcNo", "bidClsfNo")
    rebid_no = _merged_value(item, notice_row, "rbidNo", "rebidNo")
    award_date = _date(
        _merged_value(
            item,
            notice_row,
            "opengDt",
            "opengDate",
            "rgstDt",
            "sucsfbidDt",
            "award_date",
        )
    )
    org_code, org_name = _award_org(item, notice_row)
    title = _title(item, notice_row)
    kind = _business_type(business_type)
    evidence_type = _evidence_type(kind, title)

    vendor_name = _merged_value(
        item, notice_row, "bidwinnrNm", "fnlSucsfCorpNm", "vendor_name"
    )
    vendor_bizno = _digits(
        _merged_value(
            item,
            notice_row,
            "bidwinnrBizno",
            "fnlSucsfCorpBizno",
            "vendor_bizno",
        )
    )
    award_amount = _integer(
        _merged_value(item, notice_row, "sucsfbidAmt", "finalAwardAmount", "award_amount")
    )
    award_rate = _number(
        _merged_value(item, notice_row, "sucsfbidRate", "finalAwardRate", "award_rate")
    )

    problems = []
    if not all((notice_no, notice_order, bid_clsfc_no, rebid_no)):
        problems.append("MISSING_OFFICIAL_EXECUTION_IDENTITY")
    if not award_date:
        problems.append("MISSING_AWARD_DATE")
    if not (org_code or org_name):
        problems.append("MISSING_DEMAND_OR_NOTICE_ORG")
    if not title:
        problems.append("MISSING_NOTICE_TITLE")
    if not evidence_type:
        problems.append("OUT_OF_SCOPE_WORK_AWARD")
    if not (vendor_name or vendor_bizno or award_amount):
        problems.append("MISSING_FINAL_AWARD_FACT")

    if problems:
        return {
            "valid": False,
            "problems": problems,
            "source_business_type": kind,
            "evidence_type": evidence_type,
        }

    source_reference = (
        f"{notice_no}|{notice_order}|{bid_clsfc_no}|{rebid_no}"
    )
    return {
        "valid": True,
        "problems": [],
        "source_business_type": kind,
        "evidence_type": evidence_type,
        "source_reference": source_reference,
        "notice_no": notice_no,
        "notice_order": notice_order,
        "bid_clsfc_no": bid_clsfc_no,
        "rebid_no": rebid_no,
        "award_date": award_date,
        "fiscal_year": int(award_date[:4]),
        "award_org_code": org_code,
        "award_org_name": org_name,
        "award_title": title,
        "vendor_name": vendor_name,
        "vendor_bizno": vendor_bizno,
        "award_amount": award_amount,
        "award_rate": award_rate,
    }


def _organization_match(budget, fact):
    budget_org_code = str(budget.get("org_code") or "").strip()
    budget_org_name = str(budget.get("org_name") or "").strip()
    evidence_code = str(fact.get("award_org_code") or "").strip()
    evidence_name = str(fact.get("award_org_name") or "").strip()

    institution_code = str(budget.get("institution_code") or "").strip()
    institution_name = str(budget.get("institution_name") or "").strip()
    if institution_code and evidence_code and institution_code == evidence_code:
        return "EXACT_INSTITUTION_CODE"
    if institution_name and evidence_name and _norm(institution_name) == _norm(evidence_name):
        return "EXACT_INSTITUTION_NAME"

    if budget_org_code and evidence_code and budget_org_code == evidence_code:
        return "EXACT_ORG_CODE"
    if budget_org_name and evidence_name and _norm(budget_org_name) == _norm(evidence_name):
        return "EXACT_ORG_NAME"

    transition, details = admin_geography_v41.organization_transition_basis(
        budget_org=budget_org_name,
        shopping_org=evidence_name,
        budget_text=str(budget.get("project_name") or ""),
        shopping_text=str(fact.get("award_title") or ""),
        budget_date=str(
            budget.get("source_date")
            or budget.get("snapshot_date")
            or ""
        ),
        shopping_date=str(fact.get("award_date") or ""),
    )
    if transition:
        return transition + (
            ":" + ",".join(str(value) for value in details)
            if details else ""
        )
    return ""


def _org_rank(basis):
    text = str(basis or "")
    if text == "EXACT_INSTITUTION_CODE":
        return 5
    if text == "EXACT_ORG_CODE":
        return 4
    if text == "EXACT_INSTITUTION_NAME":
        return 3
    if text == "EXACT_ORG_NAME":
        return 2
    if text.startswith("ADMIN_TRANSITION_ORG_MATCH"):
        return 1
    return 0


def match_budget_project(budget, fact, *, minimum_confidence=MIN_MATCH_CONFIDENCE):
    """Return one conservative candidate, or None when identity is insufficient."""
    if not bool(fact.get("valid")):
        return None
    if str(budget.get("source_layer") or "") not in (
        "DETAIL_EXECUTION",
        "EDUCATION",
    ):
        return None
    category = str(budget.get("primary_category") or "").upper()
    if category not in set(budget_targets_vnext.TARGET_CATEGORIES):
        return None

    budget_year = int(budget.get("fiscal_year") or 0)
    if not budget_year or budget_year != int(fact.get("fiscal_year") or 0):
        return None

    organization_basis = _organization_match(budget, fact)
    if not organization_basis:
        return None

    shared_strong, shared_weak = (
        budget_shopping_match_vnext.shared_project_identity_forms(
            str(budget.get("project_name") or ""),
            str(fact.get("award_title") or ""),
        )
    )
    # Lighting/electrical domain words are deliberately weak/non-identity in the
    # shared matcher. Require at least one distinctive facility/locality/project
    # identity so "LED 조명 공사" at the same institution cannot match everything.
    if not shared_strong:
        return None

    org_rank = _org_rank(organization_basis)
    confidence = {
        5: 0.97,
        4: 0.96,
        3: 0.95,
        2: 0.94,
        1: 0.92,
    }.get(org_rank, 0.0)
    if len(shared_strong) >= 2:
        confidence += 0.02
    if len(shared_strong) >= 3:
        confidence += 0.01
    confidence = min(confidence, 0.99)
    if confidence < float(minimum_confidence or 0):
        return None

    basis = [
        "EXACT_FISCAL_YEAR",
        organization_basis,
        f"DISTINCTIVE_PROJECT_IDENTITY:{len(shared_strong)}",
        str(fact.get("evidence_type") or ""),
        "FINAL_AWARD_FACT_PRESENT",
    ]
    return {
        **dict(fact),
        "budget_project_identity": str(
            budget.get("project_identity")
            or budget.get("sales_opportunity_identity")
            or ""
        ),
        "budget_raw_source_key": str(budget.get("raw_source_key") or ""),
        "budget_org_code": str(budget.get("org_code") or ""),
        "budget_org_name": str(budget.get("org_name") or ""),
        "budget_project_code": str(budget.get("project_code") or ""),
        "budget_project_name": str(budget.get("project_name") or ""),
        "budget_category": category,
        "organization_basis": organization_basis,
        "shared_identity": list(shared_strong),
        "shared_weak_identity": list(shared_weak),
        "match_confidence": confidence,
        "match_basis": basis,
        "candidate_only": False,
        "source_traffic": False,
    }


def match_award_facts(
    budgets,
    facts,
    *,
    minimum_confidence=MIN_MATCH_CONFIDENCE,
):
    """Assign each official award execution to at most one budget project.

    When two budget projects tie on distinctive identity strength + org identity,
    the award is dropped rather than guessed.
    """
    candidates = {}
    rejected_invalid = 0
    for fact in facts:
        if not bool((fact or {}).get("valid")):
            rejected_invalid += 1
            continue
        source_reference = str(fact.get("source_reference") or "")
        if not source_reference:
            rejected_invalid += 1
            continue
        for budget in budgets:
            candidate = match_budget_project(
                budget,
                fact,
                minimum_confidence=minimum_confidence,
            )
            if candidate is None:
                continue
            candidates.setdefault(source_reference, []).append(candidate)

    matches = []
    ambiguous = []
    for source_reference, rows in candidates.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                len(row.get("shared_identity") or []),
                _org_rank(row.get("organization_basis")),
                len(row.get("shared_weak_identity") or []),
                float(row.get("match_confidence") or 0),
            ),
            reverse=True,
        )
        top = ordered[0]
        top_rank = (
            len(top.get("shared_identity") or []),
            _org_rank(top.get("organization_basis")),
            len(top.get("shared_weak_identity") or []),
        )
        tied = [
            row for row in ordered
            if (
                len(row.get("shared_identity") or []),
                _org_rank(row.get("organization_basis")),
                len(row.get("shared_weak_identity") or []),
            ) == top_rank
        ]
        if len(tied) != 1:
            ambiguous.append({
                "source_reference": source_reference,
                "candidate_count": len(tied),
                "reason": "AMBIGUOUS_BUDGET_PROJECT_ASSIGNMENT",
            })
            continue
        matches.append(top)

    matches.sort(
        key=lambda row: (
            int(row.get("fiscal_year") or 0),
            str(row.get("award_date") or ""),
            float(row.get("match_confidence") or 0),
            str(row.get("budget_project_name") or ""),
        ),
        reverse=True,
    )
    return {
        "matches": matches,
        "ambiguous": ambiguous,
        "invalid_facts": rejected_invalid,
        "source_traffic": False,
        "one_execution_one_budget_project": True,
    }


def _evidence_key(row):
    parts = (
        str(row.get("budget_project_identity") or ""),
        str(row.get("evidence_type") or ""),
        str(row.get("source_reference") or ""),
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def save_compact_evidence(rows):
    """Persist matched compact facts only. No raw API payload or bidder lists."""
    ensure_schema()
    values = []
    for raw in rows or []:
        row = dict(raw)
        if not (
            row.get("budget_project_identity")
            and row.get("source_reference")
            and row.get("evidence_type") in EVIDENCE_TYPES
            and float(row.get("match_confidence") or 0) >= MIN_MATCH_CONFIDENCE
        ):
            continue
        values.append((
            _evidence_key(row),
            int(row.get("fiscal_year") or 0),
            str(row.get("evidence_type") or ""),
            str(row.get("source_business_type") or ""),
            str(row.get("source_reference") or ""),
            str(row.get("notice_no") or ""),
            str(row.get("notice_order") or ""),
            str(row.get("bid_clsfc_no") or ""),
            str(row.get("rebid_no") or ""),
            str(row.get("award_date") or ""),
            str(row.get("award_org_code") or ""),
            str(row.get("award_org_name") or ""),
            str(row.get("award_title") or ""),
            str(row.get("vendor_name") or ""),
            str(row.get("vendor_bizno") or ""),
            int(row.get("award_amount") or 0),
            float(row.get("award_rate") or 0),
            str(row.get("budget_project_identity") or ""),
            str(row.get("budget_raw_source_key") or ""),
            str(row.get("budget_org_code") or ""),
            str(row.get("budget_org_name") or ""),
            str(row.get("budget_project_code") or ""),
            str(row.get("budget_project_name") or ""),
            str(row.get("budget_category") or ""),
            str(row.get("organization_basis") or ""),
            json.dumps(
                list(row.get("shared_identity") or []),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            float(row.get("match_confidence") or 0),
            json.dumps(
                list(row.get("match_basis") or []),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ))

    if values:
        with connect() as conn:
            conn.executemany(
                """INSERT INTO budget_execution_evidence(
                       evidence_key,fiscal_year,evidence_type,source_business_type,
                       source_reference,notice_no,notice_order,bid_clsfc_no,rebid_no,
                       award_date,award_org_code,award_org_name,award_title,
                       vendor_name,vendor_bizno,award_amount,award_rate,
                       budget_project_identity,budget_raw_source_key,budget_org_code,
                       budget_org_name,budget_project_code,budget_project_name,
                       budget_category,organization_basis,shared_identity_json,
                       match_confidence,match_basis_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(evidence_key) DO UPDATE SET
                       fiscal_year=excluded.fiscal_year,
                       evidence_type=excluded.evidence_type,
                       source_business_type=excluded.source_business_type,
                       source_reference=excluded.source_reference,
                       notice_no=excluded.notice_no,
                       notice_order=excluded.notice_order,
                       bid_clsfc_no=excluded.bid_clsfc_no,
                       rebid_no=excluded.rebid_no,
                       award_date=excluded.award_date,
                       award_org_code=excluded.award_org_code,
                       award_org_name=excluded.award_org_name,
                       award_title=excluded.award_title,
                       vendor_name=excluded.vendor_name,
                       vendor_bizno=excluded.vendor_bizno,
                       award_amount=excluded.award_amount,
                       award_rate=excluded.award_rate,
                       budget_raw_source_key=excluded.budget_raw_source_key,
                       budget_org_code=excluded.budget_org_code,
                       budget_org_name=excluded.budget_org_name,
                       budget_project_code=excluded.budget_project_code,
                       budget_project_name=excluded.budget_project_name,
                       budget_category=excluded.budget_category,
                       organization_basis=excluded.organization_basis,
                       shared_identity_json=excluded.shared_identity_json,
                       match_confidence=excluded.match_confidence,
                       match_basis_json=excluded.match_basis_json,
                       updated_at=CURRENT_TIMESTAMP""",
                values,
            )
    return {
        "saved": len(values),
        "compact_only": True,
        "raw_payload_saved": False,
        "source_traffic": False,
    }


def evidence_rows(*, fiscal_year=None, evidence_type="", limit=500):
    ensure_schema()
    where = ["1=1"]
    params = []
    if fiscal_year is not None:
        where.append("fiscal_year=?")
        params.append(int(fiscal_year))
    if str(evidence_type or ""):
        if str(evidence_type) not in EVIDENCE_TYPES:
            return []
        where.append("evidence_type=?")
        params.append(str(evidence_type))
    params.append(max(1, min(int(limit), 5000)))
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM budget_execution_evidence
                WHERE {' AND '.join(where)}
                ORDER BY award_date DESC,match_confidence DESC
                LIMIT ?""",
            tuple(params),
        ).fetchall()
    result = []
    for raw in rows:
        item = dict(raw)
        try:
            item["shared_identity"] = json.loads(
                str(item.pop("shared_identity_json") or "[]")
            )
        except (TypeError, ValueError):
            item["shared_identity"] = []
        try:
            item["match_basis"] = json.loads(
                str(item.pop("match_basis_json") or "[]")
            )
        except (TypeError, ValueError):
            item["match_basis"] = []
        result.append(item)
    return result


def evidence_summary(*, fiscal_year=None):
    ensure_schema()
    where = ""
    params = ()
    if fiscal_year is not None:
        where = "WHERE fiscal_year=?"
        params = (int(fiscal_year),)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT evidence_type,COUNT(*) AS n,
                       COALESCE(SUM(award_amount),0) AS amount
                FROM budget_execution_evidence
                {where}
                GROUP BY evidence_type
                ORDER BY evidence_type""",
            params,
        ).fetchall()
    return {
        "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
        "by_type": {
            str(row["evidence_type"]): {
                "count": int(row["n"] or 0),
                "award_amount": int(row["amount"] or 0),
            }
            for row in rows
        },
        "compact_only": True,
        "source_traffic": False,
    }


def build_compact_evidence(
    award_rows,
    *,
    business_type,
    budgets=None,
    notice_by_key=None,
    minimum_confidence=MIN_MATCH_CONFIDENCE,
    persist=False,
):
    """Normalize already-obtained award rows, match budgets, optionally persist."""
    budget_rows = list(
        budgets
        if budgets is not None
        else budget_targets_vnext.target_candidates(
            categories=budget_targets_vnext.TARGET_CATEGORIES,
            minimum_confidence=0.0,
        )
    )
    notice_index = dict(notice_by_key or {})
    facts = []
    invalid = []
    for raw in award_rows or []:
        row = dict(raw or {})
        notice_no = _pick(row, "bidNtceNo", "bidNoticeNo")
        notice_order = _pick(row, "bidNtceOrd", "bidNoticeOrd") or "000"
        notice = (
            notice_index.get(f"{notice_no}|{notice_order}")
            or notice_index.get(notice_no)
            or {}
        )
        fact = compact_award_fact(
            row,
            business_type=business_type,
            notice=notice,
        )
        if fact.get("valid"):
            facts.append(fact)
        else:
            invalid.append({
                "source_reference": (
                    f"{notice_no}|{notice_order}" if notice_no else ""
                ),
                "problems": list(fact.get("problems") or []),
            })

    matched = match_award_facts(
        budget_rows,
        facts,
        minimum_confidence=minimum_confidence,
    )
    saved = {"saved": 0}
    if persist:
        saved = save_compact_evidence(matched["matches"])
    return {
        "business_type": _business_type(business_type),
        "facts": len(facts),
        "invalid": invalid,
        "matches": matched["matches"],
        "ambiguous": matched["ambiguous"],
        "saved": int(saved.get("saved") or 0),
        "compact_only": True,
        "raw_payload_saved": False,
        "source_traffic": False,
    }
