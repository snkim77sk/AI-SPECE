"""V4.1.299 — D-1 delivery source must not be starved by Jan backlog.

Synthetic only. Existing baseline receipts, page boundaries, source quota and
PG safety contracts are not altered; this tests the schedule's day ordering.
"""
import datetime as dt
from contextlib import contextmanager
import inspect

import pytest

import shopping_recent_vnext as recent
import vnext_clean_app as app


def _complete(iso):
    return {
        "dataset": "shopping_delivery", "scope": f"{iso}:{iso}",
        "fetched": 1, "saved": 1, "source_total": 1,
        "complete": True, "status": "COMPLETE", "resumed": False,
    }


def _partial(iso):
    return {
        "dataset": "shopping_delivery", "scope": f"{iso}:{iso}",
        "fetched": 1, "saved": 1, "source_total": 999,
        "complete": False, "status": "PARTIAL", "resumed": True,
    }


def _prepare(monkeypatch, completed, calls, *, blocked_date=None, statuses=None):
    statuses = statuses if statuses is not None else []
    monkeypatch.setattr(recent, "_status", lambda key, val: statuses.append((key, val)))
    monkeypatch.setattr(recent.shopping_vnext, "prepare_collection_storage",
                        lambda: None)
    monkeypatch.setattr(recent.shopping_vnext, "migrate_legacy_source_keys",
                        lambda: None)
    monkeypatch.setattr(recent.classification_vnext, "classify_dataset",
                        lambda *a, **k: {"classified": 0})
    monkeypatch.setattr(recent, "_already_complete",
                        lambda day, **kw: day.isoformat() in completed)
    monkeypatch.setattr(recent, "_collection_page_size", lambda *a, **kw: 999)
    @contextmanager
    def ctx(*, collection_date, max_requests):
        assert 0 < max_requests <= 64
        assert dt.date.fromisoformat(collection_date).year == 2026
        yield {}
    monkeypatch.setattr(recent, "operational_recent_source_context", ctx)

    def collect(start, end, **kw):
        assert start == end
        assert kw["resume"] is True
        calls.append(start)
        if blocked_date and start == blocked_date:
            return _partial(start)
        completed.add(start)
        return _complete(start)

    monkeypatch.setattr(recent.shopping_vnext, "collect_all", collect)


def test_yesterday_runs_before_stuck_january_backlog_and_preserves_partial(monkeypatch):
    completed = {"2026-01-01", "2026-01-02", "2026-01-03",
                 "2026-01-04", "2026-01-05", "2026-01-06"}
    calls, statuses = [], []
    _prepare(monkeypatch, completed, calls, blocked_date="2026-01-07",
             statuses=statuses)

    result = recent.collect_forward(
        start_date="2026-01-01", latest_date="2026-10-13",
        max_days=62, prioritize_latest_day=True, defer_classification=True,
    )
    assert result["status"] == "PARTIAL"
    assert calls == ["2026-10-13", "2026-01-07"]
    assert "2026-10-13" in completed
    assert "2026-01-07" not in completed
    assert ("daily_priority_last_completed_date", "2026-10-13") in statuses
    assert result["results"][0]["date"] == "2026-10-13"
    assert result["results"][1]["date"] == "2026-01-07"


def test_next_kst_day_d1_runs_even_if_same_old_backlog_is_partial(monkeypatch):
    completed = {"2026-01-01", "2026-10-13"}
    calls = []
    _prepare(monkeypatch, completed, calls, blocked_date="2026-01-02")

    result = recent.collect_forward(
        start_date="2026-01-01", latest_date="2026-10-14",
        max_days=1, prioritize_latest_day=True, defer_classification=True,
    )
    assert calls == ["2026-10-14"]
    assert result["status"] == "PARTIAL"
    assert result["results"][0]["complete"] is True
    assert "2026-10-14" in completed


def test_when_yesterday_is_complete_oldest_unfinished_receipt_runs_first(monkeypatch):
    completed = {"2026-09-01", "2026-09-03"}
    calls = []
    _prepare(monkeypatch, completed, calls)
    result = recent.collect_forward(
        start_date="2026-09-01", latest_date="2026-09-03",
        max_days=1, prioritize_latest_day=True, defer_classification=True,
    )
    assert calls == ["2026-09-02"]
    assert result["status"] == "COMPLETE"


def test_limited_historical_day_budget_still_guarantees_d1_once(monkeypatch):
    completed = set()
    calls = []
    _prepare(monkeypatch, completed, calls)
    result = recent.collect_forward(
        start_date="2026-09-01", latest_date="2026-09-05",
        max_days=2, prioritize_latest_day=True, defer_classification=True,
    )
    assert calls == ["2026-09-05", "2026-09-01"]
    assert result["status"] == "PARTIAL"
    assert len(result["results"]) == 2
    assert "2026-09-02" not in completed


def test_legacy_callers_keep_strict_oldest_first_order(monkeypatch):
    completed=set()
    calls=[]
    _prepare(monkeypatch, completed, calls)
    result = recent.collect_forward(
        start_date="2026-09-01", latest_date="2026-09-03",
        max_days=2, defer_classification=True,
    )
    assert calls == ["2026-09-01", "2026-09-02"]
    assert result["status"] == "PARTIAL"


def test_production_collector_explicitly_sets_priority_with_no_quota_or_data_reset():
    source = inspect.getsource(app._run_recent_collection_once_impl)
    assert "prioritize_latest_day=True" in source
    algorithm = inspect.getsource(recent.collect_forward)
    assert "_already_complete(latest_day, storage_prepared=True)" in algorithm
    assert "day != priority_day" in algorithm
    assert "operational_recent_source_context(" in algorithm
    assert "resume=True" in algorithm
    assert "DROP TABLE" not in algorithm
    assert "TRUNCATE" not in algorithm
