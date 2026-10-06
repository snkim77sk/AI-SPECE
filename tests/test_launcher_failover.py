import run


def test_launcher_falls_back_on_runtime_exception(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run,
        "_run_full_runtime",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")),
    )
    monkeypatch.setattr(run, "_run_emergency_http", lambda: calls.append("recovery"))

    run.main()

    assert calls == ["recovery"]


def test_launcher_falls_back_on_nonzero_system_exit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run,
        "_run_full_runtime",
        lambda: (_ for _ in ()).throw(SystemExit(1)),
    )
    monkeypatch.setattr(run, "_run_emergency_http", lambda: calls.append("recovery"))

    run.main()

    assert calls == ["recovery"]


def test_launcher_clean_return_does_not_start_recovery(monkeypatch):
    calls = []
    monkeypatch.setattr(run, "_run_full_runtime", lambda: calls.append("full"))
    monkeypatch.setattr(run, "_run_emergency_http", lambda: calls.append("recovery"))

    run.main()

    assert calls == ["full"]


def test_recovery_snapshot_is_storage_and_source_io_free():
    snapshot = run._recovery_snapshot()

    assert snapshot["status"] == "ok"
    assert snapshot["process_alive"] is True
    assert snapshot["runtime"] == "G2B_LAUNCHER_RECOVERY"
    assert snapshot["database_touched"] is False
    assert snapshot["source_io_performed"] is False
    assert snapshot["process_instance_id"]
    assert len(snapshot["source_fingerprint"]) == 64
