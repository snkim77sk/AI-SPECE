"""Future sales evidence derived from persisted historical budget-shopping matches.

This module is read-only and performs no source traffic. Scores are evidence-strength
indicators, not procurement probabilities and not claims of causal budget funding.
"""
from __future__ import annotations

import admin_geography_v41
import budget_shopping_match_store
import budget_shopping_match_vnext

TARGET_CATEGORIES = ("LIGHTING", "POLE")
FUTURE_EVIDENCE_VERSION = "future-sales-evidence-v2-admin-lineage"


def _norm_org(value):
    return "".join(
        ch for ch in str(value or "").casefold()
        if ch.isalnum()
    )


def _org_aliases(name, region=""):
    return budget_shopping_match_vnext._org_aliases(name, region)


def _future_org_text(row):
    return " ".join(str(row.get(name) or "") for name in (
        "project_name",
        "field_name",
        "section_name",
        "account_name",
    ))


def _future_as_of_date(row):
    # Future evidence follows the target fiscal year, not the observation
    # snapshot date. A 2027 budget observed in June 2026 still belongs to the
    # post-reform 2027 organization lineage.
    year = int(row.get("fiscal_year") or 0)
    if year > 0:
        return f"{year:04d}-12-31"
    for name in ("source_date", "snapshot_date"):
        value = str(row.get(name) or "")[:10]
        if value:
            return value
    return ""


def _pattern_index(patterns):
    index = {}
    for pattern in patterns or ():
        org = str(pattern.get("org_name") or "")
        for alias in _org_aliases(org):
            current = index.get(alias)
            rank = (
                int(pattern.get("high_matched_budget_projects") or 0),
                int(pattern.get("actual_shopping_amount") or 0),
            )
            if current is None or rank > current[0]:
                index[alias] = (rank, pattern)
    return index


def _find_pattern(row, index):
    raw_org = row.get("org_name") or row.get("institution_name")
    lineage = admin_geography_v41.organization_lineage(
        org=raw_org,
        project_text=_future_org_text(row),
        source_date=str(
            row.get("source_date") or row.get("snapshot_date") or ""
        ),
        region=str(row.get("region_name") or ""),
        as_of_date=_future_as_of_date(row),
    )
    group_org = str(lineage.get("org_name") or "").strip()
    group_key = str(lineage.get("group_key") or "").strip()
    basis = str(lineage.get("basis") or "SOURCE_ORG")

    if not group_key or not group_org:
        return None, basis

    aliases = _org_aliases(group_org, row.get("region_name"))
    candidates = {}
    for alias in aliases:
        found = index.get(alias)
        if found:
            pattern = found[1]
            candidates[str(pattern.get("org_name") or alias)] = found
    if not candidates:
        return None, basis

    pattern = max(candidates.values(), key=lambda item: item[0])[1]
    if basis in {"SOURCE_ORG", "CURRENT_ORG"}:
        return pattern, "DIRECT_ORG_ALIAS"
    return pattern, f"LINEAGE:{basis}"


def _row_signals(row):
    text = " ".join(str(row.get(name) or "") for name in (
        "project_name",
        "field_name",
        "section_name",
        "account_name",
    ))
    return budget_shopping_match_vnext._signals(text)


