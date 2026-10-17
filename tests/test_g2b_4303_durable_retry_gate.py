"""Disposable SQLite control storage + real parent lifecycle, no external APIs."""
import copy
import json
import threading

import pytest

import db
import g2b_dynamic_source_budget as dyn
import vnext_clean_app as app

RAW = 155 * dyn.MIB


def proof(pages=0, *, scope="budget:2026:2026-10-01", generation="gen-a"):
    return {"version": 1, "scopes": {scope: {"generation": generation, "pages": pages, "saved": pages}}}


def gate():
    result = dyn.NoProgressGate()
    assert result.initialize("budget")
    assert result.initialize("shopping")
    return result


def abort(g, source="budget", *, before=None, after=None, raw=RAW, raw_after=RAW):
    before = proof() if before is None else before
    after = before if after is None else after
    assert g.record_launch(source, raw, before)
    return g.record_exit(source, raw_after_bytes=raw_after, receipt_after=after, memory_held=True)


def blocked_gate():
    result = gate()
    abort(result)
    abort(result)
    return result


def test_restart_preserves_two_abort_latch_and_scope_isolation():
    first = blocked_gate()
    assert not first.may_start("budget", RAW, proof())[0]
    restart = dyn.NoProgressGate()
    assert restart.initialize("budget")
    assert restart.status("budget")["consecutive_no_progress"] == 2
    assert not restart.may_start("budget", RAW, proof())[0]
    assert restart.may_start("shopping", RAW, proof())[0]
    # Extra committed pages in a different scope cannot release the held page.
    unrelated = copy.deepcopy(proof())
    unrelated["scopes"].update(proof(10, scope="budget:other")["scopes"])
    assert not restart.may_start("budget", RAW, unrelated)[0]
    # Cursor reset or freshly initialized generation is not durable advance.
    assert not restart.may_start("budget", RAW, proof(generation="gen-new"))[0]
    assert not restart.may_start("budget", RAW, proof(50, generation="gen-new"))[0]
    assert restart.may_start("budget", RAW, proof(1))[0]


def test_restart_after_confirmed_progress_resumes_without_baseline_drop():
    first = gate()
    assert abort(first, before=proof(3), after=proof(4))["last_outcome"] == "DURABLE_PROGRESS"
    restart = dyn.NoProgressGate()
    assert restart.initialize("budget")
    assert restart.may_start("budget", RAW, proof(4))[0]
    assert restart.record_launch("budget", RAW, proof(4))


def test_unconfirmed_reap_survives_restart_and_cannot_be_cleared_by_other_parent():
    first = gate()
    token = first.record_launch("budget", RAW, proof())
    assert token
    restart = dyn.NoProgressGate()
    assert restart.initialize("budget")
    assert restart.may_start("budget", RAW - 20 * dyn.MIB, proof(20)) == (False, "NO_PROGRESS_REAP_UNCONFIRMED")
    restart.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(1), memory_held=False)
    assert not first.may_start("budget", RAW, proof(1))[0]
    # Only the owning parent's actual reap releases its reservation.
    first.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(1), memory_held=False)
    assert restart.may_start("budget", RAW, proof(1))[0]


@pytest.mark.parametrize("bad", [None, (), (1, 2, 3), {}, {"version": 1, "scopes": {"bad": {}}}])
def test_missing_or_malformed_proof_blocks_first_spawn(bad):
    g = gate()
    assert not g.may_start("budget", RAW, bad)[0]
    assert not g.record_launch("budget", RAW, bad)
    assert not g.status("budget")["active_generation"]


@pytest.mark.parametrize("missing", [True, False])
def test_missing_corrupt_state_never_bootstrap_resets_latch(missing):
    blocked_gate()
    with db.connect() as conn:
        if missing:
            conn.execute("DELETE FROM app_settings WHERE key=?", (dyn.GATE_KEY_PREFIX + "budget",))
        else:
            conn.execute("UPDATE app_settings SET value=? WHERE key=?", ("{corrupt", dyn.GATE_KEY_PREFIX + "budget"))
    restart = dyn.NoProgressGate()
    assert not restart.initialize("budget")
    assert not restart.may_start("budget", RAW - 20 * dyn.MIB, proof(50))[0]


