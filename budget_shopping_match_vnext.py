"""Explainable historical QWGJK -> G2B shopping match analysis.

The matcher is read-only. It compares stored normalized QWGJK detail projects with
stored LED/pole shopping delivery rows. It never claims that a budget row directly
funded a procurement row; results are evidence-ranked candidates for sales analysis.
"""
from __future__ import annotations

import datetime as dt
import re

import budget_read_vnext
import procurement_read_vnext

DEFAULT_CATEGORIES = ("LIGHTING", "POLE")
MIN_CANDIDATE_SCORE = 65
MIN_HIGH_SCORE = 80
MIN_PROJECT_SAMPLE = 30
MIN_HIGH_MATCHED_PROJECTS = 10

_GENERIC_TOKENS = frozenset({
    "사업", "공사", "구매", "납품", "설치", "교체", "개선", "정비", "유지",
    "유지보수", "신규", "노후", "시설", "물품", "관급", "관급자재", "일원",
    "일식", "제작", "공급", "추진", "시행", "관련", "관리",
})

_SIGNAL_TERMS = {
    "LED": ("led", "엘이디"),
    "STREET_LIGHT": ("가로등", "도로조명", "도로 조명"),
    "SECURITY_LIGHT": ("보안등", "방범등"),
    "LANDSCAPE_LIGHT": ("경관조명", "경관등"),
    "PATH_LIGHT": ("산책로조명", "산책로 조명", "보행등", "공원등"),
    "FLOOD_LIGHT": ("투광등", "투광조명"),
    "SOLAR_LIGHT": ("태양광조명", "태양광 조명", "태양광등"),
    "POLE": ("가로등주", "보안등주", "등주", "조명주", "폴"),
}