def score_future_budget_evidence(
    row,
    pattern=None,
    *,
    pattern_match_basis="DIRECT_PATTERN_ARGUMENT",
):
    """Attach historical buying evidence without converting it to a probability."""
    item = dict(row or {})
    category = str(item.get("primary_category") or "").upper()
    if category not in TARGET_CATEGORIES:
        return {
            **item,
            "historical_evidence_score": 0,
            "historical_evidence_level": "OUTSIDE_LED_POLE",
            "historical_evidence_reasons": [],
            "historical_pattern_org": "",
            "historical_high_projects": 0,
            "historical_total_budget_projects": 0,
            "historical_high_match_project_rate": None,
            "historical_matched_project_rate": None,
            "historical_population_complete": False,
            "historical_actual_shopping_amount": 0,
            "historical_shopping_to_budget_amount_ratio": 0.0,
            "historical_average_lag_days": None,
            "historical_evidence_years": [],
            "historical_evidence_model_version": FUTURE_EVIDENCE_VERSION,
            "historical_pattern_match_basis": str(pattern_match_basis or ""),
            "historical_pattern_historical_org_names": [],
            "historical_organization_lineage_applied": False,
        }

    if not pattern:
        return {
            **item,
            "historical_evidence_score": 0,
            "historical_evidence_level": "NO_HISTORY",
            "historical_evidence_reasons": [],
            "historical_pattern_org": "",
            "historical_high_projects": 0,
            "historical_total_budget_projects": 0,
            "historical_high_match_project_rate": None,
            "historical_matched_project_rate": None,
            "historical_population_complete": False,
            "historical_actual_shopping_amount": 0,
            "historical_shopping_to_budget_amount_ratio": 0.0,
            "historical_average_lag_days": None,
            "historical_evidence_years": [],
            "historical_evidence_model_version": FUTURE_EVIDENCE_VERSION,
            "historical_pattern_match_basis": str(pattern_match_basis or ""),
            "historical_pattern_historical_org_names": [],
            "historical_organization_lineage_applied": False,
        }

    score = 35
    if str(pattern_match_basis or "").startswith("LINEAGE:"):
        reasons = [
            "HISTORICAL_ORG_LINEAGE_MATCH",
            str(pattern_match_basis),
        ]
    else:
        reasons = ["HISTORICAL_ORG_MATCH"]
    pattern_budget_categories = {
        str(value).upper()
        for value in (pattern.get("budget_categories") or [])
    }
    pattern_shopping_categories = {
        str(value).upper()
        for value in (pattern.get("shopping_categories") or [])
    }
    if category in pattern_budget_categories | pattern_shopping_categories:
        score += 20
        reasons.append("HISTORICAL_CATEGORY_MATCH")

    high_projects = int(pattern.get("high_matched_budget_projects") or 0)
    total_projects = int(pattern.get("historical_budget_projects") or 0)
    population_complete = bool(pattern.get("population_complete"))
    high_match_rate = pattern.get("high_match_project_rate")
    matched_rate = pattern.get("matched_project_rate")
    if high_projects >= 5:
        score += 20
        reasons.append("HISTORICAL_SAMPLE_5_PLUS")
    elif high_projects >= 3:
        score += 14
        reasons.append("HISTORICAL_SAMPLE_3_PLUS")
    elif high_projects >= 1:
        score += 6
        reasons.append("HISTORICAL_SAMPLE_PRESENT")

    if (
        population_complete
        and total_projects >= 5
        and high_match_rate is not None
    ):
        rate = float(high_match_rate)
        if rate >= 0.50:
            score += 10
            reasons.append("HISTORICAL_HIGH_MATCH_RATE_50_PLUS")
        elif rate >= 0.25:
            score += 5
            reasons.append("HISTORICAL_HIGH_MATCH_RATE_25_PLUS")
        elif rate < 0.10:
            score -= 8
            reasons.append("HISTORICAL_HIGH_MATCH_RATE_LOW")

    years = sorted({
        int(value) for value in (pattern.get("evidence_years") or [])
        if int(value) > 0
    })
    if len(years) >= 2:
        score += 10
        reasons.append("MULTI_YEAR_EVIDENCE")

    pattern_signals = set((pattern.get("signal_counts") or {}).keys())
    shared_signals = sorted(_row_signals(item) & pattern_signals)
    if shared_signals:
        score += min(15, 5 * len(shared_signals))
        reasons.append("REPEATED_SIGNAL:" + ",".join(shared_signals))

    layer = str(item.get("source_layer") or "")
    if layer == "APPROPRIATION":
        # AIDFA is structural, not a project-level commitment. Historical behavior
        # can support prioritization, but evidence strength is deliberately capped.
        score = min(score, 75)
        reasons.append("AIDFA_STRUCTURAL_CAP")
    else:
        score = min(score, 100)

    if score >= 70:
        level = "STRONG_HISTORY"
    elif score >= 50:
        level = "HISTORY_PRESENT"
    else:
        level = "LIMITED_HISTORY"

    return {
        **item,
        "historical_evidence_score": int(score),
        "historical_evidence_level": level,
        "historical_evidence_reasons": reasons,
        "historical_pattern_org": str(pattern.get("org_name") or ""),
        "historical_high_projects": high_projects,
        "historical_total_budget_projects": total_projects,
        "historical_high_match_project_rate": (
            float(high_match_rate)
            if high_match_rate is not None
            else None
        ),
        "historical_matched_project_rate": (
            float(matched_rate)
            if matched_rate is not None
            else None
        ),
        "historical_population_complete": population_complete,
        "historical_actual_shopping_amount": int(
            pattern.get("actual_shopping_amount") or 0
        ),
        "historical_shopping_to_budget_amount_ratio": float(
            pattern.get("shopping_to_budget_amount_ratio") or 0
        ),
        "historical_average_lag_days": pattern.get(
            "average_nonnegative_lag_days"
        ),
        "historical_evidence_years": years,
        "historical_shared_signals": shared_signals,
        "historical_pattern_basis": str(
            pattern.get("pattern_basis") or ""
        ),
        "historical_evidence_model_version": FUTURE_EVIDENCE_VERSION,
        "historical_pattern_match_basis": str(pattern_match_basis or ""),
        "historical_pattern_historical_org_names": list(
            pattern.get("historical_org_names") or []
        ),
        "historical_organization_lineage_applied": bool(
            pattern.get("organization_lineage_applied")
            or str(pattern_match_basis or "").startswith("LINEAGE:")
        ),
    }


def enrich_rows(rows, *, patterns):
    index = _pattern_index(patterns)
    result = []
    for row in rows or ():
        pattern, match_basis = _find_pattern(row, index)
        result.append(
            score_future_budget_evidence(
                row,
                pattern,
                pattern_match_basis=match_basis,
            )
        )
    result.sort(
        key=lambda row: (
            int(row.get("historical_evidence_score") or 0),
            int(row.get("budget_amount") or row.get("appropriation_amount") or 0),
            str(row.get("org_name") or ""),
            str(row.get("project_name") or ""),
        ),
        reverse=True,
    )
    return result


def future_budget_rows(
    *,
    fiscal_year,
    region="",
    categories=TARGET_CATEGORIES,
    limit=200,
):
    """Return bounded future budget rows enriched by persisted 2025/2026 evidence."""
    import budget_read_vnext

    selected = tuple(
        str(value).upper()
        for value in categories
        if str(value).strip()
    )
    rows = budget_read_vnext.screen_budget_rows(
        fiscal_year=int(fiscal_year),
        source_layers=("APPROPRIATION", "DETAIL_EXECUTION"),
        categories=selected,
        region=region,
        limit=max(1, min(int(limit), 500)),
    )
    patterns = budget_shopping_match_store.organization_patterns(
        fiscal_years=(2025, 2026),
        region=region,
        min_score=80,
        limit=1000,
    )
    return enrich_rows(rows, patterns=patterns)
