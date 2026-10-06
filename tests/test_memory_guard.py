import memory_guard


def test_memory_soft_limit_defaults_and_clamps(monkeypatch):
    monkeypatch.delenv("G2B_MEMORY_SOFT_LIMIT_MB", raising=False)
    assert memory_guard.soft_limit_mib() == 160

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "20")
    assert memory_guard.soft_limit_mib() == 96

    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "99999")
    assert memory_guard.soft_limit_mib() == 4096


def test_memory_guard_holds_at_soft_limit(monkeypatch):
    monkeypatch.setenv("G2B_MEMORY_SOFT_LIMIT_MB", "160")
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 159.0)
    assert memory_guard.heavy_work_allowed(collect=False) is True

    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 160.0)
    state = memory_guard.snapshot(collect=False)
    assert state == {
        "rss_mib": 160.0,
        "soft_limit_mib": 160,
        "guard_ok": False,
    }
    assert memory_guard.heavy_work_allowed(collect=False) is False


def test_memory_guard_unknown_rss_fails_open(monkeypatch):
    monkeypatch.setattr(memory_guard, "current_rss_mib", lambda: 0.0)
    assert memory_guard.snapshot(collect=False)["guard_ok"] is True


def test_postgres_pool_defaults_are_memory_bounded():
    from pathlib import Path

    source = Path("g2b_database.py").read_text(encoding="utf-8")
    assert '_env_int("G2B_DB_POOL_SIZE", 1, lower=1, upper=2)' in source
    assert '_env_int("G2B_DB_MAX_OVERFLOW", 1, lower=0, upper=1)' in source


def test_postgres_pool_environment_values_are_capped(monkeypatch):
    import g2b_database

    monkeypatch.setenv("G2B_DB_POOL_SIZE", "99")
    monkeypatch.setenv("G2B_DB_MAX_OVERFLOW", "99")
    source = __import__("pathlib").Path("g2b_database.py").read_text(encoding="utf-8")
    assert '_env_int("G2B_DB_POOL_SIZE", 1, lower=1, upper=2)' in source
    assert '_env_int("G2B_DB_MAX_OVERFLOW", 1, lower=0, upper=1)' in source
