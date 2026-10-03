from contextlib import contextmanager
import datetime as dt

import shopping_recent_vnext
import vnext_http
import vnext_source_guard


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


def test_initial_32_day_backlog_can_finish_in_one_run(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)

    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda start, end, **kwargs: _complete_result(start, end),
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-10-02",
        max_days=62,
        defer_classification=True,
    )

    assert result["status"] == "COMPLETE"
    assert len(result["results"]) == 32
    assert result["results"][0]["date"] == "2026-09-01"
    assert result["results"][-1]["date"] == "2026-10-02"


def test_per_day_request_budget_is_capped_at_64(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)
    captured = {}

    def collect(start, end, **kwargs):
        captured["max_pages"] = kwargs["max_pages"]
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-01",
        max_days=1,
        max_pages_per_day=999,
        request_budget_per_day=999,
    )

    assert result["status"] == "COMPLETE"
    assert ("context", "2026-09-01", 64) in seen
    assert captured["max_pages"] == 40


def test_local_daily_quota_exhaustion_returns_waiting_quota(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            vnext_http.VNextLocalQuotaReached(
                "LOCAL_QUOTA",
                "synthetic local daily quota",
            )
        ),
    )
    monkeypatch.setattr(
        vnext_http,
        "api_usage",
        lambda kind=None: {
            "date": "2026-10-03",
            "total": 900,
            "limit": 900,
            "kind": "shopping",
            "kind_count": 900,
        },
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=62,
    )

    assert result["status"] == "WAITING_QUOTA"
    assert result["results"] == []
    assert result["quota"]["total"] == 900
    assert result["quota"]["limit"] == 900


def test_per_day_request_budget_exhaustion_returns_partial(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            vnext_source_guard.VNextSourceAccessError(
                "VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED"
            )
        ),
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=62,
    )

    assert result["status"] == "PARTIAL"
    assert result["results"] == []


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


def test_terminal_checkpoint_stays_complete_after_later_raw_revision(monkeypatch):
    checkpoint = {"status": "COMPLETE"}
    monkeypatch.setattr(
        shopping_recent_vnext,
        "get_checkpoint",
        lambda dataset, scope: checkpoint,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "verified_terminal_receipt",
        lambda cp: cp is checkpoint,
    )
    assert shopping_recent_vnext._already_complete(dt.date(2026, 9, 1)) is True


def test_forward_run_repairs_stale_classification_even_when_all_dates_are_complete(monkeypatch):
    calls = []
    monkeypatch.setattr(shopping_recent_vnext, "_status", lambda *a, **k: None)
    monkeypatch.setattr(shopping_recent_vnext, "_already_complete", lambda day: True)
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: calls.append((a, k)) or {"classified": 3},
    )
    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-02",
        max_days=2,
    )
    assert result["status"] == "COMPLETE"
    assert result["results"] == []
    assert calls
    assert result["classification"]["classified"] == 3


def test_forward_runs_identity_migration_before_collection(monkeypatch):
    events = []
    monkeypatch.setattr(shopping_recent_vnext, "_status", lambda *a, **k: None)
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "migrate_legacy_source_keys",
        lambda: events.append("migrate") or {
            "status": "COMPLETE", "migrated_current": 1, "copied_revisions": 2
        },
    )
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: events.append("classify") or {"classified": 2},
    )
    monkeypatch.setattr(shopping_recent_vnext, "_already_complete", lambda day: False)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "operational_recent_source_context",
        lambda **kw: _context_recorder(events, **kw),
    )
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda start, end, **kwargs: (
            events.append("collect") or _complete_result(start, end)
        ),
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-01",
        max_days=1,
    )
    assert events[0] == "migrate"
    assert "collect" in events
    assert events.index("migrate") < events.index("collect")
    assert result["identity_migration"]["migrated_current"] == 1



def test_deferred_classification_runs_once_after_multi_day_collection(monkeypatch):
    seen = []
    classification_calls = []
    _wire(monkeypatch, seen)
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: classification_calls.append((a, k)) or {"classified": 3},
    )
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda start, end, **kwargs: _complete_result(start, end),
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        defer_classification=True,
    )

    assert result["status"] == "COMPLETE"
    assert [row["date"] for row in result["results"]] == [
        "2026-09-01", "2026-09-02", "2026-09-03"
    ]
    assert len(classification_calls) == 1
    assert classification_calls[0][1]["batch_size"] == 5000
    assert result["classification"]["classified"] == 3


def test_deferred_classification_runs_once_before_partial_return(monkeypatch):
    seen = []
    classification_calls = []
    _wire(monkeypatch, seen)
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: classification_calls.append((a, k)) or {"classified": 7},
    )
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda start, end, **kwargs: {
            "dataset": "shopping_delivery",
            "scope": f"{start}:{end}",
            "fetched": 999,
            "saved": 999,
            "source_total": 5000,
            "complete": False,
            "resumed": False,
            "status": "RUNNING",
            "reason": "",
            "completion_reason": "",
        },
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        defer_classification=True,
    )

    assert result["status"] == "PARTIAL"
    assert len(classification_calls) == 1
    assert classification_calls[0][1]["batch_size"] == 5000
    assert result["classification"]["classified"] == 7


def test_deferred_classification_repairs_stale_rows_when_all_dates_complete(monkeypatch):
    calls = []
    monkeypatch.setattr(shopping_recent_vnext, "_status", lambda *a, **k: None)
    monkeypatch.setattr(shopping_recent_vnext, "_already_complete", lambda day: True)
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: calls.append((a, k)) or {"classified": 4},
    )
    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-02",
        max_days=2,
        defer_classification=True,
    )
    assert result["status"] == "COMPLETE"
    assert result["results"] == []
    assert len(calls) == 1
    assert calls[0][1]["batch_size"] == 5000
    assert result["classification"]["classified"] == 4
