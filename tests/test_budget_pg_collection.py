import json

from sqlalchemy import select

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
    assert calls == [1]

    cp = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    assert budget_pg_collection.verified_checkpoint(cp) is True

    finished = _collect(fetch, resume=True)
    assert finished["complete"] is True
    assert finished["fetched"] == 3
    assert calls == [1, 2, 3]


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
