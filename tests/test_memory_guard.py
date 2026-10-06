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
