import datetime as dt
import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import local_collector


def _args(start="2026-10-02", end="2026-10-04"):
    return SimpleNamespace(start_date=start, end_date=end)


def test_local_collector_default_start_is_sep1(monkeypatch):
    monkeypatch.delenv("G2B_LOCAL_START_DATE", raising=False)
    monkeypatch.setattr(sys, "argv", ["local_collector.py", "--skip-collect"])
    args = local_collector._parse_args()
    assert args.start_date == "2026-09-01"


def test_local_collector_default_max_days_is_62(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["local_collector.py", "--skip-collect"])
    args = local_collector._parse_args()
    assert args.max_days == 62


def test_collection_window_accepts_exact_bounded_range():
    start, end = local_collector._collection_window(
        _args(),
        today=dt.date(2026, 10, 29),
    )
    assert start == dt.date(2026, 10, 2)
    assert end == dt.date(2026, 10, 4)


def test_collection_window_uses_korea_d_minus_one_when_end_omitted():
    start, end = local_collector._collection_window(
        _args(start="2026-10-02", end=""),
        today=dt.date(2026, 10, 29),
    )
    assert start == dt.date(2026, 10, 2)
    assert end == dt.date(2026, 10, 28)


def test_collection_window_rejects_future_end_date():
    with pytest.raises(ValueError, match="Korea D-1"):
        local_collector._collection_window(
            _args(start="2026-10-02", end="2026-10-29"),
            today=dt.date(2026, 10, 29),
        )


def test_collection_window_rejects_reverse_range():
    with pytest.raises(ValueError, match="must not be after"):
        local_collector._collection_window(
            _args(start="2026-10-05", end="2026-10-04"),
            today=dt.date(2026, 10, 29),
        )


def test_collection_window_rejects_invalid_date_format():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        local_collector._collection_window(
            _args(start="2026/10/02", end="2026-10-04"),
            today=dt.date(2026, 10, 29),
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



def test_direct_script_execution_from_external_working_directory(tmp_path):
    repo = pathlib.Path(local_collector.__file__).resolve().parents[1]
    script = repo / "scripts" / "local_collector.py"
    db_path = tmp_path / "g2b.sqlite3"
    snapshot_path = tmp_path / "result.json.gz"
    cwd = tmp_path / "outside-repo"
    cwd.mkdir()

    env = dict(os.environ)
    env["G2B_TEST_MODE"] = "1"
    env.pop("G2B_RESULT_SERVER_URL", None)
    env.pop("G2B_RESULT_SYNC_TOKEN", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--skip-collect",
            "--db",
            str(db_path),
            "--output",
            str(snapshot_path),
        ],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "ModuleNotFoundError" not in completed.stderr
    assert db_path.is_file()
    assert snapshot_path.is_file()



def test_console_progress_prints_page_and_day_status(capsys):
    local_collector._console_progress({
        "event": "day_start",
        "date": "2026-10-02",
        "day_index": 2,
        "total_days": 28,
    })
    local_collector._console_progress({
        "event": "page_complete",
        "date": "2026-10-02",
        "day_index": 2,
        "total_days": 28,
        "page": 3,
        "total_pages": 10,
        "saved": 2997,
        "source_total": 9017,
    })
    local_collector._console_progress({
        "event": "day_complete",
        "date": "2026-10-02",
        "day_index": 2,
        "total_days": 28,
        "saved": 9017,
        "source_total": 9017,
    })
    out = capsys.readouterr().out
    assert "[DAY 2/28] 2026-10-02 START" in out
    assert "[PAGE 3/10]" in out
    assert "saved=2,997/9,017" in out
    assert "[DAY 2/28] 2026-10-02 COMPLETE" in out


def test_console_progress_never_prints_unknown_payload_values(capsys):
    local_collector._console_progress({
        "event": "run_failed",
        "error_type": "RuntimeError",
        "api_key": "SECRET_SHOULD_NOT_PRINT",
        "token": "TOKEN_SHOULD_NOT_PRINT",
    })
    out = capsys.readouterr().out
    assert "RuntimeError" in out
    assert "SECRET_SHOULD_NOT_PRINT" not in out
    assert "TOKEN_SHOULD_NOT_PRINT" not in out



def test_local_cycle_requests_deferred_classification(monkeypatch, tmp_path):
    calls = []
    fake_db = SimpleNamespace(init_db=lambda: None)
    fake_store = SimpleNamespace(ensure_foundation=lambda: None)
    fake_recent = SimpleNamespace(
        collect_forward=lambda **kwargs: calls.append(kwargs) or {
            "status": "COMPLETE",
            "results": [],
        }
    )
    fake_snapshot = SimpleNamespace(
        build_local_snapshot=lambda: {
            "snapshot_id": "FAST",
            "sections": {},
        }
    )
    monkeypatch.setitem(sys.modules, "db", fake_db)
    monkeypatch.setitem(sys.modules, "vnext_store", fake_store)
    monkeypatch.setitem(sys.modules, "shopping_recent_vnext", fake_recent)
    monkeypatch.setitem(sys.modules, "result_snapshot_vnext", fake_snapshot)

    args = SimpleNamespace(
        db=str(tmp_path / "local.sqlite3"),
        g2b_key="",
        skip_collect=False,
        start_date="2026-10-01",
        end_date="2026-10-01",
        max_days=31,
        output=str(tmp_path / "result.json.gz"),
        server="",
        token="",
        progress=False,
    )
    result, failure = local_collector._execute_cycle(args)

    assert failure is None
    assert result["status"] == "COMPLETE"
    assert len(calls) == 1
    assert calls[0]["defer_classification"] is True


def test_local_collector_runtime_is_hard_isolated_from_production_postgres(monkeypatch, tmp_path):
    args = SimpleNamespace(
        db=str(tmp_path / "compat.sqlite3"),
        g2b_key="",
    )
    monkeypatch.setenv(
        "G2B_DATABASE_URL",
        "postgresql://prod:secret@db.invalid/prod",
    )
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        "postgresql://legacy:secret@db.invalid/prod",
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://generic:secret@db.invalid/prod",
    )

    local_collector._prepare_runtime(args)

    assert os.environ["G2B_TEST_MODE"] == "1"
    assert os.environ["G2B_BUDGET_STORAGE"] == "sqlite"
    assert os.environ["G2B_RUNTIME_ROLE"] == "LOCAL_COLLECTOR"
    assert os.environ["G2B_AUTO_SYNC"] == "0"
    for name in (
        "G2B_DATABASE_URL",
        "G2B_BUDGET_DATABASE_URL",
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
    ):
        assert name not in os.environ
