from contextlib import contextmanager
import datetime as dt
import json

import pytest

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
        shopping_recent_vnext.shopping_vnext,
        "prepare_collection_storage",
        lambda: seen.append(("prepare_storage",)),
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "operational_recent_source_context",
        lambda **kw: _context_recorder(seen, **kw),
    )
    if complete is None:
        monkeypatch.setattr(
            shopping_recent_vnext,
            "_already_complete",
            lambda day, **kwargs: False,
        )
    else:
        monkeypatch.setattr(
            shopping_recent_vnext,
            "_already_complete",
            lambda day, **kwargs: day.isoformat() in complete,
        )
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *a, **k: {"classified": 1},
    )


def test_collect_forward_clamps_to_jan1_2026_bootstrap(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)

    def collect(start, end, **kwargs):
        seen.append(("collect", start, end, kwargs["resume"]))
        return _complete_result(start, end)

    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        collect,
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2025-12-01",
        latest_date="2026-01-02",
        max_days=2,
    )

    assert result["requested_start_date"] == "2025-12-01"
    assert result["start_date"] == "2026-01-01"
    assert [row["date"] for row in result["results"]] == [
        "2026-01-01", "2026-01-02"
    ]


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


def test_multi_day_run_prepares_storage_once_and_reuses_it(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)
    collect_flags = []

    def collect(start, end, **kwargs):
        collect_flags.append(kwargs.get("storage_prepared"))
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        defer_classification=True,
    )

    assert result["status"] == "COMPLETE"
    assert seen.count(("prepare_storage",)) == 1
    assert collect_flags == [True, True, True]


def _wire_recheck_state(monkeypatch):
    state = {}

    def get_setting(name, default=""):
        return state.get(str(name), default)

    def set_setting(name, value):
        state[str(name)] = str(value)

    monkeypatch.setattr(shopping_recent_vnext, "get_setting", get_setting)
    monkeypatch.setattr(shopping_recent_vnext, "set_setting", set_setting)
    return state


def test_recent_complete_days_recheck_once_per_kst_day(monkeypatch):
    seen = []
    completed = {"2026-09-01", "2026-09-02", "2026-09-03"}
    _wire(monkeypatch, seen, complete=completed)
    state = _wire_recheck_state(monkeypatch)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 3),
    )
    calls = []

    def collect(start, end, **kwargs):
        calls.append((start, kwargs["resume"]))
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    first = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        recheck_days=2,
        defer_classification=True,
    )
    assert first["status"] == "COMPLETE"
    assert first["results"] == []
    assert [row["date"] for row in first["rechecks"]] == [
        "2026-09-02", "2026-09-03"
    ]
    assert calls == [("2026-09-02", False), ("2026-09-03", False)]
    assert "2026-09-02" in state[shopping_recent_vnext.RECHECK_STATE_KEY]
    assert "2026-09-03" in state[shopping_recent_vnext.RECHECK_STATE_KEY]

    calls.clear()
    second = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        recheck_days=2,
        defer_classification=True,
    )
    assert second["status"] == "COMPLETE"
    assert second["rechecks"] == []
    assert calls == []

    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 4),
    )
    third = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        recheck_days=2,
        defer_classification=True,
    )
    assert [row["date"] for row in third["rechecks"]] == [
        "2026-09-02", "2026-09-03"
    ]
    assert calls == [("2026-09-02", False), ("2026-09-03", False)]


def test_freshly_collected_recent_day_is_not_immediately_rechecked(monkeypatch):
    seen = []
    completed = {"2026-09-01", "2026-09-02"}
    _wire(monkeypatch, seen, complete=completed)
    _wire_recheck_state(monkeypatch)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 3),
    )
    calls = []

    def collect(start, end, **kwargs):
        calls.append((start, kwargs["resume"]))
        completed.add(start)
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        recheck_days=2,
        defer_classification=True,
    )

    assert result["status"] == "COMPLETE"
    assert [row["date"] for row in result["results"]] == ["2026-09-03"]
    assert [row["date"] for row in result["rechecks"]] == ["2026-09-02"]
    assert calls == [("2026-09-03", True), ("2026-09-02", False)]


