"""G2B vNext budget collection: collect first, classify later.

This module is additive. The legacy keyword collector remains untouched while vNext
uses an independent 지방재정365 HTTP/parser path.
"""
import datetime as dt
import hashlib

from lofin_vnext_http import SOURCE_NAME, fetch_budget_page
from vnext_paging import source_page_complete
from vnext_store import preserve_raw, save_checkpoint

DATASET = "budget"
SOURCE_OPERATION = "QWGJK_FULL"


def _source_key(row, fiscal_year):
    """Build a stable budget identity from codes, not mutable business names."""
    year = str(row.get("fyr") or fiscal_year or "").strip()
    code_parts = [
        year,
        str(row.get("laf_cd") or "").strip(),
        str(row.get("dept_cd") or "").strip(),
        str(row.get("dbiz_cd") or "").strip(),
        str(row.get("acnt_dv_cd") or "").strip(),
    ]
    # With a business code, title/name changes must remain revisions of the same row.
    if code_parts[3]:
        return hashlib.sha1("|".join(code_parts).encode("utf-8")).hexdigest()
    # Some linked/local rows may omit dbiz_cd. In that case retain the descriptive
    # name as a collision-avoidance fallback rather than merging unrelated projects.
    fallback = code_parts + [str(row.get("dbiz_nm") or "").strip()]
    return hashlib.sha1("|".join(fallback).encode("utf-8")).hexdigest()


def preserve_budget_rows(rows, fiscal_year, snapshot_date):
    """Preserve every returned budget row before any lighting classification."""
    saved = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        preserve_raw(
            DATASET,
            _source_key(row, fiscal_year),
            row,
            source_system=SOURCE_NAME,
            source_operation=SOURCE_OPERATION,
            source_date=str(snapshot_date),
        )
        saved += 1
    return saved


def collect_full_budget(fiscal_year=None, snapshot_date=None, *, page_size=1000, max_pages=None, resume=True):
    """Collect the unfiltered QWGJK result set into RAW storage.

    `dbiz_nm` is deliberately sent as an empty string. No LED/lighting/pole
    keyword is used in the collection decision. `max_pages` is a page budget only:
    reaching it must never mark a partial source range COMPLETE.
    """
    page_size = min(max(int(page_size), 1), 1000)
    year = int(fiscal_year or dt.date.today().year)
    snapshot = snapshot_date or dt.date.today().isoformat()
    scope = f"{year}:{snapshot}"
    page = 1
    fetched = 0
    saved = 0
    source_total = 0
    pages_done = 0

    if resume:
        from vnext_store import get_checkpoint
        checkpoint = get_checkpoint(DATASET, scope)
        if checkpoint and checkpoint.get("status") == "COMPLETE":
            return {
                "dataset": DATASET,
                "scope": scope,
                "source_total": int(checkpoint.get("source_total") or 0),
                "fetched": int(checkpoint.get("fetched_count") or 0),
                "saved": int(checkpoint.get("saved_count") or 0),
                "complete": True,
                "resumed": True,
            }
        if checkpoint and checkpoint.get("status") in ("RUNNING", "FAILED"):
            page = max(1, int(checkpoint.get("page_no") or 1))
            fetched = int(checkpoint.get("fetched_count") or 0)
            saved = int(checkpoint.get("saved_count") or 0)
            source_total = int(checkpoint.get("source_total") or 0)

    save_checkpoint(
        DATASET, scope, page_no=page, range_start=str(year), range_end=str(snapshot),
        source_total=source_total, fetched_count=fetched, saved_count=saved, status="RUNNING",
    )

    try:
        while True:
            rows, total, _code, _message = fetch_budget_page(year, snapshot, "", page=page, size=page_size)
            reported_total = int(total or 0)
            if reported_total > 0:
                source_total = reported_total
            batch_count = len(rows)
            batch_saved = preserve_budget_rows(rows, year, snapshot)
            fetched += batch_count
            saved += batch_saved
            pages_done += 1

            next_page = page + 1
            source_done = source_page_complete(batch_count, page_size, fetched, source_total)
            page_budget_hit = max_pages is not None and pages_done >= int(max_pages)

            save_checkpoint(
                DATASET, scope,
                page_no=(page if source_done else next_page),
                range_start=str(year), range_end=str(snapshot), source_total=source_total,
                fetched_count=fetched, saved_count=saved,
                status=("COMPLETE" if source_done else "RUNNING"),
            )
            if source_done or page_budget_hit:
                return {
                    "dataset": DATASET,
                    "scope": scope,
                    "source_total": source_total,
                    "fetched": fetched,
                    "saved": saved,
                    "complete": bool(source_done),
                }
            page = next_page
    except Exception as exc:
        save_checkpoint(
            DATASET, scope, page_no=page, range_start=str(year), range_end=str(snapshot),
            source_total=source_total, fetched_count=fetched, saved_count=saved,
            status="FAILED", last_error=str(exc)[:1000],
        )
        raise
