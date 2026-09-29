import datetime as dt
from types import SimpleNamespace

import pytest

from scripts import local_collector


def _args(start="2026-09-02", end="2026-09-04"):
    return SimpleNamespace(start_date=start, end_date=end)


def test_collection_window_accepts_exact_bounded_range():
    start, end = local_collector._collection_window(
        _args(),
        today=dt.date(2026, 9, 29),
    )
    assert start == dt.date(2026, 9, 2)
    assert end == dt.date(2026, 9, 4)


def test_collection_window_uses_korea_d_minus_one_when_end_omitted():
    start, end = local_collector._collection_window(
        _args(start="2026-09-02", end=""),
        today=dt.date(2026, 9, 29),
    )
    assert start == dt.date(2026, 9, 2)
    assert end == dt.date(2026, 9, 28)


def test_collection_window_rejects_future_end_date():
    with pytest.raises(ValueError, match="Korea D-1"):
        local_collector._collection_window(
            _args(start="2026-09-02", end="2026-09-29"),
            today=dt.date(2026, 9, 29),
        )


def test_collection_window_rejects_reverse_range():
    with pytest.raises(ValueError, match="must not be after"):
        local_collector._collection_window(
            _args(start="2026-09-05", end="2026-09-04"),
            today=dt.date(2026, 9, 29),
        )


def test_collection_window_rejects_invalid_date_format():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        local_collector._collection_window(
            _args(start="2026/09/02", end="2026-09-04"),
            today=dt.date(2026, 9, 29),
        )
