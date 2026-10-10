import json

from sqlalchemy import select
import pytest

import budget_pg_collection
import budget_pg_store


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'budget-pg-collection.sqlite3'}",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()


def _collect(fetch, *, max_pages=None, resume=True):
    return budget_pg_collection.collect_pages(
        dataset="budget",
        scope="2026:2026-10-01",
        range_start="2026",
        range_end="2026-10-01",
        page_size=1,
        max_pages=max_pages,
        resume=resume,
        fetch=fetch,
        identity=lambda row: str(row["dbiz_cd"]),
        source_system="LOFIN",
        source_operation="QWGJK",
        source_date=lambda row: "2026-10-01",
        checkpoint_contract="TEST_V1",
    )


def test_verified_partial_checkpoint_resumes_next_page(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    calls = []

    def fetch(page, size):
        calls.append(page)
        rows = {
            1: [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}],
            2: [{"fyr": "2026", "dbiz_cd": "B", "amount": 200}],
            3: [{"fyr": "2026", "dbiz_cd": "C", "amount": 300}],
        }
        return rows[page], 3

    first = _collect(fetch, max_pages=1, resume=False)
    assert first["status"] == "RUNNING"
    assert first["fetched"] == 1
    assert first["resumed"] is False
    assert calls == [1]

    cp = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    assert budget_pg_collection.verified_checkpoint(cp) is True

    finished = _collect(fetch, resume=True)
    assert finished["complete"] is True
    assert finished["fetched"] == 3
    assert finished["resumed"] is True
    assert calls == [1, 2, 3]


def test_complete_checkpoint_skips_same_day_source_fetch(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    calls = []

    first = _collect(
        lambda page, size: calls.append(page) or (
            [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}],
            1,
        ),
        resume=False,
    )
    assert first["complete"] is True
    assert first["resumed"] is False
    assert calls == [1]

    def forbidden_fetch(page, size):
        raise AssertionError("complete checkpoint must not call source fetch")

    second = _collect(forbidden_fetch, resume=True)

    assert second["complete"] is True
    assert second["resumed"] is True
    assert second["fetched"] == 1
    assert second["saved"] == 1
    assert calls == [1]


def test_budget_page_transaction_rolls_back_raw_when_terminal_checkpoint_fails(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    real = budget_pg_store.save_checkpoint

    def broken(dataset, scope_key="default", _conn=None, **values):
        if values.get("status") == "COMPLETE":
            raise RuntimeError("synthetic checkpoint failure")
        return real(dataset, scope_key, _conn=_conn, **values)

    monkeypatch.setattr(budget_pg_store, "save_checkpoint", broken)

    def fetch(page, size):
        return [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}], 1

    try:
        _collect(fetch, resume=False)
    except RuntimeError as exc:
        assert "synthetic checkpoint failure" in str(exc)
    else:
        raise AssertionError("terminal checkpoint failure must propagate")

    counts = budget_pg_store.dataset_counts("budget")
    assert counts["current_records"] == 0
    assert counts["observations"] == 0

    checkpoint = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    assert checkpoint["status"] == "FAILED"
    assert checkpoint["fetched_count"] == 0
    assert checkpoint["saved_count"] == 0



def test_explicit_replay_replaces_old_receipt_generation(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    def first_fetch(page, size):
        return [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}], 1

    first = _collect(first_fetch, resume=False)
    assert first["complete"] is True
    first_cp = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    first_generation = json.loads(first_cp["cursor_value"])["generation"]

    def second_fetch(page, size):
        return [{"fyr": "2026", "dbiz_cd": "A", "amount": 200}], 1

    second = _collect(second_fetch, resume=False)
    assert second["complete"] is True
    second_cp = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    second_generation = json.loads(second_cp["cursor_value"])["generation"]
    assert second_generation != first_generation

    engine, tables = budget_pg_store._engine_and_tables()
    with engine.connect() as conn:
        page_generations = {
            str(row[0])
            for row in conn.execute(
                select(tables["pages"].c.generation).where(
                    tables["pages"].c.scope_key == "2026:2026-10-01"
                )
            ).all()
        }
        item_generations = {
            str(row[0])
            for row in conn.execute(
                select(tables["items"].c.generation).where(
                    tables["items"].c.scope_key == "2026:2026-10-01"
                )
            ).all()
        }

    assert page_generations == {second_generation}
    assert item_generations == {second_generation}