def test_recent_recheck_never_steals_quota_while_backlog_remains(monkeypatch):
    seen = []
    completed = set()
    _wire(monkeypatch, seen, complete=completed)
    _wire_recheck_state(monkeypatch)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 3),
    )
    calls = []

    def collect(start, end, **kwargs):
        calls.append((start, kwargs["resume"]))
        completed.add(start)
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=1,
        recheck_days=3,
        defer_classification=True,
    )

    assert result["status"] == "PARTIAL"
    assert [row["date"] for row in result["results"]] == ["2026-09-01"]
    assert result["rechecks"] == []
    assert calls == [("2026-09-01", True)]


def test_retention_floor_moves_forward_after_one_year():
    assert shopping_recent_vnext._retention_start_day(
        dt.date(2026, 9, 1),
        dt.date(2027, 10, 3),
        365,
    ) == dt.date(2026, 10, 3)
    assert shopping_recent_vnext._retention_start_day(
        dt.date(2026, 9, 1),
        dt.date(2026, 10, 3),
        365,
    ) == dt.date(2026, 9, 1)


def test_forward_collection_never_refetches_before_retention_floor(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2027, 10, 3),
    )
    calls = []

    def collect(start, end, **kwargs):
        calls.append(start)
        return _complete_result(start, end)

    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        collect,
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2027-10-02",
        max_days=1,
        retention_days=365,
        defer_classification=True,
    )

    assert result["start_date"] == "2026-10-03"
    assert result["requested_start_date"] == "2026-09-01"
    assert result["retention_start_date"] == "2026-10-03"
    assert calls == ["2026-10-03"]
    assert result["status"] == "PARTIAL"


def test_purged_complete_checkpoint_before_floor_never_reopens_baseline(monkeypatch):
    import shopping_store_v41
    import shopping_vnext
    import vnext_collection
    import vnext_store

    monkeypatch.setattr(
        shopping_vnext,
        "compact_complete_enabled",
        lambda: True,
    )

    def fetch(start_date, end_date, page=1, rows=999):
        source_date = str(start_date)
        return (
            [{
                "dlvrReqNo": "RET-BASE-" + source_date,
                "dlvrReqChgOrd": "0",
                "prdctSno": "1",
                "dlvrReqRcptDate": source_date.replace("-", ""),
                "dtilPrdctClsfcNo": "3911160302",
                "prdctNm": "LED retention baseline",
            }],
            1,
        )

    monkeypatch.setattr(shopping_vnext, "fetch_page", fetch)

    for day in ("2026-10-02", "2026-10-03"):
        collected = shopping_vnext.collect_all(
            day,
            day,
            page_size=1,
            max_pages=1,
            resume=False,
        )
        assert collected["complete"] is True

    old_scope = "2026-10-02:2026-10-02"
    floor_scope = "2026-10-03:2026-10-03"
    assert vnext_store.get_checkpoint("shopping_delivery", old_scope) is not None
    floor_cp = vnext_store.get_checkpoint("shopping_delivery", floor_scope)
    assert vnext_collection.verified_compact_completion(floor_cp) is True

    purged = shopping_store_v41.purge_history(
        365,
        now=dt.datetime(
            2027, 10, 3, 12, 0,
            tzinfo=dt.timezone(dt.timedelta(hours=9)),
        ),
    )
    assert purged["cutoff_date"] == "2026-10-03"
    assert vnext_store.get_checkpoint("shopping_delivery", old_scope) is None
    floor_cp = vnext_store.get_checkpoint("shopping_delivery", floor_scope)
    assert vnext_collection.verified_compact_completion(floor_cp) is True

    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2027, 10, 3),
    )
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("retention-expired COMPLETE day must never be refetched")
        ),
    )
    monkeypatch.setattr(
        shopping_recent_vnext.classification_vnext,
        "classify_dataset",
        lambda *args, **kwargs: {"classified": 0},
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-10-03",
        max_days=1,
        recheck_days=0,
        longtail_recheck_days_per_run=0,
        retention_days=365,
        defer_classification=True,
    )

    assert result["status"] == "COMPLETE"
    assert result["start_date"] == "2026-10-03"
    assert result["retention_start_date"] == "2026-10-03"
    assert result["results"] == []
    assert result["rechecks"] == []
    assert result["longtail_rechecks"] == []


