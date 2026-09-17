"""Read-only budget -> procurement lifecycle view for G2B vNext.

This composes conservative budget/notice candidates with the existing normalized
service award/contract analysis.  It does not fetch sources and does not persist
budget-to-notice links.

Goods notices currently remain NOTICE_ONLY because the additive vNext award/contract
normalization in this foundation is service-specific.  That limitation is explicit
rather than guessed around.
"""
from __future__ import annotations

import analysis_vnext
import budget_notice_links_vnext
import budget_targets_vnext


def _stage(row):
    if str(row.get("contract_no") or ""):
        return "CONTRACTED"
    if int(row.get("contract_count") or 0) > 0:
        return "CONTRACT_RECORDS_PRESENT"
    if str(row.get("final_vendor") or ""):
        return "FINAL_AWARD"
    if str(row.get("first_rank_vendor") or ""):
        return "OPENING_RANKED"
    return "NOTICE_ONLY"


def _empty_lifecycle(*, supported):
    return {
        "lifecycle_supported": bool(supported),
        "latest_known_stage": "NOTICE_ONLY",
        "award_summary_key": "",
        "opening_date": "",
        "participant_count": 0,
        "first_rank_vendor": "",
        "first_rank_bizno": "",
        "first_rank_amount": 0,
        "final_vendor": "",
        "final_vendor_bizno": "",
        "final_award_amount": 0,
        "award_rate": 0.0,
        "contract_no": "",
        "contract_vendor": "",
        "contract_vendor_bizno": "",
        "contract_amount": 0,
        "contract_count": 0,
        "multiple_contracts": False,
        "opening_stale": False,
        "final_award_stale": False,
        "contract_stale": False,
    }


def _lifecycle_fields(row):
    keys = (
        "award_summary_key", "opening_date", "participant_count",
        "first_rank_vendor", "first_rank_bizno", "first_rank_amount",
        "final_vendor", "final_vendor_bizno", "final_award_amount", "award_rate",
        "contract_no", "contract_vendor", "contract_vendor_bizno", "contract_amount",
        "contract_count", "multiple_contracts",
        "opening_stale", "final_award_stale", "contract_stale",
    )
    result = {key: row.get(key) for key in keys}
    result["lifecycle_supported"] = True
    result["latest_known_stage"] = _stage(row)
    return result


def budget_procurement_lifecycle_rows(*, fiscal_year=None, categories=None,
                                      minimum_classification_confidence=0.0,
                                      minimum_match_confidence=0.92,
                                      classifier_version=None, limit=1000):
    """Return budget candidate notices enriched with current service lifecycle facts.

    One service notice may produce multiple rows when the official source contains
    multiple executions/rebids.  Goods candidates remain one NOTICE_ONLY row.
    """
    candidates = budget_notice_links_vnext.budget_notice_candidates(
        fiscal_year=fiscal_year,
        categories=categories,
        minimum_classification_confidence=minimum_classification_confidence,
        minimum_match_confidence=minimum_match_confidence,
        classifier_version=classifier_version,
        limit=max(1, int(limit)),
    )
    service_keys = sorted({
        row["notice_source_key"]
        for row in candidates
        if row["notice_dataset"] == "bid_notice_service"
    })
    lifecycle_by_notice = {}
    if service_keys:
        rows = analysis_vnext.service_lifecycle_rows(
            source_keys=service_keys,
            classifier_version=classifier_version,
            limit=max(100, len(service_keys) * 20),
        )
        for row in rows:
            lifecycle_by_notice.setdefault(str(row["source_key"]), []).append(row)

    result = []
    for candidate in candidates:
        base = dict(candidate)
        base["budget_notice_relation"] = "CANDIDATE_ONLY"
        if candidate["notice_dataset"] != "bid_notice_service":
            result.append({**base, **_empty_lifecycle(supported=False)})
            continue

        executions = lifecycle_by_notice.get(candidate["notice_source_key"], [])
        if not executions:
            result.append({**base, **_empty_lifecycle(supported=True)})
            continue
        for execution in executions:
            result.append({**base, **_lifecycle_fields(execution)})

    result.sort(key=lambda row: (
        -float(row.get("match_confidence") or 0),
        str(row.get("notice_date") or ""),
        str(row.get("notice_source_key") or ""),
        str(row.get("award_summary_key") or ""),
    ), reverse=True)
    return result[:max(1, int(limit))]


def budget_procurement_lifecycle_summary(**kwargs):
    rows = budget_procurement_lifecycle_rows(**kwargs)
    by_stage = {}
    for row in rows:
        stage = str(row.get("latest_known_stage") or "NOTICE_ONLY")
        by_stage[stage] = by_stage.get(stage, 0) + 1
    return {
        "rows": len(rows),
        "budget_projects": len({row["budget_project_identity"] for row in rows}),
        "notices": len({(row["notice_dataset"], row["notice_source_key"]) for row in rows}),
        "by_stage": dict(sorted(by_stage.items())),
        "candidate_only": True,
        "persisted_budget_notice_links": 0,
        "source_traffic": False,
    }


_STAGE_RANK = {
    "BUDGET_ONLY": 0,
    "NOTICE_PUBLISHED": 1,
    "NOTICE_ONLY": 1,
    "OPENING_RANKED": 2,
    "FINAL_AWARD": 3,
    "CONTRACT_RECORDS_PRESENT": 4,
    "CONTRACTED": 5,
}


def _highest_stage(stages, *, has_notice=False):
    values = [str(value or "") for value in stages if str(value or "")]
    if not values:
        return "NOTICE_PUBLISHED" if has_notice else "BUDGET_ONLY"
    best = max(values, key=lambda value: _STAGE_RANK.get(value, -1))
    return "NOTICE_PUBLISHED" if best == "NOTICE_ONLY" else best


