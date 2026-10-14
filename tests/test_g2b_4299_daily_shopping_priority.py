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
    assert "day not in selected" in algorithm
    assert "BACKLOG_RECENT_WINDOW_DAYS" in algorithm
    assert "resume=not recent_refresh" in algorithm
    assert "operational_recent_source_context(" in algorithm
    assert "resume=True" in algorithm
    assert "DROP TABLE" not in algorithm
    assert "TRUNCATE" not in algorithm



def _recent_state(monkeypatch):
    """Persistent settings simulation without a production PostgreSQL write."""
    import json
    saved = {}

    def get_setting(key, default=""):
        return saved.get(key, default)

    def set_setting(key, value):
        saved[key] = value

    monkeypatch.setattr(recent, "get_setting", get_setting)
    monkeypatch.setattr(recent, "set_setting", set_setting)
    monkeypatch.setattr(recent, "_kst_today", lambda: dt.date(2026, 10, 14))
    return saved


def test_recent_late_arrivals_rechecked_even_when_january_backlog_partial(monkeypatch):
    """D-1 comes FIRST, then one D-2 refresh, then Jan-07 resume."""
    completed = {f"2026-01-{day:02d}" for day in range(1, 7)}
    completed.add("2026-10-12")
    calls = []
    saved = _recent_state(monkeypatch)
    _prepare(monkeypatch, completed, calls, blocked_date="2026-01-07")
    observed = []

    def collect(start, end, **kw):
        observed.append((start, kw["resume"], kw["max_pages"]))
        calls.append(start)
        assert kw["max_pages"] <= 40
        if start == "2026-01-07":
            return _partial(start)
        completed.add(start)
        return _complete(start)

    monkeypatch.setattr(recent.shopping_vnext, "collect_all", collect)
    result = recent.collect_forward(
        start_date="2026-01-01",
        latest_date="2026-10-13",
        max_days=3,
        recheck_days=7,
        prioritize_latest_day=True,
        defer_classification=True,
    )

    assert calls == ["2026-10-13", "2026-10-12", "2026-01-07"]
    assert observed == [
        ("2026-10-13", True, 40),
        ("2026-10-12", False, 40),
        ("2026-01-07", True, 40),
    ]
    assert [x["date"] for x in result["results"]] == [
        "2026-10-13", "2026-01-07"
    ]
    assert [x["date"] for x in result["rechecks"]] == ["2026-10-12"]
    assert result["status"] == "PARTIAL"
    import json
    check = json.loads(saved[recent.RECHECK_STATE_KEY])
    assert check["run_date"] == "2026-10-14"
    assert check["dates"] == ["2026-10-12"]


def test_completed_yesterday_rechecks_once_per_day_then_next_recent_date(monkeypatch):
    """Completing D-1 at 00KST is NOT proof no late deliveries appear at 04."""
    completed = {"2026-01-01", "2026-10-11", "2026-10-12", "2026-10-13"}
    calls = []
    saved = _recent_state(monkeypatch)
    _prepare(monkeypatch, completed, calls, blocked_date="2026-01-02")
    observed = []

    def collect(start, end, **kw):
        calls.append(start)
        observed.append((start, kw["resume"]))
        return _partial(start) if start == "2026-01-02" else _complete(start)

    monkeypatch.setattr(recent.shopping_vnext, "collect_all", collect)
    for expected in ("2026-10-13", "2026-10-12", "2026-10-11"):
        before = len(observed)
        result = recent.collect_forward(
            start_date="2026-01-01",
            latest_date="2026-10-13",
            max_days=2,
            recheck_days=7,
            prioritize_latest_day=True,
            defer_classification=True,
        )
        assert observed[before:] == [(expected, False), ("2026-01-02", True)]
        assert result["status"] == "PARTIAL"
        assert result["rechecks"][0]["date"] == expected
    # On the fourth visit, all last-three dates have been checked today.
    before = len(observed)
    recent.collect_forward(
        start_date="2026-01-01",
        latest_date="2026-10-13",
        max_days=2,
        recheck_days=7,
        prioritize_latest_day=True,
        defer_classification=True,
    )
    assert observed[before:] == [("2026-01-02", True)]
    import json
    assert json.loads(saved[recent.RECHECK_STATE_KEY])["dates"] == [
        "2026-10-11", "2026-10-12", "2026-10-13"
    ]