def test_longtail_window_starts_at_retention_floor():
    floor = shopping_recent_vnext._retention_start_day(
        dt.date(2026, 9, 1),
        dt.date(2027, 10, 3),
        365,
    )
    window = shopping_recent_vnext._longtail_window(
        floor,
        dt.date(2027, 10, 2),
        7,
    )
    assert window == (
        dt.date(2026, 10, 3),
        dt.date(2027, 9, 25),
    )


def test_recheck_window_is_hard_capped_at_seven_days():
    window = shopping_recent_vnext._recheck_window(
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 20),
        999,
    )
    assert len(window) == 7
    assert window[0] == dt.date(2026, 9, 14)
    assert window[-1] == dt.date(2026, 9, 20)


def test_longtail_recheck_rotates_two_old_dates_once_per_kst_day(monkeypatch):
    seen = []
    completed = {
        f"2026-09-{day:02d}"
        for day in range(1, 13)
    }
    _wire(monkeypatch, seen, complete=completed)
    state = _wire_recheck_state(monkeypatch)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 3),
    )
    state[shopping_recent_vnext.RECHECK_STATE_KEY] = json.dumps({
        "run_date": "2026-10-03",
        "dates": ["2026-09-10", "2026-09-11", "2026-09-12"],
    })
    calls = []

    def collect(start, end, **kwargs):
        calls.append((start, kwargs["resume"]))
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    first = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-12",
        max_days=12,
        recheck_days=3,
        longtail_recheck_days_per_run=999,
        defer_classification=True,
    )

    assert first["status"] == "COMPLETE"
    assert first["results"] == []
    assert first["rechecks"] == []
    assert [row["date"] for row in first["longtail_rechecks"]] == [
        "2026-09-01", "2026-09-02"
    ]
    assert calls == [("2026-09-01", False), ("2026-09-02", False)]
    longtail_state = json.loads(
        state[shopping_recent_vnext.LONGTAIL_RECHECK_STATE_KEY]
    )
    assert longtail_state == {
        "run_date": "2026-10-03",
        "next_date": "2026-09-03",
        "dates": ["2026-09-01", "2026-09-02"],
    }

    calls.clear()
    second = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-12",
        max_days=12,
        recheck_days=3,
        longtail_recheck_days_per_run=2,
        defer_classification=True,
    )
    assert second["longtail_rechecks"] == []
    assert calls == []

    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 4),
    )
    state[shopping_recent_vnext.RECHECK_STATE_KEY] = json.dumps({
        "run_date": "2026-10-04",
        "dates": ["2026-09-10", "2026-09-11", "2026-09-12"],
    })
    third = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-12",
        max_days=12,
        recheck_days=3,
        longtail_recheck_days_per_run=2,
        defer_classification=True,
    )
    assert [row["date"] for row in third["longtail_rechecks"]] == [
        "2026-09-03", "2026-09-04"
    ]
    assert calls == [("2026-09-03", False), ("2026-09-04", False)]


