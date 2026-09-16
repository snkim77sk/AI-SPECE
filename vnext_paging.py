"""Shared conservative pagination rules for vNext source collectors."""


def source_page_complete(batch_count, page_size, fetched_count, source_total):
    """Return True only when the source range is safely exhausted.

    Preferred signal is a positive source total. If an API omits totalCount, a short
    page is accepted as end-of-range. A full page with missing/zero total is never
    treated as complete because another page may exist.
    """
    batch = max(0, int(batch_count or 0))
    size = max(1, int(page_size or 1))
    fetched = max(0, int(fetched_count or 0))
    total = max(0, int(source_total or 0))
    if batch == 0:
        return True
    if total > 0:
        return fetched >= total
    return batch < size