def test_gate_never_initializes_from_normal_admission():
    uninitialized = dyn.NoProgressGate()
    assert uninitialized.may_start("budget", RAW, proof()) == (False, "NO_PROGRESS_STATE_UNAVAILABLE")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM app_settings WHERE key LIKE ?", (dyn.GATE_KEY_PREFIX + "%",)).fetchone()[0] == 0


def test_missing_post_reap_proof_blocks_even_first_hold_and_low_baseline():
    g = gate()
    assert g.record_launch("budget", RAW, proof())
    g.record_exit("budget", raw_after_bytes=0, receipt_after=None, memory_held=True)
    assert not g.may_start("budget", RAW - 30 * dyn.MIB, proof())[0]


def test_operator_hold_never_reopens_for_lower_raw_or_page_progress(monkeypatch):
    g = gate()
    token = g.record_launch("budget", RAW, proof())
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_SOURCE_DYNAMIC_CHILD", "1")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_MODE", "budget")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_GENERATION", token)
    assert dyn.record_operator_hold()
    outcome = g.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(2), memory_held=True)
    assert outcome["operator_hold"]
    restarted = dyn.NoProgressGate()
    assert restarted.initialize("budget")
    assert restarted.may_start("budget", RAW - 30 * dyn.MIB, proof(50)) == (False, "NO_PROGRESS_OPERATOR_HOLD")


def test_classification_commit_is_durable_and_generation_scoped(monkeypatch):
    g = gate()
    token = g.record_launch("budget", RAW, proof(10))
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_SOURCE_DYNAMIC_CHILD", "1")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_MODE", "budget")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_GENERATION", "wrong-token")
    with pytest.raises(app.memory_guard.MemoryPressureError, match="NO_PROGRESS_DURABLE_WRITE_HOLD"):
        dyn.record_classification_commit(rows=1)
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_GENERATION", token)
    assert dyn.record_classification_commit(rows=1)
    assert g.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(10), memory_held=True)["last_outcome"] == "DURABLE_PROGRESS"
    restarted = dyn.NoProgressGate()
    assert restarted.initialize("budget")
    assert restarted.may_start("budget", RAW, proof(10))[0]


def test_classification_write_cannot_count_rolled_back_control_update(monkeypatch):
    g = gate()
    assert g.record_launch("budget", RAW, proof(10))
    class BadStore(dyn.DatabaseGateStore):
        def transact(self, source, change):
            def fail(rec):
                rec["classified_commits"] += 10
                raise RuntimeError("rollback")
            return super().transact(source, fail)
    with pytest.raises(RuntimeError, match="rollback"):
        BadStore().transact("budget", lambda r: None)
    assert g.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(10), memory_held=True)["consecutive_no_progress"] == 1


def test_only_one_parent_can_reserve_a_source():
    first, second = gate(), dyn.NoProgressGate()
    assert first.record_launch("budget", RAW, proof())
    assert not second.record_launch("budget", RAW, proof())
    assert second.status("budget")["active_generation"]


def parent_state(raw=RAW):
    return {"cgroup_source": "cgroup-v2", "cgroup_limit_bytes": dyn.CGROUP_LIMIT_BYTES,
            "cgroup_current_bytes": raw, "cgroup_limit_mib": 256, "cgroup_current_mib": raw/dyn.MIB,
            "cgroup_effective_mib": 100, "guard_ok": True, "process_guard_ok": True, "rss_mib": 80,
            "cgroup_oom_group": 0}


def setup_parent(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "is_unified", lambda: True)
    monkeypatch.setattr(app, "_web_first_grace_remaining", lambda: 0)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "")
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PENDING", [])
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_SUPERVISOR", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_LAST", {"kind": "", "exit_code": None})
    monkeypatch.setattr(app, "_DYNAMIC_SOURCE_NO_PROGRESS", gate())
    monkeypatch.setattr(app, "_recover_unreaped_isolated_child_locked", lambda: False)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kwargs: parent_state())
    monkeypatch.setattr(app.memory_guard, "container_budget_snapshot", lambda: {
        "source": "cgroup-v2", "limit_bytes": dyn.CGROUP_LIMIT_BYTES, "current_bytes": RAW})
    monkeypatch.setattr(app, "_isolated_source_durable_receipt_counts", lambda mode: proof())


