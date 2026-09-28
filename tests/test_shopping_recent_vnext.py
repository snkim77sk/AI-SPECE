from contextlib import contextmanager
import datetime as dt

import shopping_recent_vnext


@contextmanager
def _context_recorder(seen, *, collection_date, max_requests):
    seen.append(("context", collection_date, max_requests))
    yield {}


def _complete_result(start, end):
    return {
        "dataset": "shopping_delivery",
        "scope": f"{start}:{end}",
        "fetched": 1,
        "saved": 1,
        "source_total": 1,
        "complete": True,
        "resumed": False,
        "status": "COMPLETE",
        "reason": "",
        "completion_reason": "TOTAL_REACHED",
    }


def _wire(monkeypatch, seen, complete=None):
    monkeypatch.setattr(shopping_recent_vnext, "_status", lambda *a, **k: None)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "operational_recent_source_context",
        lambda **kw: _context_recorder(seen, **kw),
    )
    if complete is None:
        monkeypatch.setattr(shopping_recent_vnext, "_already_complete", lambda day: False)
    else:
        monkeypatch.setattr(
            shopping_recent_vnext,
            "_already_complete",
            lambda day: day.isoformat() in complete,
        )
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: {"classified": 1},
    )


def test_collect_forward_starts_sep1_and_ascends(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)

    def collect(start, end, **kwargs):
        seen.append(("collect", start, end, kwargs["resume"]))
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
    )

    dates = [row["date"] for row in result["results"]]
    assert dates == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert [row[1] for row in seen if row[0] == "collect"] == dates
    assert result["order"] == "FORWARD"
    assert result["start_date"] == "2026-09-01"
    assert result["status"] == "COMPLETE"


def test_completed_days_do_not_consume_active_day_budget(monkeypatch):
    seen = []
    completed = {"2026-09-01", "2026-09-02"}
    _wire(monkeypatch, seen, complete=completed)

    def collect(start, end, **kwargs):
        seen.append(("collect", start, end, kwargs["resume"]))
        completed.add(start)
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-04",
        max_days=2,
    )

    assert [row["date"] for row in result["results"]] == [
        "2026-09-03", "2026-09-04"
    ]
    assert result["status"] == "COMPLETE"


def test_forward_collection_stops_on_first_partial_day(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda start, end, **kwargs: {
            "dataset": "shopping_delivery",
            "scope": f"{start}:{end}",
            "fetched": 999,
            "saved": 999,
            "source_total": None,
            "complete": False,
            "resumed": False,
            "status": "RUNNING",
            "reason": "",
            "completion_reason": "",
        },
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-05",
        max_days=5,
    )

    assert [row["date"] for row in result["results"]] == ["2026-09-01"]
    assert result["status"] == "PARTIAL"


def test_default_latest_day_is_d_minus_one(monkeypatch):
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 9, 29),
    )
    assert shopping_recent_vnext._latest_available_day() == dt.date(2026, 9, 28)


def test_compatibility_entrypoint_is_forward(monkeypatch):
    seen = []
    monkeypatch.setattr(
        shopping_recent_vnext,
        "collect_forward",
        lambda **kwargs: seen.append(kwargs) or {"order": "FORWARD"},
    )
    result = shopping_recent_vnext.collect_latest_first(
        today="2026-09-29",
        lookback_days=14,
        max_days=7,
    )
    assert result["order"] == "FORWARD"
    assert seen == [{"max_days": 7}]
