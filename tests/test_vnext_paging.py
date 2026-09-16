from vnext_paging import source_page_complete


def test_empty_page_is_complete_even_without_total():
    assert source_page_complete(0, 999, 0, 0) is True


def test_positive_total_requires_fetched_to_reach_total():
    assert source_page_complete(999, 999, 999, 1000) is False
    assert source_page_complete(1, 999, 1000, 1000) is True


def test_missing_total_accepts_short_page_as_end_of_range():
    assert source_page_complete(37, 999, 37, 0) is True


def test_missing_total_full_page_never_claims_complete():
    assert source_page_complete(999, 999, 999, 0) is False
    assert source_page_complete(1000, 1000, 1000, 0) is False
