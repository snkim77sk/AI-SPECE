"""Manual fail-closed orchestration for the independent G2B vNext service lifecycle."""
import award_projection
import award_vnext
import bid_vnext
import classification_vnext
import contract_projection
import contract_vnext

SERVICE_CLASSIFICATION_DATASETS = (
    "bid_notice_service", "opening_result_service", "award_result_service", "contract_service",
)


def _require_complete(stage, result):
    if not bool((result or {}).get("complete")):
        raise RuntimeError(f"vNext pipeline blocked: {stage} RAW collection is incomplete")


def collect_service_lifecycle(start_date, end_date, *, page_size=999, max_pages=None,
                              resume=True, normalize_limit=None, classify_batch_size=1000,
                              run_classification=True):
    result = {}
    result["notice_raw"] = bid_vnext.collect_all(
        "service", start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)
    _require_complete("service_notice", result["notice_raw"])

    result["opening_raw"] = award_vnext.collect_service_opening(
        start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)
    _require_complete("service_opening", result["opening_raw"])
    result["first_rank"] = award_projection.normalize_dataset(
        award_projection.OPENING_DATASET, limit=normalize_limit)

    result["award_raw"] = award_vnext.collect_service_awards(
        start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)
    _require_complete("service_final_award", result["award_raw"])
    result["final_award"] = award_projection.normalize_dataset(
        award_projection.AWARD_DATASET, limit=normalize_limit)

    result["contract_raw"] = contract_vnext.collect_all(
        start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)
    _require_complete("service_contract", result["contract_raw"])
    result["contract_link"] = contract_projection.normalize_contracts(limit=normalize_limit)

    if run_classification:
        result["classification"] = classification_vnext.classify_all(
            datasets=SERVICE_CLASSIFICATION_DATASETS, batch_size=classify_batch_size)
    result["status"] = "COMPLETE"
    return result