def budget_project_procurement_rows(*, fiscal_year=None, categories=None,
                                    minimum_classification_confidence=0.0,
                                    minimum_match_confidence=0.92,
                                    classifier_version=None, limit=1000):
    """Group current target budgets with zero or more candidate procurement notices.

    Every current target budget remains visible.  A project with no conservative
    notice candidate is explicitly BUDGET_ONLY rather than disappearing from the
    result.
    """
    selected = categories if categories is not None else budget_targets_vnext.TARGET_CATEGORIES
    if categories is not None and not list(categories):
        return []
    projects = budget_targets_vnext.target_candidates(
        fiscal_year=fiscal_year,
        categories=selected,
        minimum_confidence=minimum_classification_confidence,
        classifier_version=classifier_version,
    )
    lifecycle = budget_procurement_lifecycle_rows(
        fiscal_year=fiscal_year,
        categories=selected,
        minimum_classification_confidence=minimum_classification_confidence,
        minimum_match_confidence=minimum_match_confidence,
        classifier_version=classifier_version,
        limit=max(1000, len(projects) * 50),
    )

    by_project = {}
    for row in lifecycle:
        by_project.setdefault(str(row["budget_project_identity"]), []).append(row)

    result = []
    for project in projects[:max(1, int(limit))]:
        identity = str(project.get("project_identity") or "")
        linked = by_project.get(identity, [])
        notice_groups = {}
        for row in linked:
            key = (str(row["notice_dataset"]), str(row["notice_source_key"]))
            notice_groups.setdefault(key, []).append(row)

        notices = []
        for (_dataset, _key), group in sorted(
            notice_groups.items(),
            key=lambda item: (
                str(item[1][0].get("notice_date") or ""),
                str(item[0][1]),
            ),
            reverse=True,
        ):
            first = group[0]
            service = first["notice_dataset"] == "bid_notice_service"
            executions = []
            if service:
                for item in group:
                    if not item.get("award_summary_key") and item.get("latest_known_stage") == "NOTICE_ONLY":
                        continue
                    executions.append({
                        "award_summary_key": str(item.get("award_summary_key") or ""),
                        "latest_known_stage": str(item.get("latest_known_stage") or "NOTICE_ONLY"),
                        "opening_date": str(item.get("opening_date") or ""),
                        "participant_count": int(item.get("participant_count") or 0),
                        "first_rank_vendor": str(item.get("first_rank_vendor") or ""),
                        "first_rank_bizno": str(item.get("first_rank_bizno") or ""),
                        "first_rank_amount": int(item.get("first_rank_amount") or 0),
                        "final_vendor": str(item.get("final_vendor") or ""),
                        "final_vendor_bizno": str(item.get("final_vendor_bizno") or ""),
                        "final_award_amount": int(item.get("final_award_amount") or 0),
                        "award_rate": float(item.get("award_rate") or 0),
                        "contract_no": str(item.get("contract_no") or ""),
                        "contract_vendor": str(item.get("contract_vendor") or ""),
                        "contract_vendor_bizno": str(item.get("contract_vendor_bizno") or ""),
                        "contract_amount": int(item.get("contract_amount") or 0),
                        "contract_count": int(item.get("contract_count") or 0),
                        "multiple_contracts": bool(item.get("multiple_contracts")),
                        "opening_stale": bool(item.get("opening_stale")),
                        "final_award_stale": bool(item.get("final_award_stale")),
                        "contract_stale": bool(item.get("contract_stale")),
                    })
            notice_stage = _highest_stage(
                [item.get("latest_known_stage") for item in group],
                has_notice=True,
            )
            notices.append({
                "notice_dataset": str(first.get("notice_dataset") or ""),
                "notice_source_key": str(first.get("notice_source_key") or ""),
                "notice_name": str(first.get("notice_name") or ""),
                "notice_date": str(first.get("notice_date") or ""),
                "notice_org_name": str(first.get("notice_org_name") or ""),
                "organization_match": str(first.get("organization_match") or ""),
                "match_basis": str(first.get("match_basis") or ""),
                "match_confidence": float(first.get("match_confidence") or 0),
                "shared_project_tokens": list(first.get("shared_project_tokens") or []),
                "lifecycle_supported": bool(first.get("lifecycle_supported")),
                "latest_known_stage": notice_stage,
                "executions": executions,
            })

        overall_stage = _highest_stage(
            [notice["latest_known_stage"] for notice in notices],
            has_notice=bool(notices),
        )
        result.append({
            "budget_project_identity": identity,
            "budget_raw_dataset": str(project.get("raw_dataset") or ""),
            "budget_raw_source_key": str(project.get("raw_source_key") or ""),
            "fiscal_year": int(project.get("fiscal_year") or 0),
            "org_code": str(project.get("org_code") or ""),
            "org_name": str(project.get("org_name") or ""),
            "dept_name": str(project.get("dept_name") or ""),
            "project_code": str(project.get("project_code") or ""),
            "project_name": str(project.get("project_name") or ""),
            "primary_category": str(project.get("primary_category") or ""),
            "subcategory": str(project.get("subcategory") or ""),
            "classification_confidence": float(project.get("classification_confidence") or 0),
            "budget_amount": int(project.get("budget_amount") or 0),
            "executed_amount": int(project.get("executed_amount") or 0),
            "remaining_amount": int(project.get("remaining_amount") or 0),
            "procurement_candidate_count": len(notices),
            "latest_known_stage": overall_stage,
            "notices": notices,
            "read_only": True,
            "source_traffic": False,
        })
    return result
