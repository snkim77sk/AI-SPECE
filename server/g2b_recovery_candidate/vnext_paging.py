"""Shared conservative pagination rules for vNext source collectors.

The v2 transactional collector uses ``source_page_complete`` directly.  The older
helper functions remain part of the public vNext surface so existing callers/tests
keep the same fail-closed behaviour while the receipt-backed collector is adopted.
"""
from __future__ import annotations

import hashlib
import json


class PaginationInvariantError(RuntimeError):
    """Raised when a source response cannot safely advance a resumable cursor."""


def source_page_complete(batch_count, page_size, fetched_count, source_total):
    """Return True only when source exhaustion is proven.

    ``None`` means the source did not provide a total.  Explicit zero with non-empty
    rows is treated as unknown because some source variants report zero while still
    returning data.  A positive total is authoritative and must be reached exactly.
    """
    batch = max(0, int(batch_count or 0))
    size = max(1, int(page_size or 1))
    fetched = max(0, int(fetched_count or 0))
    total = int(source_total) if source_total is not None else -1
    if total > 0:
        return fetched == total
    return batch < size


def assert_page_consistency(batch_count, fetched_count, source_total):
    """Reject a premature empty page when a positive source total remains."""
    batch = max(0, int(batch_count or 0))
    fetched = max(0, int(fetched_count or 0))
    total = int(source_total or 0)
    if total > 0 and fetched < total and batch == 0:
        raise PaginationInvariantError(
            f"source_total={total} but source returned an empty page after fetched={fetched}"
        )


def page_fingerprint(rows):
    """Stable fingerprint for compatibility/repeated-page diagnostics."""
    normalized = rows if isinstance(rows, list) else list(rows or [])
    text = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if normalized else ""


def assert_not_repeated_page(previous_fingerprint, current_fingerprint, batch_count):
    """Fail closed when the next non-empty page exactly repeats the previous page."""
    if int(batch_count or 0) <= 0:
        return
    previous = str(previous_fingerprint or "")
    current = str(current_fingerprint or "")
    if previous and current and previous == current:
        raise PaginationInvariantError(
            "source repeated the previous non-empty page; refusing to advance"
        )


def validate_resume_page_size(checkpoint, page_size):
    """A legacy page-number checkpoint is valid only with the same page size."""
    if not checkpoint or str(checkpoint.get("status") or "") == "COMPLETE":
        return
    stored = int(checkpoint.get("page_size") or 0)
    current = max(1, int(page_size or 1))
    if stored and stored != current:
        raise PaginationInvariantError(
            f"resume page_size mismatch: checkpoint={stored}, requested={current}"
        )
