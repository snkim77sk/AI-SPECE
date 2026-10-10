import pytest

import memory_guard


MIB = 1024 * 1024


def _no_cgroup(monkeypatch):
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: ("", None, None, None, {}, {}),
    )


def test_memory_soft_limit_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("G2B_MEMORY_SOFT_LIMIT_MB", raising=False)
    assert memory_guard.soft_limit_mib() == 160

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "20")
    assert memory_guard.soft_limit_mib() == 96

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "99999")
    assert memory_guard.soft_limit_mib() == 4096


def test_memory_guard_holds_at_process_soft_limit(monkeypatch):
    _no_cgroup(monkeypatch)
    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "160")
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 159.0)
    safe = memory_guard.snapshot(collect=False)
    assert safe["guard_ok"] is True
    assert safe["guard_state"] == "SAFE"

    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 160.0)
    state = memory_guard.snapshot(collect=False)
    assert state["rss_mib"] == 160.0
    assert state["soft_limit_mib"] == 160
    assert state["process_guard_ok"] is False
    assert state["guard_ok"] is False
    assert state["guard_state"] == "PROCESS_RSS_HOLD"


def test_rsk_style_256mib_cgroup_thresholds(monkeypatch):
    limit = 256 * MIB

    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: (
            "cgroup-v2",
            180 * MIB,
            limit,
            190 * MIB,
            {
                "anon": 140 * MIB,
                "file": 100 * MIB,
                "shmem": 4 * MIB,
                "kernel": 8 * MIB,
            },
            {"oom": 0, "oom_kill": 0},
        ),
    )
    budget = memory_guard.container_budget_snapshot()
    assert budget["limit_mib"] == 256.0
    assert budget["wait_threshold_mib"] == 208.0
    assert budget["block_threshold_mib"] == 224.0
    assert budget["effective_mib"] == 184.0
    assert budget["wait_ok"] is True
    assert budget["blocked"] is False


def test_cgroup_wait_and_block_are_enforced_before_256mib_oom(monkeypatch):
    limit = 256 * MIB
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 80.0)

    # 176 MiB anon + bounded 32 MiB file cache = 208 MiB effective.
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: (
            "cgroup-v2",
            230 * MIB,
            limit,
            230 * MIB,
            {"anon": 176 * MIB, "file": 90 * MIB},
            {"oom": 0, "oom_kill": 0},
        ),
    )
    wait_state = memory_guard.snapshot(collect=False)
    assert wait_state["guard_ok"] is False
    assert wait_state["guard_state"] == "CGROUP_WAIT"
    assert wait_state["cgroup_effective_mib"] == 208.0

    # 192 MiB anon + bounded 32 MiB file cache = 224 MiB effective.
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: (
            "cgroup-v2",
            245 * MIB,
            limit,
            245 * MIB,
            {"anon": 192 * MIB, "file": 90 * MIB},
            {"oom": 1, "oom_kill": 1},
        ),
    )
    block_state = memory_guard.snapshot(collect=False)
    assert block_state["guard_ok"] is False
    assert block_state["guard_state"] == "CGROUP_BLOCK"
    assert block_state["cgroup_effective_mib"] == 224.0
    assert block_state["cgroup_blocked"] is True
    assert block_state["cgroup_events"]["oom_kill"] == 1


def test_rsk_style_thresholds_adapt_to_512mib(monkeypatch):
    limit = 512 * MIB
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: ("cgroup-v2", 0, limit, 0, {}, {}),
    )
    budget = memory_guard.container_budget_snapshot()
    assert budget["limit_mib"] == 512.0
    assert budget["wait_threshold_mib"] == 435.2
    assert budget["block_threshold_mib"] == 460.8


def test_cgroup_current_fallback_is_conservative_without_memory_stat(monkeypatch):
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: (
            "cgroup-v2",
            210 * MIB,
            256 * MIB,
            210 * MIB,
            {},
            {},
        ),
    )
    budget = memory_guard.container_budget_snapshot()
    assert budget["effective_mib"] == 210.0
    assert budget["wait_ok"] is False


def test_memory_guard_unknown_rss_uses_cgroup_guard(monkeypatch):
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 0.0)
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: ("", None, None, None, {}, {}),
    )
    assert memory_guard.snapshot(collect=False)["guard_ok"] is True


def test_256mib_unified_web_tier_holds_heavy_work(monkeypatch):
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "160")
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 70.0)
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: (
            "cgroup-v2",
            120 * MIB,
            256 * MIB,
            120 * MIB,
            {"anon": 80 * MIB, "file": 30 * MIB, "kernel": 4 * MIB},
            {"oom": 0, "oom_kill": 0},
        ),
    )

    state = memory_guard.snapshot(collect=False)

    assert state["guard_ok"] is True
    assert state["low_memory_web_hold"] is True
    assert state["heavy_work_ok"] is False
    assert memory_guard.heavy_work_allowed(collect=False) is False