def test_recent_incomplete_page_is_resumed_never_refetched_from_zero(monkeypatch):
    completed = {"2026-01-01", "2026-10-13"}
    calls = []
    saved = _recent_state(monkeypatch)
    # The D-1 receipt was rechecked earlier THIS KST day, so the newest
    # remaining candidate is the D-2 incomplete page, which MUST resume.
    saved[recent.RECHECK_STATE_KEY] = (
        '{"run_date":"2026-10-14","dates":["2026-10-13"]}'
    )
    _prepare(monkeypatch, completed, calls, blocked_date="2026-10-12")
    seen = []

    def collect(start, end, **kw):
        seen.append((start, kw["resume"]))
        calls.append(start)
        return _partial(start) if start == "2026-10-12" else _complete(start)

    monkeypatch.setattr(recent.shopping_vnext, "collect_all", collect)
    result = recent.collect_forward(
        start_date="2026-01-01",
        latest_date="2026-10-13",
        max_days=2,
        recheck_days=7,
        prioritize_latest_day=True,
        defer_classification=True,
    )
    assert seen == [("2026-10-12", True)]
    assert result["status"] == "PARTIAL"
    assert result["rechecks"] == []
    assert result["results"][0]["date"] == "2026-10-12"


def test_day_budget_one_never_exceeds_single_new_priority_date(monkeypatch):
    completed = {"2026-10-12"}
    calls = []
    _recent_state(monkeypatch)
    _prepare(monkeypatch, completed, calls)
    result = recent.collect_forward(
        start_date="2026-10-10",
        latest_date="2026-10-13",
        max_days=1,
        recheck_days=7,
        prioritize_latest_day=True,
        defer_classification=True,
    )
    assert calls == ["2026-10-13"]
    assert result["rechecks"] == []
    assert result["status"] == "PARTIAL"


def test_day_rollover_resets_only_recent_rechecks_not_existing_receipts(monkeypatch):
    completed = {"2026-01-01", "2026-10-12", "2026-10-13"}
    calls = []
    saved = _recent_state(monkeypatch)
    _prepare(monkeypatch, completed, calls, blocked_date="2026-01-02")
    # This CI fixture must distinguish an ALREADY-COMPLETE recent-source
    # replay (resume=False) from a NEW source date (resume=True).
    observed = []

    def collect(start, end, **kw):
        observed.append((start, kw["resume"]))
        calls.append(start)
        if start == "2026-01-02":
            assert kw["resume"] is True
            return _partial(start)
        if not kw["resume"]:
            assert start in completed
        else:
            completed.add(start)
        return _complete(start)

    monkeypatch.setattr(recent.shopping_vnext, "collect_all", collect)

    first = recent.collect_forward(
        start_date="2026-01-01",
        latest_date="2026-10-13",
        max_days=2, recheck_days=7,
        prioritize_latest_day=True, defer_classification=True,
    )
    assert first["rechecks"][0]["date"] == "2026-10-13"
    monkeypatch.setattr(recent, "_kst_today", lambda: dt.date(2026, 10, 15))
    second = recent.collect_forward(
        start_date="2026-01-01",
        latest_date="2026-10-14",
        max_days=2, recheck_days=7,
        prioritize_latest_day=True, defer_classification=True,
    )
    assert second["results"][0]["date"] == "2026-10-14"
    assert second["rechecks"][0]["date"] == "2026-10-13"
    assert observed == [
        ("2026-10-13", False),
        ("2026-01-02", True),
        ("2026-10-14", True),
        ("2026-10-13", False),
    ]
    assert "2026-10-12" in completed
    assert "2026-01-02" not in completed
