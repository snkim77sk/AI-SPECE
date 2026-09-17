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
