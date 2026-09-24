"""Manual orchestration for the independent G2B vNext service lifecycle.

This module is intentionally NOT wired to the production scheduler. The safe order is:

service notice RAW -> opening RAW -> final-award RAW -> contract RAW ->
source replay verification for every collected dataset -> exact trusted-current-RAW
coverage for the service consumer -> first-rank/final-award normalization -> exact
contract linkage -> versioned post-RAW classification.
"""
import datetime as dt

import award_projection
import award_vnext
import bid_vnext
import classification_vnext
import contract_projection
import contract_vnext
import vnext_stability
from vnext_finalize_guard import FinalizeCoverageError, require_plan_raw_coverage
from vnext_store import get_checkpoint

SERVICE_CLASSIFICATION_DATASETS = (
    "bid_notice_service",
    "opening_result_service",
    "award_result_service",
    "contract_service",
)


def _date_range(start_date, end_date):
    start = dt.date.fromisoformat(str(start_date)).isoformat()
    end = dt.date.fromisoformat(str(end_date)).isoformat()
    if start > end:
        raise ValueError("start_date must not exceed end_date")
    return start, end


def _verify_service_stability(start_date, end_date):
    scope = f"{start_date}:{end_date}"
    stages = (
        (
            "bid_notice_service",
            lambda page, size: bid_vnext.fetch_page(
                "service", start_date, end_date, page=page, rows=size
            ),
            bid_vnext._source_key,
        ),
        (
            "opening_result_service",
            lambda page, size: award_vnext.fetch_page(
                "opening", start_date, end_date, page=page, rows=size
            ),
            award_vnext._raw_source_key,
        ),
        (
            "award_result_service",
            lambda page, size: award_vnext.fetch_page(
                "award", start_date, end_date, page=page, rows=size
            ),
            award_vnext._raw_source_key,
        ),
        (
            "contract_service",
            lambda page, size: contract_vnext.fetch_page(
                start_date, end_date, page=page, rows=size
            ),
            contract_vnext._source_key,
        ),
    )
    records = {}
    for dataset, fetch, identity in stages:
        proof = vnext_stability.verify_checkpoint_source(
            dataset=dataset,
            scope=scope,
            fetch=fetch,
            identity=identity,
        )
        checkpoint = get_checkpoint(dataset, scope)
        fresh = vnext_stability.stability_fresh_checkpoint(checkpoint)
        records[dataset] = {
            "stable": bool(proof.get("stable")),
            "reason": str(proof.get("reason") or ""),
            "replayed_pages": int(proof.get("replayed_pages") or 0),
            "fresh": bool(fresh),
            "verified_at_utc": vnext_stability.stability_verified_at(checkpoint),
        }
        if not records[dataset]["stable"] or not records[dataset]["fresh"]:
            return records, dataset
    return records, ""


def _require_service_raw_coverage(start_date, end_date):
    scope = f"{start_date}:{end_date}"
    audit = {
        "all_complete": True,
        "records": [
            {"dataset": dataset, "scope": scope, "complete": True}
            for dataset in SERVICE_CLASSIFICATION_DATASETS
        ],
    }
    return require_plan_raw_coverage(
        audit,
        consumer_datasets=SERVICE_CLASSIFICATION_DATASETS,
        reject_unplanned_datasets=False,
    )


def collect_service_lifecycle(start_date, end_date, *, page_size=999, max_pages=None,
                              resume=True, normalize_limit=None, classify_batch_size=1000,
                              run_classification=True):
    start_date, end_date = _date_range(start_date, end_date)
    result = {'start_date': start_date, 'end_date': end_date, 'complete': False}
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

    stability, failed_dataset = _verify_service_stability(start_date, end_date)
    result['source_stability'] = stability
    if failed_dataset:
        result['stopped_on'] = f'{failed_dataset}_stability'
        return result

    try:
        result['trusted_raw_coverage'] = _require_service_raw_coverage(start_date, end_date)
    except FinalizeCoverageError as exc:
        result['stopped_on'] = 'service_raw_coverage'
        result['raw_coverage_error'] = str(exc)
        return result

    result['first_rank'] = award_projection.normalize_dataset(award_projection.OPENING_DATASET, limit=normalize_limit)
    result['final_award'] = award_projection.normalize_dataset(award_projection.AWARD_DATASET, limit=normalize_limit)
    result['contract_link'] = contract_projection.normalize_contracts(limit=normalize_limit)
    result['complete'] = not any((result[name].get('errors') or result[name].get('pending', 0)) for name in ('first_rank', 'final_award', 'contract_link'))
    if run_classification and result['complete']:
        result['classification'] = classification_vnext.classify_all(
            datasets=SERVICE_CLASSIFICATION_DATASETS, batch_size=classify_batch_size)
    return result