class FakeProcess:
    pid = 123456
    def poll(self):
        return None


def test_real_parent_reserves_before_popen_and_only_releases_after_confirmed_reap(monkeypatch):
    setup_parent(monkeypatch)
    events = []
    def popen(*args, **kwargs):
        assert app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["active_generation"]
        assert kwargs["env"]["G2B_SOURCE_DYNAMIC_CHILD"] == "1"
        assert kwargs["env"]["G2B_DYNAMIC_SOURCE_GENERATION"]
        events.append("spawn")
        return FakeProcess()
    monkeypatch.setattr(app.subprocess, "Popen", popen)
    assert app._request_isolated_source_worker("budget") == "STARTED"
    assert app._request_isolated_source_worker("budget") == "ALREADY"
    assert app._request_isolated_source_worker("shopping") == "QUEUED"
    assert events == ["spawn"]
    # Keep this test on the current child; separate tests cover next-mode re-admission.
    app._ISOLATED_HEAVY_PENDING.clear()
    def reap(process, mode):
        assert app._ISOLATED_HEAVY_PROCESS is process
        assert app._DYNAMIC_SOURCE_NO_PROGRESS.status(mode)["active_generation"]
        events.append("confirmed_reap")
        return 75, True
    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", reap)
    monkeypatch.setattr(app, "_isolated_worker_exit_state", lambda *a, **k: events.append("published"))
    app._isolated_heavy_supervisor_worker()
    assert events == ["spawn", "confirmed_reap", "published"]
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert not app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["active_generation"]
    assert app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["consecutive_no_progress"] == 1


def test_manual_and_automatic_real_admission_block_before_popen(monkeypatch):
    setup_parent(monkeypatch)
    abort(app._DYNAMIC_SOURCE_NO_PROGRESS)
    abort(app._DYNAMIC_SOURCE_NO_PROGRESS)
    def forbidden(*args, **kwargs):
        raise AssertionError("latched source must not spawn or call any source API")
    monkeypatch.setattr(app.subprocess, "Popen", forbidden)
    assert app._request_isolated_source_worker("budget") == "HOLD"
    assert not app._isolated_worker_admission_ok("budget")
    assert app._ISOLATED_HEAVY_PROCESS is None


def test_failed_popen_clears_only_its_known_unstarted_reservation(monkeypatch):
    setup_parent(monkeypatch)
    def failed(*args, **kwargs):
        raise OSError("fork failed")
    monkeypatch.setattr(app.subprocess, "Popen", failed)
    assert app._request_isolated_source_worker("budget") == "HOLD"
    assert not app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["active_generation"]
    assert app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["last_outcome"] == "SPAWN_FAILED_NO_CHILD"
    assert app._DYNAMIC_SOURCE_NO_PROGRESS.may_start("budget", RAW, proof())[0]


def test_admission_without_spawn_never_records_fake_launch(monkeypatch):
    setup_parent(monkeypatch)
    assert app._isolated_worker_admission_ok("budget")
    assert not app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["active_generation"]


def test_no_sql_holds_status_lock_during_prelaunch(monkeypatch):
    setup_parent(monkeypatch)
    original = app._DYNAMIC_SOURCE_NO_PROGRESS.record_launch
    def reserve(*args):
        assert app._ISOLATED_HEAVY_LOCK.acquire(blocking=False)
        app._ISOLATED_HEAVY_LOCK.release()
        return original(*args)
    monkeypatch.setattr(app._DYNAMIC_SOURCE_NO_PROGRESS, "record_launch", reserve)
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: FakeProcess())
    assert app._request_isolated_source_worker("budget") == "STARTED"


def test_missing_first_sample_never_falls_back_to_legacy_policy(monkeypatch):
    setup_parent(monkeypatch)
    missing = parent_state()
    missing.update(cgroup_limit_mib=0, cgroup_limit_bytes=0, cgroup_current_bytes=0)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kw: missing)
    assert app._source_dynamic_budget_for("budget", missing)
    assert not app._isolated_worker_admission_ok("budget")
    assert app._request_isolated_source_worker("budget") == "HOLD"


