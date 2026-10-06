import pytest

import memory_guard


MIB = 1024 * 1024


def _cgroup(limit_mib, *, current_mib=100, anon_mib=70, file_mib=20,
            shmem_mib=0, kernel_mib=8, events=None):
    return (
        "cgroup-v2",
        int(current_mib * MIB),
        int(limit_mib * MIB),
        {
            "anon": int(anon_mib * MIB),
            "file": int(file_mib * MIB),
            "shmem": int(shmem_mib * MIB),
            "kernel": int(kernel_mib * MIB),
        },
        dict(events or {}),
        0,
    )


def test_memory_soft_limit_adapts_to_256_and_512_mib(monkeypatch):
    monkeypatch.delenv("G2B_MEMORY_SOFT_LIMIT_MB", raising=False)

    monkeypatch.setattr(memory_guard, "_cgroup_values", lambda: _cgroup(256))
    assert memory_guard.soft_limit_mib() == 160

    snapshot_256 = memory_guard.memory_budget_snapshot()
    assert snapshot_256["limit_mib"] == 256.0
    assert snapshot_256["wait_threshold_mib"] == 208.0
    assert snapshot_256["block_threshold_mib"] == 224.0

    monkeypatch.setattr(memory_guard, "_cgroup_values", lambda: _cgroup(512))
    assert memory_guard.soft_limit_mib() == 320

    snapshot_512 = memory_guard.memory_budget_snapshot()
    assert snapshot_512["limit_mib"] == 512.0
    assert snapshot_512["wait_threshold_mib"] == 435.2
    assert snapshot_512["block_threshold_mib"] == 460.8


def test_explicit_process_soft_limit_still_overrides_adaptive_value(monkeypatch):
    monkeypatch.setattr(memory_guard, "_cgroup_values", lambda: _cgroup(512))

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "160")
    assert memory_guard.soft_limit_mib() == 160

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "20")
    assert memory_guard.soft_limit_mib() == 96

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "99999")
    assert memory_guard.soft_limit_mib() == 4096

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "auto")
    assert memory_guard.soft_limit_mib() == 320


def test_cgroup_effective_pressure_discounts_reclaimable_file_cache(monkeypatch):
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: _cgroup(
            256,
            current_mib=245,
            anon_mib=100,
            file_mib=125,
            shmem_mib=0,
            kernel_mib=8,
        ),
    )

    state = memory_guard.memory_budget_snapshot()

    # 256 MiB tier counts only a 32 MiB floor of the reclaimable file cache.
    assert state["current_mib"] == 245.0
    assert state["effective_mib"] == 140.0
    assert state["effective_mib"] < state["wait_threshold_mib"]


def test_memory_guard_blocks_at_process_soft_limit(monkeypatch):
    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "160")
    monkeypatch.setattr(memory_guard, "_cgroup_values", lambda: _cgroup(256))
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 159.0)
    assert memory_guard.heavy_work_allowed(collect=False) is True

    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 160.0)
    state = memory_guard.snapshot(collect=False)
    assert state["rss_mib"] == 160.0
    assert state["soft_limit_mib"] == 160
    assert state["guard_ok"] is False
    assert state["blocked"] is True
    assert memory_guard.heavy_work_allowed(collect=False) is False


def test_memory_guard_blocks_before_256_mib_container_hard_limit(monkeypatch):
    monkeypatch.delenv("G2B_MEMORY_SOFT_LIMIT_MB", raising=False)
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 100.0)
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: _cgroup(
            256,
            current_mib=238,
            anon_mib=188,
            file_mib=32,
            shmem_mib=0,
            kernel_mib=5,
        ),
    )

    state = memory_guard.snapshot(collect=False)

    assert state["effective_mib"] == 225.0
    assert state["block_threshold_mib"] == 224.0
    assert state["limit_mib"] == 256.0
    assert state["guard_ok"] is False
    assert state["blocked"] is True


def test_memory_guard_exposes_oom_events(monkeypatch):
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: _cgroup(
            256,
            events={"high": 2, "max": 3, "oom": 1, "oom_kill": 1},
        ),
    )
    state = memory_guard.snapshot(collect=False)
    assert state["events"]["high"] == 2
    assert state["events"]["max"] == 3
    assert state["events"]["oom"] == 1
    assert state["events"]["oom_kill"] == 1


def test_wait_for_heavy_work_budget_waits_then_allows(monkeypatch):
    states = iter([
        {"guard_ok": False, "blocked": False},
        {"guard_ok": True, "blocked": False},
    ])
    monkeypatch.setattr(
        memory_guard,
        "snapshot",
        lambda collect=False: next(states),
    )
    monkeypatch.setattr(memory_guard.time, "sleep", lambda _seconds: None)

    allowed = memory_guard.wait_for_heavy_work_budget(timeout=1.0)
    assert allowed["guard_ok"] is True


def test_wait_for_heavy_work_budget_blocks_without_starting_work(monkeypatch):
    state = {
        "guard_ok": False,
        "blocked": True,
        "rss_mib": 170.0,
        "soft_limit_mib": 160,
        "effective_mib": 225.0,
        "block_threshold_mib": 224.0,
        "limit_mib": 256.0,
    }
    monkeypatch.setattr(
        memory_guard,
        "snapshot",
        lambda collect=False: dict(state),
    )
    with pytest.raises(memory_guard.MemoryPressureError):
        memory_guard.wait_for_heavy_work_budget(timeout=1.0)


def test_process_tuning_fills_blank_values_and_preserves_explicit(monkeypatch):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "")
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "2")
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)

    tuning = memory_guard.apply_default_process_tuning()

    assert tuning["MALLOC_ARENA_MAX"] == "2"
    assert tuning["OMP_NUM_THREADS"] == "1"
    assert tuning["OPENBLAS_NUM_THREADS"] == "2"
    assert tuning["MKL_NUM_THREADS"] == "1"


def test_memory_guard_unknown_rss_and_cgroup_fails_open(monkeypatch):
    monkeypatch.delenv("G2B_MEMORY_SOFT_LIMIT_MB", raising=False)
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 0.0)
    monkeypatch.setattr(
        memory_guard,
        "_cgroup_values",
        lambda: ("", None, None, {}, {}, 0),
    )
    state = memory_guard.snapshot(collect=False)
    assert state["soft_limit_mib"] == 160
    assert state["guard_ok"] is True


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
