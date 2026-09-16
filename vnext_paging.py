"""Shared conservative pagination rules for vNext source collectors."""


def source_page_complete(batch_count, page_size, fetched_count, source_total):
    batch = max(0, int(batch_count or 0))
    size = max(1, int(page_size or 1))
    fetched = max(0, int(fetched_count or 0))
    total = int(source_total) if source_total is not None else -1
    # A premature empty page cannot override an earlier positive source total.
    if total > 0:
        return fetched == total
    # Explicit zero plus nonempty rows is an inconsistent source total, not proof
    # of completion; treat it as unknown for compatibility with the API variants.
    return batch < size