def test_scoped_receipt_reader_rejects_uncommitted_checkpoint_advance():
    from vnext_collection import ensure_collection_storage
    from vnext_store import save_checkpoint
    ensure_collection_storage()
    save_checkpoint("shopping_delivery", "day", cursor_value=json.dumps({"generation": "gen-a"}),
                    page_no=1, page_size=1, fetched_count=0, saved_count=0)
    initial = app._isolated_source_durable_receipt_counts("shopping")
    assert initial["scopes"]["shopping_delivery:day"]["pages"] == 0
    with db.connect() as conn:
        conn.execute("UPDATE collection_checkpoints SET page_no=2,fetched_count=1,saved_count=1 WHERE dataset=?", ("shopping_delivery",))
    assert app._isolated_source_durable_receipt_counts("shopping") is None
    with db.connect() as conn:
        conn.execute("INSERT INTO vnext_collection_pages VALUES (?,?,?,?,?,?,?,?,?)", ("shopping_delivery", "day", "gen-a", 1, 1, "hash", 1, 2, ""))
    actual = app._isolated_source_durable_receipt_counts("shopping")
    assert actual["scopes"]["shopping_delivery:day"]["pages"] == 1
    assert dyn.NoProgressGate._progressed(initial, actual)


def test_restart_unreaped_other_source_blocks_serial_slot():
    first = gate()
    assert first.record_launch("budget", RAW, proof())
    restarted = dyn.NoProgressGate()
    assert restarted.initialize("shopping")
    assert restarted.may_start("shopping", RAW, proof()) == (False, "NO_PROGRESS_OTHER_SOURCE_REAP_UNCONFIRMED")
    assert not restarted.record_launch("shopping", RAW, proof())
    first.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(1), memory_held=False)
    assert restarted.record_launch("shopping", RAW, proof())


def test_deleting_source_row_and_marker_cannot_reinitialize_it():
    blocked_gate()
    with db.connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key IN (?,?)", (
            dyn.GATE_KEY_PREFIX + "budget", dyn.GATE_KEY_PREFIX + "budget:initialized"))
    assert not dyn.NoProgressGate().initialize("budget")


def test_explicit_retry_consumes_operator_hold_only_in_launch_transaction():
    first = gate()
    assert first.record_launch("budget", RAW, proof())
    first.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(), memory_held=True, operator_hold=True)
    assert first.may_start("budget", RAW, proof(), explicit_operator_retry=True)[0]
    # A mere admission probe/failed capacity check never clears the durable hold.
    assert not dyn.NoProgressGate().may_start("budget", RAW, proof())[0]
    assert first.record_launch("budget", RAW, proof(), explicit_operator_retry=True)
    assert not dyn.NoProgressGate().may_start("budget", RAW, proof())[0]
    first.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(), memory_held=True, spawn_failed=True)
    assert first.may_start("budget", RAW - 10 * dyn.MIB, proof(10)) == (False, "NO_PROGRESS_OPERATOR_HOLD")
    assert first.record_launch("budget", RAW, proof(), explicit_operator_retry=True)
    first.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(1), memory_held=False)
    assert first.may_start("budget", RAW, proof(1))[0]


def test_explicit_retry_cannot_override_unreaped_reservation():
    first = gate()
    assert first.record_launch("budget", RAW, proof())
    assert first.may_start("budget", RAW, proof(), explicit_operator_retry=True) == (False, "NO_PROGRESS_REAP_UNCONFIRMED")
    assert not first.record_launch("budget", RAW, proof(), explicit_operator_retry=True)


def test_explicit_parent_retry_still_requires_idle_slot_and_fresh_headroom(monkeypatch):
    setup_parent(monkeypatch)
    g = app._DYNAMIC_SOURCE_NO_PROGRESS
    assert g.record_launch("budget", RAW, proof())
    g.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(), memory_held=True, operator_hold=True)
    called = []
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: (called.append(k["env"]) or FakeProcess()))
    assert app._request_isolated_source_worker("budget") == "HOLD"
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", FakeProcess())
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "shopping")
    assert app._request_isolated_source_worker("budget", explicit_operator_retry=True) == "HOLD"
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_PROCESS", None)
    monkeypatch.setattr(app, "_ISOLATED_HEAVY_KIND", "")
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kw: parent_state(173 * dyn.MIB))
    assert app._request_isolated_source_worker("budget", explicit_operator_retry=True) == "HOLD"
    assert g.status("budget")["operator_hold"]
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kw: parent_state())
    assert app._request_isolated_source_worker("budget", explicit_operator_retry=True) == "STARTED"
    assert len(called) == 1