def test_cooperative_guard_fails_closed_on_256mib_web_tier(monkeypatch):
    monkeypatch.setattr(
        memory_guard,
        "snapshot",
        lambda collect=False: {
            "guard_ok": True,
            "heavy_work_ok": False,
            "low_memory_web_hold": True,
            "process_guard_ok": True,
            "cgroup_blocked": False,
        },
    )
    with pytest.raises(memory_guard.MemoryPressureError, match="LOW_MEMORY_WEB_TIER"):
        memory_guard.wait_for_heavy_work_budget(timeout=0)


def test_cooperative_guard_returns_immediately_when_safe(monkeypatch):
    expected = {
        "guard_ok": True,
        "heavy_work_ok": True,
        "low_memory_web_hold": False,
        "process_guard_ok": True,
        "cgroup_blocked": False,
    }
    monkeypatch.setattr(
        memory_guard,
        "snapshot",
        lambda collect=False: dict(expected),
    )
    assert memory_guard.cooperative_batch_checkpoint(timeout=0) == expected


def test_collection_and_match_loops_have_inflight_memory_checkpoints():
    from pathlib import Path

    for path in (
        "vnext_collection.py",
        "budget_pg_collection.py",
        "classification_vnext.py",
        "budget_shopping_match_vnext.py",
    ):
        source = Path(path).read_text(encoding="utf-8")
        assert "cooperative_batch_checkpoint" in source


def test_isolated_heavy_worker_protects_web_and_preserves_data_contract():
    from pathlib import Path

    source = Path("g2b_heavy_worker.py").read_text(encoding="utf-8")
    assert 'write_text("900", encoding="ascii")' in source
    assert "g2b_heavy_background_worker.lock" in source
    assert "g2b-heavy-worker-parent-watchdog" in source
    assert 'os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"' in source
    assert 'os.environ["G2B_V41_FRESH_START"] = "0"' in source
    assert "wait_for_heavy_work_budget(timeout=30.0)" in source
    assert "initialize_backend(force=True)" in source


def test_native_process_tuning_fills_blank_values_only(monkeypatch):
    for name in (
        "MALLOC_ARENA_MAX",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OMP_NUM_THREADS", "4")

    values = memory_guard.apply_default_process_tuning()

    assert values["MALLOC_ARENA_MAX"] == "2"
    assert values["OMP_NUM_THREADS"] == "4"
    assert values["OPENBLAS_NUM_THREADS"] == "1"
    assert values["MKL_NUM_THREADS"] == "1"


def test_postgres_pool_defaults_are_memory_bounded():
    from pathlib import Path

    source = Path("g2b_database.py").read_text(encoding="utf-8")
    assert '_env_int("G2B_DB_POOL_SIZE", 1, lower=1, upper=2)' in source
    assert '_env_int("G2B_DB_MAX_OVERFLOW", 1, lower=0, upper=1)' in source


def test_postgres_pool_environment_values_are_capped(monkeypatch):
    import g2b_database

    monkeypatch.setenv("G2B_DB_POOL_SIZE", "99")
    monkeypatch.setenv("G2B_DB_MAX_OVERFLOW", "99")
    assert g2b_database._env_int(
        "G2B_DB_POOL_SIZE", 1, lower=1, upper=2
    ) == 2
    assert g2b_database._env_int(
        "G2B_DB_MAX_OVERFLOW", 1, lower=0, upper=1
    ) == 1


def test_run_port_guard_is_stdlib_only_and_tunes_child_environment():
    from pathlib import Path

    source = Path("run.py").read_text(encoding="utf-8")
    assert "import memory_guard" not in source
    assert "import uvicorn" not in source
    assert '"-m",\n        "uvicorn",' in source
    assert '"MALLOC_ARENA_MAX": "2"' in source
    assert '"OPENBLAS_NUM_THREADS": "1"' in source
    assert 'env["G2B_SUPERVISED_CHILD"] = "1"' in source
    assert source.index("server = _bind_server(external_port)") < source.index(
        "supervisor.start()"
    )


def test_isolated_worker_projected_headroom_blocks_256mb_spawn():
    state = {
        "guard_ok": True,
        "guard_state": "SAFE",
        "cgroup_oom_group": 0,
        "cgroup_limit_mib": 256.0,
        "cgroup_current_mib": 150.0,
    }
    result = memory_guard.isolated_worker_memory_admission(state)
    assert result["allowed"] is False
    assert result["reason"] == "CGROUP_HEADROOM_HOLD"
    assert result["headroom_mib"] == 106.0
    assert result["required_headroom_mib"] == 128


def test_isolated_worker_projected_headroom_allows_safe_256mb_spawn():
    state = {
        "guard_ok": True,
        "guard_state": "SAFE",
        "cgroup_oom_group": 0,
        "cgroup_limit_mib": 256.0,
        "cgroup_current_mib": 120.0,
    }
    result = memory_guard.isolated_worker_memory_admission(state)
    assert result["allowed"] is True
    assert result["headroom_mib"] == 136.0


def test_isolated_worker_admission_respects_guard_before_headroom():
    state = {
        "guard_ok": False,
        "guard_state": "CGROUP_WAIT",
        "cgroup_oom_group": 0,
        "cgroup_limit_mib": 256.0,
        "cgroup_current_mib": 80.0,
    }
    result = memory_guard.isolated_worker_memory_admission(state)
    assert result["allowed"] is False
    assert result["reason"] == "CGROUP_WAIT"