def test_longtail_daily_cap_survives_restart_after_one_success(monkeypatch):
    seen = []
    completed = {
        f"2026-09-{day:02d}"
        for day in range(1, 13)
    }
    _wire(monkeypatch, seen, complete=completed)
    state = _wire_recheck_state(monkeypatch)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 3),
    )
    state[shopping_recent_vnext.RECHECK_STATE_KEY] = json.dumps({
        "run_date": "2026-10-03",
        "dates": ["2026-09-10", "2026-09-11", "2026-09-12"],
    })
    state[shopping_recent_vnext.LONGTAIL_RECHECK_STATE_KEY] = json.dumps({
        "run_date": "2026-10-03",
        "next_date": "2026-09-02",
        "dates": ["2026-09-01"],
    })
    calls = []

    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        lambda start, end, **kwargs: (
            calls.append((start, kwargs["resume"]))
            or _complete_result(start, end)
        ),
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-12",
        max_days=12,
        recheck_days=3,
        longtail_recheck_days_per_run=2,
        defer_classification=True,
    )

    assert [row["date"] for row in result["longtail_rechecks"]] == [
        "2026-09-02"
    ]
    assert calls == [("2026-09-02", False)]
    persisted = json.loads(
        state[shopping_recent_vnext.LONGTAIL_RECHECK_STATE_KEY]
    )
    assert persisted["dates"] == ["2026-09-01", "2026-09-02"]
    assert persisted["next_date"] == "2026-09-03"


def test_longtail_recheck_never_runs_after_baseline_work_in_same_run(monkeypatch):
    seen = []
    completed = {"2026-09-01", "2026-09-02"}
    _wire(monkeypatch, seen, complete=completed)
    _wire_recheck_state(monkeypatch)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 3),
    )
    calls = []

    def collect(start, end, **kwargs):
        calls.append((start, kwargs["resume"]))
        completed.add(start)
        return _complete_result(start, end)

    monkeypatch.setattr(shopping_recent_vnext.shopping_vnext, "collect_all", collect)

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-01",
        latest_date="2026-09-03",
        max_days=3,
        recheck_days=0,
        longtail_recheck_days_per_run=2,
        defer_classification=True,
    )

    assert result["status"] == "COMPLETE"
    assert [row["date"] for row in result["results"]] == ["2026-09-03"]
    assert result["longtail_rechecks"] == []
    assert calls == [("2026-09-03", True)]


def test_longtail_window_excludes_recent_recheck_window():
    window = shopping_recent_vnext._longtail_window(
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 20),
        7,
    )
    assert window == (
        dt.date(2026, 9, 1),
        dt.date(2026, 9, 13),
    )


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


def test_compact_completion_fast_skip_avoids_receipt_scan(monkeypatch):
    checkpoint = {"dataset": "shopping_delivery", "status": "COMPLETE"}
    monkeypatch.setattr(
        shopping_recent_vnext,
        "get_checkpoint",
        lambda dataset, scope, **kwargs: checkpoint,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "verified_compact_completion",
        lambda cp: cp is checkpoint,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "verified_terminal_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("compact marker must bypass receipt scan")
        ),
    )

    assert shopping_recent_vnext._already_complete(
        dt.date(2026, 9, 1),
        storage_prepared=True,
    ) is True


def test_existing_verified_receipt_is_compacted_once_in_production(monkeypatch):
    checkpoint = {"dataset": "shopping_delivery", "status": "COMPLETE"}
    calls = []
    monkeypatch.setattr(
        shopping_recent_vnext,
        "get_checkpoint",
        lambda dataset, scope, **kwargs: checkpoint,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "verified_compact_completion",
        lambda cp: False,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "verified_terminal_receipt",
        lambda cp, **kwargs: True,
    )
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "compact_complete_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "compact_verified_terminal_receipt",
        lambda cp, **kwargs: calls.append((cp, kwargs)) or True,
    )

    assert shopping_recent_vnext._already_complete(
        dt.date(2026, 9, 1),
        storage_prepared=True,
    ) is True
    assert len(calls) == 1
    assert calls[0][0] is checkpoint
    assert calls[0][1]["receipt_verified"] is True
    assert calls[0][1]["schema_prepared"] is True