@pytest.mark.parametrize("source,handler,path", [
    ("budget", app.collect_budget_manual, "/collect/budget"),
    ("shopping", app.collect_shopping_recent, "/collect/shopping-recent"),
])
def test_only_authenticated_csrf_explicit_post_requests_operator_retry(monkeypatch, source, handler, path):
    import asyncio
    from starlette.requests import Request
    def request():
        async def receive():
            return {"type": "http.request", "body": b"retry_dynamic_hold=1", "more_body": False}
        return Request({"type": "http", "path": path, "method": "POST",
                        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
                        "query_string": b"", "client": ("127.0.0.1", 1234)}, receive)
    calls = []
    monkeypatch.setattr(app, "schedule_manual_collection", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(app, "is_result_server", lambda: False)
    monkeypatch.setattr(app, "require_user", lambda request: None)
    assert asyncio.run(handler(request())).status_code == 302
    assert not calls
    monkeypatch.setattr(app, "require_user", lambda request: {"username": "operator", "role": "admin"})
    monkeypatch.setattr(app, "valid_csrf", lambda *args: False)
    assert asyncio.run(handler(request())).status_code == 403
    assert not calls
    monkeypatch.setattr(app, "valid_csrf", lambda *args: True)
    assert asyncio.run(handler(request())).status_code == 303
    assert calls == [((source,), {"explicit_operator_retry": True})]


def test_operator_ui_distinguishes_unconfirmed_reap_and_deterministic_hold(monkeypatch):
    monkeypatch.setattr(app, "csrf_input", lambda request, path: '<input name="_csrf" value="fixture">')
    status = {"dynamic_source_memory_gates": {"budget": {
        "operator_hold": True, "last_outcome": "SOURCE_RESPONSE_LIMIT", "consecutive_no_progress": 2}},
        "isolated_heavy_worker": {"slot_reserved": False}}
    html = app._collection_dynamic_gate_notice(None, status)
    assert "SOURCE_RESPONSE_LIMIT" in html
    assert 'action="/collect/budget"' in html and 'name="retry_dynamic_hold" value="1"' in html
    assert 'name="_csrf"' in html
    status["dynamic_source_memory_gates"]["budget"]["active_generation"] = True
    html = app._collection_dynamic_gate_notice(None, status)
    assert "NO_PROGRESS_REAP_UNCONFIRMED" in html
    assert "retry_dynamic_hold" not in html


def test_explicit_bound_retry_without_durable_progress_remains_operator_held():
    first = gate()
    assert first.record_launch("budget", RAW, proof())
    first.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(), memory_held=True, operator_hold=True)
    assert first.record_launch("budget", RAW, proof(), explicit_operator_retry=True)
    first.record_exit("budget", raw_after_bytes=RAW - 20 * dyn.MIB, receipt_after=proof(), memory_held=True)
    assert first.may_start("budget", RAW - 40 * dyn.MIB, proof())[1] == "NO_PROGRESS_OPERATOR_HOLD"


def test_budget_receipt_reader_tracks_only_committed_matching_generation(monkeypatch, tmp_path):
    import budget_pg_collection
    import budget_pg_store
    monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'receipt-proof.sqlite3'}")
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(name, raising=False)
    budget_pg_store.reset_engine_cache()
    try:
        def collect():
            return budget_pg_collection.collect_pages(
                dataset="budget", scope="2026:day", range_start="2026", range_end="day",
                page_size=1, max_pages=1, resume=True,
                fetch=lambda page, size: ([{"fyr": "2026", "dbiz_cd": f"A{page}", "amount": 100}], 3),
                identity=lambda row: row["dbiz_cd"], source_system="LOFIN", source_operation="QWGJK",
                source_date=lambda row: "2026-10-17", checkpoint_contract="TEST_V1",
            )
        collect()
        before = app._isolated_source_durable_receipt_counts("budget")
        assert before["scopes"]["budget:2026:day"]["pages"] == 1
        collect()
        after = app._isolated_source_durable_receipt_counts("budget")
        assert after["scopes"]["budget:2026:day"]["pages"] == 2
        assert dyn.NoProgressGate._progressed(before, after)
        engine, tables = budget_pg_store._engine_and_tables()
        with engine.begin() as conn:
            conn.execute(tables["checkpoints"].update().values(page_no=4))
        assert app._isolated_source_durable_receipt_counts("budget") is None
    finally:
        budget_pg_store.reset_engine_cache()