def test_stale_postgres_collector_cannot_move_checkpoint_backwards(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    def first_fetch(page, size):
        return [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}], 2

    first = _collect(first_fetch, max_pages=1, resume=False)
    assert first["status"] == "RUNNING"

    real_get = budget_pg_store.get_checkpoint
    real_save = budget_pg_store.save_checkpoint
    stale = dict(real_get("budget", "2026:2026-10-01"))
    assert stale["page_no"] == 2

    raced = {"done": False}

    def racing_get(dataset, scope_key="default"):
        if not raced["done"]:
            raced["done"] = True
            real_save(
                dataset,
                scope_key,
                cursor_value=stale["cursor_value"],
                range_start=stale["range_start"],
                range_end=stale["range_end"],
                page_no=3,
                page_size=stale["page_size"],
                last_page_fingerprint=stale["last_page_fingerprint"],
                source_total=stale["source_total"],
                fetched_count=stale["fetched_count"],
                saved_count=stale["saved_count"],
                status="RUNNING",
                last_error="",
            )
        return dict(stale)

    monkeypatch.setattr(budget_pg_store, "get_checkpoint", racing_get)
    calls = []

    with pytest.raises(RuntimeError, match="CONCURRENT_CHECKPOINT_CHANGED"):
        _collect(
            lambda page, size: calls.append(page) or (
                [{"fyr": "2026", "dbiz_cd": "B", "amount": 200}],
                2,
            ),
            resume=True,
        )

    assert calls == []
    current = real_get("budget", "2026:2026-10-01")
    assert current["page_no"] == 3



def test_historical_receipt_verification_does_not_require_current_payload(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)

    first = _collect(
        lambda page, size: (
            [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}],
            1,
        ),
        resume=False,
    )
    assert first["complete"] is True
    cp = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    assert budget_pg_collection.verified_checkpoint(cp) is True

    budget_pg_store.preserve_observation(
        "budget",
        "A",
        {"fyr": "2026", "dbiz_cd": "A", "amount": 200},
        source_system="LOFIN",
        source_operation="QWGJK",
        source_date="2026-10-02",
    )

    assert budget_pg_collection.verified_checkpoint(cp) is False
    assert budget_pg_collection.verified_checkpoint(
        cp, require_current=False
    ) is True



def test_budget_checkpoint_resumes_after_engine_restart(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    calls = []

    def fetch(page, size):
        calls.append(page)
        rows = {
            1: [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}],
            2: [{"fyr": "2026", "dbiz_cd": "B", "amount": 200}],
        }
        return rows[page], 2

    partial = _collect(fetch, max_pages=1, resume=False)
    assert partial["status"] == "RUNNING"
    assert partial["fetched"] == 1
    assert partial["resumed"] is False

    # Simulate process restart / pool recreation while keeping the same database.
    budget_pg_store.reset_engine_cache()

    finished = _collect(fetch, resume=True)
    assert finished["complete"] is True
    assert finished["fetched"] == 2
    assert finished["resumed"] is True
    assert calls == [1, 2]


def test_budget_hard_worker_exit_preserves_committed_cursor_for_resume(monkeypatch, tmp_path):
    """Simulated SIGKILL bypasses ordinary exception checkpoint cleanup."""
    _configure(monkeypatch, tmp_path)
    calls = []
    rows = {
        1: [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}],
        2: [{"fyr": "2026", "dbiz_cd": "B", "amount": 200}],
    }

    def interrupted(page, size):
        calls.append(page)
        if page == 2:
            raise SystemExit(137)
        return rows[page], 2

    with pytest.raises(SystemExit):
        _collect(interrupted, resume=False)

    checkpoint = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    assert checkpoint["status"] == "RUNNING"
    assert checkpoint["page_no"] == 2
    assert checkpoint["fetched_count"] == checkpoint["saved_count"] == 1
    original_generation = json.loads(checkpoint["cursor_value"])["generation"]
    assert budget_pg_collection.verified_checkpoint(checkpoint) is True

    # A new process/pool must resume page 2, not discard page 1 or duplicate it.
    budget_pg_store.reset_engine_cache()

    def recovered(page, size):
        calls.append(page)
        assert page == 2
        return rows[page], 2

    result = _collect(recovered, resume=True)
    assert result["complete"] is True
    assert result["resumed"] is True
    assert result["fetched"] == result["saved"] == 2
    final = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    assert json.loads(final["cursor_value"])["generation"] == original_generation
    assert budget_pg_collection.verified_checkpoint(final) is True
    assert calls == [1, 2, 2]