def test_terminal_checkpoint_stays_complete_after_later_raw_revision(monkeypatch):
    checkpoint = {"status": "COMPLETE"}
    monkeypatch.setattr(
        shopping_recent_vnext,
        "get_checkpoint",
        lambda dataset, scope, **kwargs: checkpoint,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "verified_terminal_receipt",
        lambda cp, **kwargs: cp is checkpoint,
    )
    assert shopping_recent_vnext._already_complete(dt.date(2026, 9, 1)) is True


def test_forward_run_repairs_stale_classification_even_when_all_dates_are_complete(monkeypatch):
    calls = []
    monkeypatch.setattr(shopping_recent_vnext, "_status", lambda *a, **k: None)
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "prepare_collection_storage",
        lambda: None,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_already_complete",
        lambda day, **kwargs: True,
    )
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
        "prepare_collection_storage",
        lambda: None,
    )
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
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_already_complete",
        lambda day, **kwargs: False,
    )
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
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "prepare_collection_storage",
        lambda: None,
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_already_complete",
        lambda day, **kwargs: True,
    )
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

def test_overlap_checkpoint_adapts_page_size_and_running_resume_keeps_it(monkeypatch):
    checkpoint = {
        "status": "INCOMPLETE",
        "last_error": "REPEATED_OR_OVERLAPPING_PAGE",
        "page_size": 999,
    }
    monkeypatch.setattr(
        shopping_recent_vnext,
        "get_checkpoint",
        lambda *args, **kwargs: dict(checkpoint),
    )
    day = dt.date(2026, 9, 4)

    assert shopping_recent_vnext._collection_page_size(
        day, 999, storage_prepared=True
    ) == 500

    checkpoint["page_size"] = 500
    assert shopping_recent_vnext._collection_page_size(
        day, 999, storage_prepared=True
    ) == 250

    checkpoint["page_size"] = 250
    assert shopping_recent_vnext._collection_page_size(
        day, 999, storage_prepared=True
    ) == 250

    checkpoint.update(
        status="RUNNING",
        last_error="",
        page_size=500,
    )
    assert shopping_recent_vnext._collection_page_size(
        day, 999, storage_prepared=True
    ) == 500


def test_collect_forward_uses_adaptive_overlap_page_size(monkeypatch):
    seen = []
    _wire(monkeypatch, seen)
    monkeypatch.setattr(
        shopping_recent_vnext,
        "_kst_today",
        lambda: dt.date(2026, 10, 3),
    )
    monkeypatch.setattr(
        shopping_recent_vnext,
        "get_checkpoint",
        lambda *args, **kwargs: {
            "status": "INCOMPLETE",
            "last_error": "REPEATED_OR_OVERLAPPING_PAGE",
            "page_size": 999,
        },
    )
    page_sizes = []

    def collect(start, end, **kwargs):
        page_sizes.append(kwargs["page_size"])
        return _complete_result(start, end)

    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "collect_all",
        collect,
    )

    result = shopping_recent_vnext.collect_forward(
        start_date="2026-09-04",
        latest_date="2026-09-04",
        max_days=1,
        retention_days=365,
        defer_classification=True,
    )

    assert result["status"] == "COMPLETE"
    assert page_sizes == [500]



def test_collection_storage_prepare_failure_is_persisted(monkeypatch):
    statuses = []

    monkeypatch.setattr(
        shopping_recent_vnext,
        "_status",
        lambda name, value: statuses.append((name, value)),
    )
    monkeypatch.setattr(
        shopping_recent_vnext.shopping_vnext,
        "prepare_collection_storage",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic prepare lock")),
    )

    with pytest.raises(RuntimeError, match="synthetic prepare lock"):
        shopping_recent_vnext.collect_forward(
            start_date="2026-01-01",
            latest_date="2026-01-01",
            max_days=1,
        )

    assert ("state", "FAILED") in statuses
    assert ("last_error", "PREPARE:RuntimeError") in statuses
    assert any(name == "last_finished_at_kst" for name, _value in statuses)