def test_uncertain_reap_keeps_durable_and_process_slots_reserved(monkeypatch):
    setup_parent(monkeypatch)
    process = FakeProcess()
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: process)
    assert app._request_isolated_source_worker("budget") == "STARTED"
    waits = []
    def wait(child, mode):
        waits.append(mode)
        assert child is app._ISOLATED_HEAVY_PROCESS
        assert app._DYNAMIC_SOURCE_NO_PROGRESS.status(mode)["active_generation"]
        if len(waits) == 1:
            raise OSError("unconfirmed reap")
        return 75, True
    def backoff(delay, **kwargs):
        assert app._ISOLATED_HEAVY_PROCESS is process
        assert dyn.NoProgressGate().may_start("shopping", RAW, proof())[1] == "NO_PROGRESS_OTHER_SOURCE_REAP_UNCONFIRMED"
        assert app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["consecutive_no_progress"] == 0
        return "CGROUP_BLOCK"
    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", wait)
    monkeypatch.setattr(app, "_watch_unreaped_child_backoff_memory", backoff)
    monkeypatch.setattr(app, "_isolated_worker_exit_state", lambda *a, **k: None)
    app._isolated_heavy_supervisor_worker()
    assert waits == ["budget", "budget"]
    assert app._ISOLATED_HEAVY_PROCESS is None
    assert app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["consecutive_no_progress"] == 1


def test_reaped_limit_exit_is_operator_held_without_child_marker(monkeypatch):
    setup_parent(monkeypatch)
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: FakeProcess())
    assert app._request_isolated_source_worker("budget") == "STARTED"
    # Simulate child unable to persist its marker; actual reaped exit79 carries
    # the deterministic-bound disposition without reading an old error setting.
    monkeypatch.setattr(app, "_wait_for_isolated_heavy_child", lambda *a: (79, False))
    monkeypatch.setattr(app, "get_setting", lambda *a: (_ for _ in ()).throw(AssertionError("must not borrow old error")))
    app._isolated_heavy_supervisor_worker()
    assert app._DYNAMIC_SOURCE_NO_PROGRESS.status("budget")["operator_hold"]
    assert app._RECENT_COLLECTION_STATE["budget_last_error"] == "SOURCE_RESPONSE_LIMIT"
    assert app._request_isolated_source_worker("budget") == "HOLD"


def test_idle_reclaim_cannot_overlap_durable_launch_reservation(monkeypatch):
    skips = []
    monkeypatch.setattr(app.g2b_idle_memory_recovery, "record_skipped_cycle", lambda reason: skips.append(reason))
    monkeypatch.setattr(app.g2b_idle_memory_recovery, "run_recovery_cycle", lambda **kw: (_ for _ in ()).throw(AssertionError("must not reclaim during source reservation")))
    with app._ISOLATED_HEAVY_LAUNCH_LOCK:
        app._idle_memory_recovery_cycle()
    assert skips == ["worker-launch-in-progress"]


@pytest.fixture
def retained_budget_store(monkeypatch, tmp_path):
    import budget_pg_store
    monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'retention-proof.sqlite3'}")
    for name in ("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"):
        monkeypatch.delenv(name, raising=False)
    budget_pg_store.reset_engine_cache()
    try:
        yield budget_pg_store
    finally:
        budget_pg_store.reset_engine_cache()


def _collect_budget_scope(scope, stamp, *, max_pages=None, total=1):
    import budget_pg_collection
    return budget_pg_collection.collect_pages(
        dataset="budget", scope=scope, range_start=stamp[:4], range_end=stamp,
        page_size=1, max_pages=max_pages, resume=True,
        fetch=lambda page, size: ([{"fyr": stamp[:4], "dbiz_cd": f"A{scope}:{page}", "amount": 100}], total),
        identity=lambda row: row["dbiz_cd"], source_system="LOFIN", source_operation="QWGJK",
        source_date=lambda row: stamp, checkpoint_contract="TEST_V1",
    )