def _norm(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _org_aliases(name, region=""):
    text = _norm(name)
    region_text = _norm(region)
    aliases = {text} if text else set()
    for token in (
        "광역시", "특별시", "특별자치시", "특별자치도",
    ):
        if token in text:
            aliases.add(text.replace(token, ""))
    if region_text:
        aliases.add(region_text)
        shortened = region_text
        for token in ("광역시", "특별시", "특별자치시", "특별자치도", "도"):
            shortened = shortened.replace(token, "")
        if shortened:
            aliases.add(shortened)
            if text.startswith(shortened) and len(text) > len(shortened):
                aliases.add(text[len(shortened):])
    for suffix in ("시", "군", "구"):
        if text.endswith(suffix):
            # Keep the final administrative unit as a conservative fallback.
            pieces = re.findall(r"[가-힣]+(?:시|군|구)", str(name or ""))
            if pieces:
                aliases.add(_norm(pieces[-1]))
    return {value for value in aliases if len(value) >= 2}


def _tokens(value):
    result = set()
    for token in re.findall(r"[0-9A-Za-z가-힣]+", str(value or "").casefold()):
        token = token.strip()
        if len(token) < 2 or token in _GENERIC_TOKENS:
            continue
        if len(token) == 4 and token.isdigit() and token.startswith("20"):
            continue
        result.add(token)
    return result


def _signals(value):
    text = str(value or "").casefold()
    return {
        key
        for key, terms in _SIGNAL_TERMS.items()
        if any(term in text for term in terms)
    }


def _budget_text(row):
    return " ".join(str(row.get(name) or "") for name in (
        "project_name", "field_name", "section_name", "account_name",
    ))


def _shopping_text(row):
    return " ".join(str(row.get(name) or "") for name in (
        "delivery_req_name", "detail_item_name", "item_name", "model_name",
    ))


def _organization_basis(budget, shopping):
    budget_aliases = _org_aliases(
        budget.get("org_name"),
        budget.get("region_name"),
    )
    shopping_aliases = _org_aliases(
        shopping.get("demand_org"),
        shopping.get("demand_region"),
    )
    shared = budget_aliases & shopping_aliases
    if not shared:
        return "", []
    budget_full = _norm(budget.get("org_name"))
    shopping_full = _norm(shopping.get("demand_org"))
    if budget_full and shopping_full and budget_full == shopping_full:
        return "EXACT_ORG_NAME", sorted(shared)
    return "ORG_ALIAS_MATCH", sorted(shared)


def _date_score(budget, shopping):
    budget_day = str(
        budget.get("source_date")
        or budget.get("snapshot_date")
        or ""
    )[:10]
    shopping_day = str(shopping.get("source_date") or "")[:10]
    try:
        bday = dt.date.fromisoformat(budget_day)
        sday = dt.date.fromisoformat(shopping_day)
    except ValueError:
        return 0, None
    if bday.year != sday.year:
        return 0, (sday - bday).days
    lag = (sday - bday).days
    # Snapshot date is observation evidence, not necessarily the first budget
    # approval date. Same-year rows are valid; later procurement gets a small bonus.
    return (8 if lag >= 0 else 4), lag


def _amount_score(budget, shopping):
    budget_amount = int(
        budget.get("budget_amount")
        or budget.get("appropriation_amount")
        or 0
    )
    shopping_amount = int(shopping.get("amount") or 0)
    if budget_amount <= 0 or shopping_amount <= 0:
        return 0
    ratio = shopping_amount / budget_amount
    if ratio <= 1.10:
        return 7
    if ratio <= 1.50:
        return 4
    return 0


def score_pair(budget, shopping):
    """Return one conservative explainable candidate score or None."""
    org_basis, shared_org = _organization_basis(budget, shopping)
    if not org_basis:
        return None

    budget_category = str(budget.get("primary_category") or "").upper()
    shopping_category = str(shopping.get("primary_category") or "").upper()
    if shopping_category not in DEFAULT_CATEGORIES:
        return None

    budget_text = _budget_text(budget)
    shopping_text = _shopping_text(shopping)
    budget_tokens = _tokens(budget_text)
    shopping_tokens = _tokens(shopping_text)
    shared_tokens = sorted(budget_tokens & shopping_tokens)
    shared_signals = sorted(_signals(budget_text) & _signals(shopping_text))

    # Require at least one lighting/pole semantic signal or distinctive shared token.
    if not shared_tokens and not shared_signals:
        return None

    score = 48 if org_basis == "EXACT_ORG_NAME" else 42
    evidence = [org_basis]

    if budget_category == shopping_category:
        score += 20
        evidence.append("CATEGORY_EXACT")
    elif {budget_category, shopping_category} <= {"LIGHTING", "POLE"}:
        score += 10
        evidence.append("CATEGORY_RELATED")
    else:
        return None

    if shared_signals:
        signal_points = min(20, 10 + 5 * (len(shared_signals) - 1))
        score += signal_points
        evidence.append("SHARED_SIGNAL:" + ",".join(shared_signals))
    if shared_tokens:
        token_points = min(18, 8 + 4 * (len(shared_tokens) - 1))
        score += token_points
        evidence.append("SHARED_TOKEN:" + ",".join(shared_tokens[:5]))

    date_points, lag_days = _date_score(budget, shopping)
    score += date_points
    if date_points:
        evidence.append("SAME_FISCAL_YEAR")
    amount_points = _amount_score(budget, shopping)
    score += amount_points
    if amount_points:
        evidence.append("AMOUNT_PLAUSIBLE")

    score = min(100, int(score))
    if score < MIN_CANDIDATE_SCORE:
        return None

    level = "HIGH" if score >= MIN_HIGH_SCORE else "CANDIDATE"
    return {
        "score": score,
        "level": level,
        "evidence": evidence,
        "organization_basis": org_basis,
        "shared_org_aliases": shared_org,
        "shared_tokens": shared_tokens,
        "shared_signals": shared_signals,
        "lag_days": lag_days,
    }


def _shopping_index(rows):
    index = {}
    for row in rows:
        aliases = _org_aliases(
            row.get("demand_org"),
            row.get("demand_region"),
        )
        for alias in aliases:
            index.setdefault(alias, []).append(row)
    return index


def historical_match_rows(
    *,
    fiscal_year,
    region="",
    categories=DEFAULT_CATEGORIES,
    budget_limit=300,
    shopping_limit=3000,
    candidates_per_project=3,
):
    """Compare stored QWGJK projects with stored LED/pole procurement deliveries."""
    year = int(fiscal_year)
    selected = tuple(
        value for value in categories
        if str(value).upper() in DEFAULT_CATEGORIES
    ) or DEFAULT_CATEGORIES

    budgets = budget_read_vnext.screen_budget_rows(
        fiscal_year=year,
        source_layers=("DETAIL_EXECUTION",),
        categories=selected,
        region=region,
        limit=max(1, min(int(budget_limit), 1000)),
    )
    shopping = procurement_read_vnext.shopping_rows(
        categories=selected,
        region=region,
        start_date=f"{year:04d}-01-01",
        end_date=f"{year:04d}-12-31",
        limit=max(1, min(int(shopping_limit), 5000)),
    )
    by_org = _shopping_index(shopping)

    result = []
    for budget in budgets:
        candidates = {}
        for alias in _org_aliases(
            budget.get("org_name"),
            budget.get("region_name"),
        ):
            for shop in by_org.get(alias, ()):
                candidates[str(shop.get("source_key") or id(shop))] = shop

        scored = []
        for shop in candidates.values():
            match = score_pair(budget, shop)
            if match is None:
                continue
            scored.append({
                "budget_raw_source_key": str(
                    budget.get("raw_source_key")
                    or budget.get("record_key")
                    or ""
                ),
                "budget_project_identity": str(
                    budget.get("project_identity") or ""
                ),
                "fiscal_year": year,
                "budget_region": str(
                    budget.get("region_name") or ""
                ),
                "budget_org": str(budget.get("org_name") or ""),
                "budget_dept": str(budget.get("dept_name") or ""),
                "budget_project_code": str(
                    budget.get("project_code") or ""
                ),
                "budget_project_name": str(
                    budget.get("project_name") or ""
                ),
                "budget_category": str(
                    budget.get("primary_category") or ""
                ),
                "budget_amount": int(
                    budget.get("budget_amount")
                    or budget.get("appropriation_amount")
                    or 0
                ),
                "budget_source_date": str(
                    budget.get("source_date")
                    or budget.get("snapshot_date")
                    or ""
                ),
                "shopping_source_key": str(
                    shop.get("source_key") or ""
                ),
                "shopping_date": str(shop.get("source_date") or ""),
                "shopping_org": str(shop.get("demand_org") or ""),
                "shopping_category": str(
                    shop.get("primary_category") or ""
                ),
                "shopping_item": str(
                    shop.get("item_name")
                    or shop.get("detail_item_name")
                    or ""
                ),
                "shopping_delivery_name": str(
                    shop.get("delivery_req_name") or ""
                ),
                "shopping_vendor": str(shop.get("vendor_name") or ""),
                "shopping_amount": int(shop.get("amount") or 0),
                **match,
            })
        scored.sort(
            key=lambda row: (
                int(row["score"]),
                int(row["shopping_amount"]),
                str(row["shopping_date"]),
            ),
            reverse=True,
        )
        result.extend(scored[:max(1, min(int(candidates_per_project), 5))])

    result.sort(
        key=lambda row: (
            int(row["score"]),
            int(row["budget_amount"]),
            str(row["budget_project_name"]),
        ),
        reverse=True,
    )
    return {
        "fiscal_year": year,
        "region": str(region or ""),
        "categories": list(selected),
        "budget_projects_scanned": len(budgets),
        "shopping_rows_scanned": len(shopping),
        "matches": result,
        "source_traffic": False,
        "relation_semantics": "CANDIDATE_EVIDENCE_NOT_FUNDING_PROOF",
    }


def historical_match_summary(**kwargs):
    payload = historical_match_rows(**kwargs)
    rows = payload["matches"]
    high = [row for row in rows if row["level"] == "HIGH"]
    candidate = [row for row in rows if row["level"] == "CANDIDATE"]
    high_projects = {
        row["budget_project_identity"] or row["budget_raw_source_key"]
        for row in high
    }
    matched_projects = {
        row["budget_project_identity"] or row["budget_raw_source_key"]
        for row in rows
    }
    high_shop_keys = {row["shopping_source_key"] for row in high}
    high_amount = sum(
        max(
            int(row["shopping_amount"])
            for row in high
            if row["shopping_source_key"] == key
        )
        for key in high_shop_keys
    ) if high_shop_keys else 0

    budget_count = int(payload["budget_projects_scanned"])
    enough = (
        budget_count >= MIN_PROJECT_SAMPLE
        and len(high_projects) >= MIN_HIGH_MATCHED_PROJECTS
    )
    reasons = []
    if budget_count < MIN_PROJECT_SAMPLE:
        reasons.append("BUDGET_PROJECT_SAMPLE_SMALL")
    if len(high_projects) < MIN_HIGH_MATCHED_PROJECTS:
        reasons.append("HIGH_MATCH_SAMPLE_SMALL")

    return {
        **payload,
        "high_matches": len(high),
        "candidate_matches": len(candidate),
        "matched_budget_projects": len(matched_projects),
        "high_matched_budget_projects": len(high_projects),
        "high_matched_shopping_amount": high_amount,
        "project_match_rate": (
            round(len(matched_projects) / budget_count, 4)
            if budget_count else 0.0
        ),
        "evidence_sufficient_for_pattern_learning": enough,
        "expand_2025_recommended": not enough,
        "expansion_reasons": reasons,
        "sufficiency_thresholds": {
            "minimum_budget_projects": MIN_PROJECT_SAMPLE,
            "minimum_high_matched_projects": MIN_HIGH_MATCHED_PROJECTS,
        },
    }
