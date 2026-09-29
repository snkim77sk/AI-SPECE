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



def _loop_args(interval=5, once=False):
    return SimpleNamespace(interval_minutes=interval, once=once)


def test_local_db_process_lock_blocks_second_process_handle(tmp_path):
    db_path = tmp_path / "g2b-local.sqlite3"
    first = local_collector.LocalDbProcessLock(db_path)
    second = local_collector.LocalDbProcessLock(db_path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_automatic_loop_recovers_after_one_failed_cycle():
    args = _loop_args(interval=5, once=False)
    emitted = []
    sleeps = []
    calls = []

    def cycle(_args):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            return (
                {
                    "status": "FAILED",
                    "error": {"code": "LOCAL_COLLECTOR_COLLECT_FAILED", "type": "RuntimeError"},
                },
                RuntimeError("secret-value-must-not-be-logged"),
            )
        return ({"status": "COMPLETE", "error": None}, None)

    rc = local_collector._run_loop(
        args,
        cycle_fn=cycle,
        sleep_fn=sleeps.append,
        emit_fn=emitted.append,
        max_cycles=2,
    )
    assert rc == 0
    assert [row["status"] for row in emitted] == ["FAILED", "COMPLETE"]
    assert sleeps == [5 * 60]


def test_safe_error_never_contains_exception_message():
    error = local_collector._safe_error(
        "sync",
        RuntimeError("API_KEY=VERY_SECRET_TOKEN"),
    )
    encoded = str(error)
    assert error == {
        "code": "LOCAL_COLLECTOR_SYNC_FAILED",
        "type": "RuntimeError",
    }
    assert "VERY_SECRET_TOKEN" not in encoded
    assert "API_KEY" not in encoded


def test_interval_zero_runs_once_without_sleep():
    args = _loop_args(interval=0, once=False)
    emitted = []
    sleeps = []

    rc = local_collector._run_loop(
        args,
        cycle_fn=lambda _args: ({"status": "COMPLETE"}, None),
        sleep_fn=sleeps.append,
        emit_fn=emitted.append,
    )
    assert rc == 0
    assert emitted == [{"status": "COMPLETE"}]
    assert sleeps == []


def test_once_flag_overrides_interval():
    args = _loop_args(interval=120, once=True)
    emitted = []
    sleeps = []

    rc = local_collector._run_loop(
        args,
        cycle_fn=lambda _args: ({"status": "COMPLETE"}, None),
        sleep_fn=sleeps.append,
        emit_fn=emitted.append,
    )
    assert rc == 0
    assert len(emitted) == 1
    assert sleeps == []


def test_manual_one_shot_preserves_raise_on_error():
    args = _loop_args(interval=0, once=False)

    with pytest.raises(RuntimeError, match="manual failure"):
        local_collector._run_loop(
            args,
            cycle_fn=lambda _args: (
                {
                    "status": "FAILED",
                    "error": {
                        "code": "LOCAL_COLLECTOR_COLLECT_FAILED",
                        "type": "RuntimeError",
                    },
                },
                RuntimeError("manual failure"),
            ),
            sleep_fn=lambda _seconds: None,
            emit_fn=lambda _row: None,
        )
