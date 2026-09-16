"""Shared conservative pagination rules for vNext source collectors."""
from __future__ import annotations

import hashlib
import json


class PaginationInvariantError(RuntimeError):
    """Raised when a source response cannot safely advance a resumable cursor."""


def source_page_complete(batch_count, page_size, fetched_count, source_total):
    """Return True only when the source range is safely exhausted.

    A positive source total is authoritative and must be satisfied even if a source
    unexpectedly returns an empty page. When totalCount is unavailable/zero, only a
    short page (including a truly empty page) proves end-of-range. A full page never
    proves completion when the source total is unknown.
    """
    batch = max(0, int(batch_count or 0))
    size = max(1, int(page_size or 1))
    fetched = max(0, int(fetched_count or 0))
    total = max(0, int(source_total or 0))
    if total > 0:
        return fetched >= total
    return batch < size


def assert_page_consistency(batch_count, fetched_count, source_total):
    """Reject gaps instead of silently completing/skipping an incomplete source."""
    batch = max(0, int(batch_count or 0))
    fetched = max(0, int(fetched_count or 0))
    total = max(0, int(source_total or 0))
    if total > 0 and fetched < total and batch == 0:
        raise PaginationInvariantError(
            f"source_total={total} but source returned an empty page after fetched={fetched}"
        )


def page_fingerprint(rows):
    """Stable fingerprint used only to detect repeated consecutive source pages."""
    normalized = rows if isinstance(rows, list) else list(rows or [])
    text = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if normalized else ""


def assert_not_repeated_page(previous_fingerprint, current_fingerprint, batch_count):
    """Fail closed when page N+1 is byte-for-byte the same non-empty page as N."""
    if int(batch_count or 0) <= 0:
        return
    previous = str(previous_fingerprint or "")
    current = str(current_fingerprint or "")
    if previous and current and previous == current:
        raise PaginationInvariantError("source repeated the previous non-empty page; refusing to advance")


def validate_resume_page_size(checkpoint, page_size):
    """A page-number checkpoint is valid only with the same effective page size."""
    if not checkpoint or str(checkpoint.get("status") or "") == "COMPLETE":
        return
    stored = int(checkpoint.get("page_size") or 0)
    current = max(1, int(page_size or 1))
    if stored and stored != current:
        raise PaginationInvariantError(
            f"resume page_size mismatch: checkpoint={stored}, requested={current}"
        )
