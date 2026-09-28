from contextlib import contextmanager

import shopping_recent_vnext


@contextmanager
def _context_recorder(seen, *, collection_date, max_requests):
    seen.append(("context", collection_date, max_requests))
    yield {}


def test_collect_latest_first_descends_by_day(monkeypatch):
    seen = []
    monkeypatch.setattr(shopping_recent_vnext, "_status", lambda *a, **k: None)
    monkeypatch.setattr(shopping_recent_vnext, "_resume_for_day", lambda day, today: True)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "operational_recent_source_context",
        lambda **kw: _context_recorder(seen, **kw),
    )

    def collect(start, end, **kwargs):
        seen.append(("collect", start, end, kwargs["resume"]))
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

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: {"classified": 1},
    )

    result = shopping_recent_vnext.collect_latest_first(
        today="2026-09-29",
        lookback_days=3,
        max_days=3,
    )

    dates = [row["date"] for row in result["results"]]
    assert dates == ["2026-09-29", "2026-09-28", "2026-09-27"]
    collect_dates = [row[1] for row in seen if row[0] == "collect"]
    assert collect_dates == dates
    assert result["order"] == "NEWEST_FIRST"
    assert result["status"] == "COMPLETE"


def test_collect_latest_first_stops_before_older_day_when_current_day_is_partial(monkeypatch):
    seen = []
    monkeypatch.setattr(shopping_recent_vnext, "_status", lambda *a, **k: None)
    monkeypatch.setattr(shopping_recent_vnext, "_resume_for_day", lambda day, today: True)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "operational_recent_source_context",
        lambda **kw: _context_recorder(seen, **kw),
    )
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
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: {"classified": 999},
    )

    result = shopping_recent_vnext.collect_latest_first(
        today="2026-09-29",
        lookback_days=5,
        max_days=5,
    )

    assert [row["date"] for row in result["results"]] == ["2026-09-29"]
    assert result["status"] == "PARTIAL"


def test_default_latest_day_is_d_minus_one(monkeypatch):
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: __import__("datetime").date(2026, 9, 29),
    )
    assert shopping_recent_vnext._as_day() == __import__("datetime").date(2026, 9, 28)
