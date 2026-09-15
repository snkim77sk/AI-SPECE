"""Manual orchestration for the independent G2B vNext service lifecycle.

This module is intentionally NOT wired to the production scheduler. It only fixes
the verified execution order for canary/backfill runs:

service notice RAW -> opening RAW -> first-rank projection -> final-award RAW ->
final-award projection -> contract RAW -> exact contract linkage.
"""
import award_projection
import award_vnext
import bid_vnext
import contract_projection
import contract_vnext


def collect_service_lifecycle(start_date, end_date, *, page_size=999, max_pages=None,
                              resume=True, normalize_limit=None):
    result = {}
    result["notice_raw"] = bid_vnext.collect_all(
        "service", start_date, end_date,
        page_size=page_size, max_pages=max_pages, resume=resume,
    )
    result["opening_raw"] = award_vnext.collect_service_opening(
        start_date, end_date,
        page_size=page_size, max_pages=max_pages, resume=resume,
    )
    result["first_rank"] = award_projection.normalize_dataset(
        award_projection.OPENING_DATASET, limit=normalize_limit,
    )
    result["award_raw"] = award_vnext.collect_service_awards(
        start_date, end_date,
        page_size=page_size, max_pages=max_pages, resume=resume,
    )
    result["final_award"] = award_projection.normalize_dataset(
        award_projection.AWARD_DATASET, limit=normalize_limit,
    )
    result["contract_raw"] = contract_vnext.collect_all(
        start_date, end_date,
        page_size=page_size, max_pages=max_pages, resume=resume,
    )
    result["contract_link"] = contract_projection.normalize_contracts(limit=normalize_limit)
    return result
