"""4.1.255: abrupt source termination resumes exact durable next page."""

import json

import pytest

import budget_pg_collection
import budget_pg_store
import db
import shopping_vnext
import vnext_clean_app as app
import vnext_collection
import vnext_store


def test_repeated_parent_preemptions_have_bounded_auto_retry_not_minute_storm(monkeypatch):
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 0, "budget": 0,
    })
    monkeypatch.setattr(
        app, "get_setting",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("parent memory preemption cannot query old source error")
        ),
    )
    monkeypatch.setattr(
        app, "_RECENT_COLLECTION_STATE",
        {
            "shopping_run_state": "IDLE", "budget_run_state": "IDLE",
            "shopping_last_error": "", "budget_last_error": "",
        },
    )
    for attempt, expected_delay in enumerate((60, 120, 240, 480, 900, 900), 1):
        app._isolated_worker_exit_state(
            "shopping", -15, parent_memory_abort=True
        )
        assert app._PARENT_MEMORY_ABORT_STREAK["shopping"] == attempt
        assert app._automatic_cycle_wait_seconds({
            "operational_cycle_lease": "ISOLATED_AUTOMATIC"
        }) == expected_delay

    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "WAITING_MEMORY"
    assert app._RECENT_COLLECTION_STATE["shopping_last_error"] == (
        "PARENT_CGROUP_MEMORY_HOLD"
    )
    app._isolated_worker_exit_state("shopping", 0)
    assert app._PARENT_MEMORY_ABORT_STREAK["shopping"] == 0
    assert app._RECENT_COLLECTION_STATE["shopping_run_state"] == "COMPLETE"


def test_non_parent_memory_hold_and_quota_preserve_existing_retry(monkeypatch):
    monkeypatch.setattr(app, "_PARENT_MEMORY_ABORT_STREAK", {
        "shopping": 0, "budget": 0,
    })
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "shopping_run_state": "WAITING_MEMORY",
        "budget_run_state": "COMPLETE",
    })
    assert app._automatic_cycle_wait_seconds({}) == max(
        app.OPERATIONAL_LEASE_RETRY_SECONDS, 60
    )
    monkeypatch.setattr(app, "_RECENT_COLLECTION_STATE", {
        "shopping_run_state": "WAITING_QUOTA",
        "budget_run_state": "COMPLETE",
    })
    assert app._automatic_cycle_wait_seconds({}) > 0


def test_shopping_abrupt_interrupt_leaves_verified_running_receipt_and_resumes(monkeypatch):
    scope = "2026-09-28:2026-09-28"
    calls = []

    def row(req):
        return {
            "dlvrReqNo": req, "dlvrReqChgOrd": "0", "prdctSno": "1",
            "dlvrReqRcptDate": "20260928",
            "dtilPrdctClsfcNo": "3911160302",
            "prdctNm": "LED 가로등기구",
        }

    def interrupt(start, end, page=1, rows=999):
        assert (start, end, rows) == ("2026-09-28", "2026-09-28", 1)
        calls.append(page)
        if page == 2:
            # BaseException bypasses ordinary per-page Exception handlers,
            # matching a worker terminated without an error checkpoint write.
            raise KeyboardInterrupt("simulated disposable worker exit")
        return [row("CRASH-A-4255")], 2

    monkeypatch.setattr(shopping_vnext, "fetch_page", interrupt)
    with pytest.raises(KeyboardInterrupt):
        shopping_vnext.collect_all(
            "2026-09-28", "2026-09-28",
            page_size=1, max_pages=2, resume=True,
        )

    checkpoint = vnext_store.get_checkpoint("shopping_delivery", scope)
    generation = json.loads(checkpoint["cursor_value"])["generation"]
    assert checkpoint["status"] == "RUNNING"
    assert checkpoint["page_no"] == 2
    assert checkpoint["fetched_count"] == checkpoint["saved_count"] == 1
    assert vnext_collection._verified_checkpoint(
        checkpoint, complete=False, schema_prepared=True
    ) is True

    def resume(start, end, page=1, rows=999):
        calls.append(page)
        assert page == 2, "page 1 must not repeat after process death"
        return [row("CRASH-B-4255")], 2

    monkeypatch.setattr(shopping_vnext, "fetch_page", resume)
    finished = shopping_vnext.collect_all(
        "2026-09-28", "2026-09-28",
        page_size=1, max_pages=2, resume=True,
    )
    assert finished["complete"] is True
    assert finished["resumed"] is True
    assert finished["fetched"] == finished["saved"] == 2
    assert calls == [1, 2, 2]
    final = vnext_store.get_checkpoint("shopping_delivery", scope)
    assert json.loads(final["cursor_value"])["generation"] == generation
    assert final["status"] == "COMPLETE"
    with db.connect() as conn:
        assert conn.execute(
            """SELECT COUNT(*) FROM shopping_records
               WHERE delivery_req_no IN ('CRASH-A-4255','CRASH-B-4255')"""
        ).fetchone()[0] == 2


def test_budget_abrupt_interrupt_retains_generation_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'budget-4255-abrupt.sqlite3'}",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()
    scope = "2026:2026-09-28:4255"
    calls = []

    def collect(fetch):
        return budget_pg_collection.collect_pages(
            dataset="budget", scope=scope,
            range_start="2026", range_end="2026-09-28",
            page_size=1, max_pages=2, resume=True,
            fetch=fetch,
            identity=lambda payload: str(payload["dbiz_cd"]),
            source_system="LOFIN", source_operation="QWGJK",
            source_date=lambda _payload: "2026-09-28",
            checkpoint_contract="TEST_FORCE_KILL_4255",
        )

    def fetch(page, size):
        calls.append(page)
        if page == 2:
            raise KeyboardInterrupt("synthetic abrupt worker death")
        return [{"fyr": "2026", "dbiz_cd": "CRASH-A-4255",
                 "amount": 100}], 2

    with pytest.raises(KeyboardInterrupt):
        collect(fetch)

    checkpoint = budget_pg_store.get_checkpoint("budget", scope)
    gen = json.loads(checkpoint["cursor_value"])["generation"]
    assert checkpoint["status"] == "RUNNING"
    assert checkpoint["page_no"] == 2
    assert checkpoint["fetched_count"] == checkpoint["saved_count"] == 1
    assert budget_pg_collection.verified_checkpoint(checkpoint) is True

    # The next process discards its SQLAlchemy pool but not PostgreSQL files.
    budget_pg_store.reset_engine_cache()

    def recovered(page, size):
        calls.append(page)
        assert page == 2
        return [{"fyr": "2026", "dbiz_cd": "CRASH-B-4255",
                 "amount": 200}], 2

    result = collect(recovered)
    assert result["complete"] is True
    assert result["resumed"] is True
    assert result["fetched"] == result["saved"] == 2
    assert calls == [1, 2, 2]
    final = budget_pg_store.get_checkpoint("budget", scope)
    assert final["status"] == "COMPLETE"
    assert json.loads(final["cursor_value"])["generation"] == gen
    assert budget_pg_collection.verified_checkpoint(final) is True
