import pytest
from vnext_paging import (PaginationInvariantError, assert_not_repeated_page,
                          assert_page_consistency, page_fingerprint,
                          source_page_complete, validate_resume_page_size)


def test_empty_page_is_complete_only_when_total_unknown_or_satisfied():
    assert source_page_complete(0, 999, 0, 0) is True
    assert source_page_complete(0, 999, 9, 10) is False
    with pytest.raises(PaginationInvariantError):
        assert_page_consistency(0, 9, 10)


def test_positive_total_requires_fetched_to_reach_total():
    assert source_page_complete(999, 999, 999, 1000) is False
    assert source_page_complete(1, 999, 1000, 1000) is True


def test_missing_total_accepts_short_page_but_not_full_page():
    assert source_page_complete(37, 999, 37, 0) is True
    assert source_page_complete(999, 999, 999, 0) is False


def test_repeated_consecutive_page_is_rejected():
    rows = [{"id": 1}, {"id": 2}]
    fp = page_fingerprint(rows)
    with pytest.raises(PaginationInvariantError, match="repeated"):
        assert_not_repeated_page(fp, fp, len(rows))


def test_resume_page_size_must_match_checkpoint():
    cp = {"status": "RUNNING", "page_size": 100}
    validate_resume_page_size(cp, 100)
    with pytest.raises(PaginationInvariantError, match="page_size mismatch"):
        validate_resume_page_size(cp, 200)
