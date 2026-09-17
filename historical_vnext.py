"""Historical RAW backfill planner for the independent G2B vNext project.

This module intentionally separates planning/audit from live collection. Historical
normalization is fail-closed until receipt completeness *and* source replay stability
are both verified for every requested stage/date chunk.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import award_projection
import award_vnext
import bid_vnext
import classification_vnext
import contract_projection
import contract_vnext
import shopping_vnext
import vnext_stability
from db import connect
from vnext_store import get_checkpoint
from vnext_collection import verified_checkpoint
from vnext_live_gate import require_canary_approval

DEFAULT_CHUNK_DAYS = 7
MAX_SAFE_CHUNK_DAYS = 28


@dataclass(frozen=True)
class BackfillChunk:
    start_date: str
    end_date: str

    @property
    def scope(self):
        return f"{self.start_date}:{self.end_date}"


STAGES = (
    ("bid_notice_goods", lambda start, end, **kw: bid_vnext.collect_all("goods", start, end, **kw)),
    ("bid_notice_service", lambda start, end, **kw: bid_vnext.collect_all("service", start, end, **kw)),
    ("opening_result_service", lambda start, end, **kw: award_vnext.collect_service_opening(start, end, **kw)),
    ("award_result_service", lambda start, end, **kw: award_vnext.collect_service_awards(start, end, **kw)),
    ("contract_service", lambda start, end, **kw: contract_vnext.collect_all(start, end, **kw)),
    ("shopping_delivery", lambda start, end, **kw: shopping_vnext.collect_all(start, end, **kw)),
)


def _as_date(value):
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def iter_date_chunks(start_date, end_date, *, chunk_days=DEFAULT_CHUNK_DAYS):
    """Yield inclusive, gapless, non-overlapping date chunks."""
    days = int(chunk_days)
    if days < 1 or days > MAX_SAFE_CHUNK_DAYS:
        raise ValueError(f"chunk_days must be between 1 and {MAX_SAFE_CHUNK_DAYS}")
    start = _as_date(start_date)
    end = _as_date(end_date)
    if start > end:
        start, end = end, start
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + dt.timedelta(days=days - 1), end)
        yield BackfillChunk(cursor.isoformat(), chunk_end.isoformat())
        cursor = chunk_end + dt.timedelta(days=1)


def build_plan(start_date, end_date, *, chunk_days=DEFAULT_CHUNK_DAYS):
    """Build a serializable plan without touching the network."""
    chunks = list(iter_date_chunks(start_date, end_date, chunk_days=chunk_days))
    return {
        "start_date": chunks[0].start_date if chunks else "",
        "end_date": chunks[-1].end_date if chunks else "",
        "chunk_days": int(chunk_days),
        "chunk_count": len(chunks),
        "stage_count": len(STAGES),
        "planned_stage_calls": len(chunks) * len(STAGES),
        "stages": [name for name, _ in STAGES],
        "chunks": [{"start_date": c.start_date, "end_date": c.end_date, "scope": c.scope} for c in chunks],
    }


def _verify_stage(dataset, chunk):
    """Replay one receipt-complete source stage and compare page identities/payloads."""
    start, end = chunk.start_date, chunk.end_date
    if dataset == "bid_notice_goods":
        return vnext_stability.verify_checkpoint_source(
            dataset=dataset, scope=chunk.scope,
            fetch=lambda page, size: bid_vnext.fetch_page("goods", start, end, page=page, rows=size),
            identity=bid_vnext._source_key,
        )
    if dataset == "bid_notice_service":
        return vnext_stability.verify_checkpoint_source(
            dataset=dataset, scope=chunk.scope,
            fetch=lambda page, size: bid_vnext.fetch_page("service", start, end, page=page, rows=size),
            identity=bid_vnext._source_key,
        )
    if dataset == "opening_result_service":
        return vnext_stability.verify_checkpoint_source(
            dataset=dataset, scope=chunk.scope,
            fetch=lambda page, size: award_vnext.fetch_page("opening", start, end, page=page, rows=size),
            identity=award_vnext._raw_source_key,
        )
    if dataset == "award_result_service":
        return vnext_stability.verify_checkpoint_source(
            dataset=dataset, scope=chunk.scope,
            fetch=lambda page, size: award_vnext.fetch_page("award", start, end, page=page, rows=size),
            identity=award_vnext._raw_source_key,
        )
    if dataset == "contract_service":
        return vnext_stability.verify_checkpoint_source(
            dataset=dataset, scope=chunk.scope,
            fetch=lambda page, size: contract_vnext.fetch_page(start, end, page=page, rows=size),
            identity=contract_vnext._source_key,
        )
    if dataset == "shopping_delivery":
        return vnext_stability.verify_checkpoint_source(
            dataset=dataset, scope=chunk.scope,
            fetch=lambda page, size: shopping_vnext.fetch_page(start, end, page=page, rows=size),
            identity=shopping_vnext._source_key,
        )
    raise ValueError(f"unsupported stability dataset: {dataset}")


def checkpoint_status(dataset, chunk):
    row = get_checkpoint(dataset, chunk.scope)
    if not row:
        return {
            "dataset": dataset,
            "scope": chunk.scope,
            "status": "NOT_STARTED",
            "receipt_complete": False,
            "stability_verified": False,
            "complete": False,
            "source_total": 0,
            "fetched_count": 0,
            "saved_count": 0,
            "page_no": 0,
            "last_error": "",
        }
    source_total = int(row.get("source_total") or 0)
    fetched = int(row.get("fetched_count") or 0)
    status = str(row.get("status") or "")
    receipt_complete = verified_checkpoint(row)
    stable = vnext_stability.stability_verified_checkpoint(row)
    return {
        "dataset": dataset,
        "scope": chunk.scope,
        "status": status,
        "receipt_complete": receipt_complete,
        "stability_verified": stable,
        "complete": stable,
        "source_total": source_total,
        "fetched_count": fetched,
        "saved_count": int(row.get("saved_count") or 0),
        "page_no": int(row.get("page_no") or 0),
        "last_error": str(row.get("last_error") or ""),
    }


def audit_backfill(start_date, end_date, *, chunk_days=DEFAULT_CHUNK_DAYS):
    """Audit receipt + source-stability completeness without issuing API requests."""
    chunks = list(iter_date_chunks(start_date, end_date, chunk_days=chunk_days))
    records = []
    for chunk in chunks:
        for dataset, _ in STAGES:
            records.append(checkpoint_status(dataset, chunk))
    counts = {"COMPLETE": 0, "RUNNING": 0, "FAILED": 0, "NOT_STARTED": 0, "OTHER": 0}
    for record in records:
        status = record["status"]
        if status in counts:
            counts[status] += 1
        else:
            counts["OTHER"] += 1
    receipt_complete = sum(1 for r in records if r["receipt_complete"])
    stable_complete = sum(1 for r in records if r["complete"])
    return {
        "chunk_count": len(chunks),
        "stage_count": len(STAGES),
        "expected_units": len(records),
        "receipt_complete_units": receipt_complete,
        "complete_units": stable_complete,
        "all_receipts_complete": bool(records) and receipt_complete == len(records),
        "all_complete": bool(records) and stable_complete == len(records),
        "status_counts": counts,
        "records": records,
    }


def raw_row_counts():
    """Return only aggregate row counts for vNext date-range datasets."""
    datasets = [name for name, _ in STAGES]
    with connect() as conn:
        out = {}
        for dataset in datasets:
            row = conn.execute("SELECT COUNT(*) AS n FROM raw_records WHERE dataset=?", (dataset,)).fetchone()
            out[dataset] = int(row["n"] if row else 0)
        return out


def run_backfill(start_date, end_date, *, chunk_days=DEFAULT_CHUNK_DAYS,
                 page_size=999, max_pages_per_stage=None, allow_live=False,
                 canary_approval=None, stop_on_incomplete=True):
    """Collect, then replay-verify every historical source stage before advancing.

    Live collection requires both an explicit boolean and a recent sanitized bounded
    canary approval. ``max_pages_per_stage`` budgets collection pages. Replay
    verification runs only after a receipt-complete unit and may issue one read per
    stored page; this is a deliberate correctness gate before normalization.
    """
    if not allow_live:
        raise RuntimeError("historical live collection is locked until canary verification; pass allow_live=True explicitly")
    approval = require_canary_approval(canary_approval)

    results = []
    for chunk in iter_date_chunks(start_date, end_date, chunk_days=chunk_days):
        for dataset, runner in STAGES:
            before = checkpoint_status(dataset, chunk)
            if before["complete"]:
                results.append({**before, "action": "SKIPPED_STABLE_COMPLETE"})
                continue

            if before["receipt_complete"]:
                stability = _verify_stage(dataset, chunk)
                after = checkpoint_status(dataset, chunk)
                results.append({**after, "action": "VERIFIED_SOURCE_STABILITY", "stability": stability})
                if stop_on_incomplete and not after["complete"]:
                    return {
                        "complete": False,
                        "stopped_on": {"dataset": dataset, "scope": chunk.scope},
                        "approval": approval,
                        "results": results,
                    }
                continue

            result = runner(
                chunk.start_date,
                chunk.end_date,
                page_size=int(page_size),
                max_pages=max_pages_per_stage,
                resume=True,
            )
            receipt_state = checkpoint_status(dataset, chunk)
            stability = None
            if receipt_state["receipt_complete"]:
                stability = _verify_stage(dataset, chunk)
            after = checkpoint_status(dataset, chunk)
            results.append({**after, "action": "COLLECTED", "collector_result": result,
                            "stability": stability})
            if stop_on_incomplete and not after["complete"]:
                return {
                    "complete": False,
                    "stopped_on": {"dataset": dataset, "scope": chunk.scope},
                    "approval": approval,
                    "results": results,
                }
    audit = audit_backfill(start_date, end_date, chunk_days=chunk_days)
    return {"complete": audit["all_complete"], "audit": audit, "approval": approval,
            "results": results}


def finalize_backfill(start_date, end_date, *, chunk_days=DEFAULT_CHUNK_DAYS,
                      normalize_limit=None, classify_batch_size=1000):
    """Normalize/classify only after receipt and source-stability proof for every unit."""
    audit = audit_backfill(start_date, end_date, chunk_days=chunk_days)
    if not audit["all_complete"]:
        raise RuntimeError(
            f"historical RAW is incomplete or unstable: {audit['complete_units']}/{audit['expected_units']} units stable"
        )

    first_rank = award_projection.normalize_dataset(
        award_projection.OPENING_DATASET, limit=normalize_limit,
    )
    final_award = award_projection.normalize_dataset(
        award_projection.AWARD_DATASET, limit=normalize_limit,
    )
    contract_link = contract_projection.normalize_contracts(limit=normalize_limit)
    complete = not any(r.get('errors') or r.get('pending', 0) for r in (first_rank, final_award, contract_link))
    classification = classification_vnext.classify_all(batch_size=classify_batch_size) if complete else {"status": "BLOCKED_NORMALIZATION_PENDING"}
    return {
        "complete": complete,
        "audit": audit,
        "first_rank": first_rank,
        "final_award": final_award,
        "contract_link": contract_link,
        "classification": classification,
    }
