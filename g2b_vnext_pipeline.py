"""Manual orchestration for the independent G2B vNext service lifecycle.

This module is intentionally NOT wired to the production scheduler. It fixes the
execution order for canary/backfill runs:

service notice RAW -> opening RAW -> final-award RAW -> contract RAW ->
first-rank/final-award normalization -> exact contract linkage -> versioned
post-RAW classification.
"""
import award_projection
import award_vnext
import bid_vnext
import classification_vnext
import contract_projection
import contract_vnext

SERVICE_CLASSIFICATION_DATASETS = (
    "bid_notice_service",
    "opening_result_service",
    "award_result_service",
    "contract_service",
)


def collect_service_lifecycle(start_date, end_date, *, page_size=999, max_pages=None,
                              resume=True, normalize_limit=None, classify_batch_size=1000,
                              run_classification=True):
    result = {'start_date': str(start_date), 'end_date': str(end_date), 'complete': False}
    stages = (
        ('notice_raw', lambda: bid_vnext.collect_all('service', start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)),
        ('opening_raw', lambda: award_vnext.collect_service_opening(start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)),
        ('award_raw', lambda: award_vnext.collect_service_awards(start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)),
        ('contract_raw', lambda: contract_vnext.collect_all(start_date, end_date, page_size=page_size, max_pages=max_pages, resume=resume)),
    )
    for name, runner in stages:
        result[name] = runner()
        if result[name].get('complete') is not True:
            result['stopped_on'] = name
            return result
    result['first_rank'] = award_projection.normalize_dataset(award_projection.OPENING_DATASET, limit=normalize_limit)
    result['final_award'] = award_projection.normalize_dataset(award_projection.AWARD_DATASET, limit=normalize_limit)
    result['contract_link'] = contract_projection.normalize_contracts(limit=normalize_limit)
    result['complete'] = not any((result[name].get('errors') or result[name].get('pending', 0)) for name in ('first_rank', 'final_award', 'contract_link'))
    if run_classification and result['complete']:
        result['classification'] = classification_vnext.classify_all(
            datasets=SERVICE_CLASSIFICATION_DATASETS, batch_size=classify_batch_size)
    return result