def test_normal_receipt_retention_keeps_archival_checkpoint_but_allows_new_scope(retained_budget_store):
    import datetime as dt
    store = retained_budget_store
    now = dt.datetime.now(dt.timezone.utc)
    old_day = (now.date() - dt.timedelta(days=10)).isoformat()
    old_scope = f"{old_day[:4]}:{old_day}"
    _collect_budget_scope(old_scope, old_day)
    original = app._isolated_source_durable_receipt_counts("budget")
    assert original["scopes"]["budget:" + old_scope]["pages"] == 1
    engine, tables = store._engine_and_tables()
    with engine.begin() as conn:
        conn.execute(tables["checkpoints"].update().where(tables["checkpoints"].c.scope_key == old_scope)
                     .values(updated_at=(now - dt.timedelta(days=10)).isoformat()))
    outcome = store.purge_history(365, receipt_retention_days=3, now=now)
    assert outcome["deleted_collection_pages"] == 1
    assert store.get_checkpoint("budget", old_scope)["status"] == "COMPLETE"
    before = app._isolated_source_durable_receipt_counts("budget")
    assert before == dyn.empty_progress()
    assert not dyn.NoProgressGate._progressed(original, before)
    g = gate()
    assert g.record_launch("budget", RAW, before)
    today = now.date().isoformat()
    fresh_scope = f"{today[:4]}:{today}"
    _collect_budget_scope(fresh_scope, today, max_pages=1, total=2)
    after = app._isolated_source_durable_receipt_counts("budget")
    assert set(after["scopes"]) == {"budget:" + fresh_scope}
    assert after["scopes"]["budget:" + fresh_scope]["pages"] == 1
    assert g.record_exit("budget", raw_after_bytes=RAW, receipt_after=after, memory_held=True)["last_outcome"] == "DURABLE_PROGRESS"


def test_expiring_scope_cannot_reopen_existing_latch(retained_budget_store):
    import datetime as dt
    store = retained_budget_store
    now = dt.datetime.now(dt.timezone.utc)
    day = (now.date() - dt.timedelta(days=10)).isoformat()
    scope = f"{day[:4]}:{day}"
    _collect_budget_scope(scope, day)
    old_proof = app._isolated_source_durable_receipt_counts("budget")
    g = gate()
    abort(g, before=old_proof)
    abort(g, before=old_proof)
    engine, tables = store._engine_and_tables()
    with engine.begin() as conn:
        conn.execute(tables["checkpoints"].update().values(updated_at=(now - dt.timedelta(days=10)).isoformat()))
    store.purge_history(365, receipt_retention_days=3, now=now)
    omitted = app._isolated_source_durable_receipt_counts("budget")
    assert omitted == dyn.empty_progress()
    assert g.may_start("budget", RAW, omitted) == (False, "NO_PROGRESS_SAME_PAGE_MEMORY_HOLD")


def test_immediately_compacted_history_and_superseded_scopes_are_not_progress(retained_budget_store):
    import datetime as dt
    store = retained_budget_store
    now = dt.datetime.now(dt.timezone.utc)
    old_day = (now.date() - dt.timedelta(days=10)).isoformat()
    history = f"history:{old_day[:4]}:{old_day}"
    _collect_budget_scope(history, old_day)
    # This is the same normal compaction function used immediately after a
    # history completion; exclusion does not depend on claiming these receipts exist.
    store.clear_collection_receipts("budget", history)
    assert app._isolated_source_durable_receipt_counts("budget") == dyn.empty_progress()
    unresolved = f"{old_day[:4]}:{old_day}"
    _collect_budget_scope(unresolved, old_day, max_pages=1, total=2)
    before = app._isolated_source_durable_receipt_counts("budget")
    today = now.date().isoformat()
    result = store.supersede_older_nationwide_checkpoints("budget", int(today[:4]), f"{today[:4]}:{today}")
    assert result["superseded_checkpoints"] == 1
    after = app._isolated_source_durable_receipt_counts("budget")
    assert after == dyn.empty_progress()
    assert not dyn.NoProgressGate._progressed(before, after)


