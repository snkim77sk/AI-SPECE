"""Source-free tests; NOT a real 256MiB, PostgreSQL, or live API test."""
import pytest
import g2b_dynamic_source_budget as dyn


def state(raw, *, limit=256*(1 << 20)):
    return {
        "source": "cgroup-v2", "limit_bytes": limit, "current_bytes": raw,
        "guard_ok": True, "process_guard_ok": True, "rss_mib": 93, "oom_group": 0,
    }


def test_decimal_bytes_are_not_220_mib():
    assert dyn.SOURCE_CEILING_BYTES == 220_000_000
    assert dyn.SOURCE_CEILING_BYTES != 220 * (1 << 20)


def test_173_mib_raw_admits_provisional_32_mib_start():
    assert dyn.admission(state(173*(1 << 20)))[0]
    assert not dyn.admission(state(180*(1 << 20)))[0]


def test_do_not_treat_cache_or_effective_as_released_raw():
    sample = state(220_000_000-8*(1 << 20))
    sample.update(effective_bytes=85*(1 << 20), inactive_file_bytes=70*(1 << 20))
    assert dyn.headroom(sample, 8*(1 << 20))[0] is False


@pytest.mark.parametrize("bad", [0, -1, None, True, float("nan"), float("inf")])
def test_fail_closed_missing_invalid_raw(bad):
    assert dyn.admission(state(bad))[0] is False


def test_fail_closed_wrong_tier_and_missing_cgroup():
    assert not dyn.admission(state(173*(1 << 20), limit=512*(1 << 20)))[0]
    sample = state(173*(1 << 20))
    sample["source"] = "cgroup-v1"
    assert not dyn.admission(sample)[0]


def test_parent_exact_byte_threshold():
    sample = state(219_999_999)
    assert dyn.parent_abort_reason(sample) == ""
    sample["current_bytes"] = 220_000_000
    assert dyn.parent_abort_reason(sample) == "CGRP_DYNAMIC_220000000_BYTE_HOLD"


def test_phase_cost_scales_with_payload():
    assert dyn.required_for_phase("parse", payload_bytes=5*(1 << 20)) >= 24*(1 << 20)
    assert dyn.headroom(
        state(199_000_000),
        dyn.required_for_phase("parse", payload_bytes=10*(1 << 20)),
        phase="parse",
    )[0] is False


def test_flag_is_optin(monkeypatch):
    monkeypatch.delenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", raising=False)
    assert not dyn.enabled("budget")
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    assert dyn.enabled("budget") and dyn.enabled("shopping")
    assert not dyn.enabled("match")


def test_phase_guard_never_runs_in_web_process(monkeypatch):
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "0")
    assert dyn.assert_worker_phase("read") is None


def test_dynamic_phase_rejects_unsafe_raw(monkeypatch):
    import memory_guard
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(memory_guard, "container_budget_snapshot", lambda: state(215_000_000))
    with pytest.raises(memory_guard.MemoryPressureError, match="CGRP_DYNAMIC_PARSE_HOLD"):
        dyn.assert_worker_phase("parse", payload_bytes=2*(1 << 20))