def test_memory_pressure_marks_checkpoint_incomplete_for_resume(monkeypatch, tmp_path):
    import memory_guard

    _configure(monkeypatch, tmp_path)

    def memory_hold():
        raise memory_guard.MemoryPressureError("synthetic")

    monkeypatch.setattr(budget_pg_collection, "_memory_checkpoint", memory_hold)

    with pytest.raises(memory_guard.MemoryPressureError):
        _collect(
            lambda page, size: (
                [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}],
                1,
            ),
            resume=False,
        )

    checkpoint = budget_pg_store.get_checkpoint(
        "budget", "2026:2026-10-01"
    )
    assert checkpoint["status"] == "INCOMPLETE"
    assert checkpoint["last_error"] == "MEMORY_PRESSURE"
    assert checkpoint["fetched_count"] == 0


def test_budget_overlap_auto_replays_once_from_page_one(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    calls = []

    def overlapping(page, size):
        calls.append(("overlap", page))
        if page == 1:
            return [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}], 2
        return [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}], 2

    first = _collect(overlapping, resume=False)
    assert first["status"] == "INCOMPLETE"
    assert first["reason"] == "REPEATED_OR_OVERLAPPING_PAGE"
    assert first["drift_replay_count"] == 0

    def recovered(page, size):
        calls.append(("recovered", page))
        row = "A" if page == 1 else "B"
        return [{"fyr": "2026", "dbiz_cd": row, "amount": 100}], 2

    second = _collect(recovered, resume=True)

    assert second["complete"] is True
    assert second["drift_replay_count"] == 1
    assert second["drift_replay_exhausted"] is False
    assert calls == [
        ("overlap", 1),
        ("overlap", 2),
        ("recovered", 1),
        ("recovered", 2),
    ]


def test_budget_overlap_replay_exhaustion_stops_quota_burn(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    calls = []

    def overlapping(page, size):
        calls.append(page)
        return [{"fyr": "2026", "dbiz_cd": "A", "amount": 100}], 2

    first = _collect(overlapping, resume=False)
    assert first["reason"] == "REPEATED_OR_OVERLAPPING_PAGE"

    second = _collect(overlapping, resume=True)
    assert second["status"] == "INCOMPLETE"
    assert second["reason"] == "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED"
    assert second["drift_replay_count"] == 1
    assert second["drift_replay_exhausted"] is True

    before = list(calls)

    def forbidden_fetch(page, size):
        raise AssertionError("exhausted replay must not consume another API call")

    third = _collect(forbidden_fetch, resume=True)
    assert third["status"] == "INCOMPLETE"
    assert third["drift_replay_exhausted"] is True
    assert calls == before

def test_budget_quota_boundaries_keep_checkpoint_resumable():
    import budget_pg_collection
    from lofin_vnext_http import LofinVNextApiError
    from vnext_source_guard import VNextSourceAccessError

    local_quota = LofinVNextApiError("LOCAL_DAILY_QUOTA_REACHED")
    context_quota = VNextSourceAccessError(
        "VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED"
    )
    unrelated = RuntimeError("CONCURRENT_CHECKPOINT_CHANGED")

    assert budget_pg_collection._failure_checkpoint_status(local_quota) == "INCOMPLETE"
    assert budget_pg_collection._failure_checkpoint_status(context_quota) == "INCOMPLETE"
    assert budget_pg_collection._safe_error_label(local_quota) == "LOCAL_DAILY_QUOTA_REACHED"
    assert (
        budget_pg_collection._safe_error_label(context_quota)
        == "VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED"
    )
    assert budget_pg_collection._failure_checkpoint_status(unrelated) == "FAILED"