@pytest.mark.parametrize("status,aged", [("RUNNING", True), ("INCOMPLETE", True), ("COMPLETE", False)])
def test_active_or_recent_missing_receipts_still_hold(retained_budget_store, status, aged):
    import datetime as dt
    store = retained_budget_store
    now = dt.datetime.now(dt.timezone.utc)
    day = (now.date() - dt.timedelta(days=10)).isoformat() if aged else now.date().isoformat()
    scope = f"{day[:4]}:{day}"
    _collect_budget_scope(scope, day, max_pages=1, total=2 if status != "COMPLETE" else 1)
    engine, tables = store._engine_and_tables()
    with engine.begin() as conn:
        conn.execute(tables["checkpoints"].update().values(status=status, updated_at=(now - dt.timedelta(days=10) if aged else now).isoformat()))
    store.clear_collection_receipts("budget", scope)
    assert app._isolated_source_durable_receipt_counts("budget") is None


def test_archival_scope_with_malformed_cursor_still_holds(retained_budget_store):
    import datetime as dt
    store = retained_budget_store
    now = dt.datetime.now(dt.timezone.utc)
    day = (now.date() - dt.timedelta(days=10)).isoformat()
    scope = f"{day[:4]}:{day}"
    _collect_budget_scope(scope, day)
    engine, tables = store._engine_and_tables()
    with engine.begin() as conn:
        conn.execute(tables["checkpoints"].update().values(
            updated_at=(now - dt.timedelta(days=10)).isoformat(), cursor_value="{broken"))
    store.clear_collection_receipts("budget", scope)
    assert app._isolated_source_durable_receipt_counts("budget") is None


def test_classification_progress_persists_only_once_for_2477_committed_rows(monkeypatch):
    g = gate()
    token = g.record_launch("budget", RAW, proof())
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_SOURCE_DYNAMIC_CHILD", "1")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_MODE", "budget")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_GENERATION", token)
    monkeypatch.setattr(dyn, "_CLASSIFICATION_PROGRESS_RECORDED", None)
    calls = []
    original = dyn.DatabaseGateStore.transact
    def count(self, source, change, **kwargs):
        calls.append(source)
        return original(self, source, change, **kwargs)
    monkeypatch.setattr(dyn.DatabaseGateStore, "transact", count)
    for _ in range(2477):
        assert dyn.record_classification_commit(rows=1)
    assert calls == ["budget"]
    assert g.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(), memory_held=True)["last_outcome"] == "DURABLE_PROGRESS"
    token2 = g.record_launch("budget", RAW, proof())
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_GENERATION", token2)
    before = len(calls)
    assert dyn.record_classification_commit(rows=1)
    assert len(calls) == before + 1


def test_failed_classification_flag_write_is_not_cached_as_progress(monkeypatch):
    g = gate()
    token = g.record_launch("budget", RAW, proof())
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_SOURCE_DYNAMIC_CHILD", "1")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_MODE", "budget")
    monkeypatch.setenv("G2B_DYNAMIC_SOURCE_GENERATION", token)
    monkeypatch.setattr(dyn, "_CLASSIFICATION_PROGRESS_RECORDED", None)
    original = dyn.DatabaseGateStore.transact
    calls = []
    def fail_first(self, source, change, **kwargs):
        calls.append(source)
        if len(calls) == 1:
            raise RuntimeError("synthetic storage outage")
        return original(self, source, change, **kwargs)
    monkeypatch.setattr(dyn.DatabaseGateStore, "transact", fail_first)
    with pytest.raises(app.memory_guard.MemoryPressureError, match="NO_PROGRESS_DURABLE_WRITE_HOLD"):
        dyn.record_classification_commit(rows=1)
    assert dyn._CLASSIFICATION_PROGRESS_RECORDED is None
    assert dyn.record_classification_commit(rows=1)
    assert calls == ["budget", "budget"]
    assert g.record_exit("budget", raw_after_bytes=RAW, receipt_after=proof(), memory_held=True)["last_outcome"] == "DURABLE_PROGRESS"
