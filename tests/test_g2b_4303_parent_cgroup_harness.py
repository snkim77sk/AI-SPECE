"""Harness invariants only; these unit tests are not real cgroup acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import g2b_4303_parent_fixture_ci as fixture
import g2b_4303_parent_cgroup_ci as harness


def allowed_env(scenario="json"):
    return {"G2B_4303_SCENARIO": scenario, "G2B_CI_PARENT_4303": "1",
        "G2B_DATABASE_URL": fixture.CI_DATABASE, "G2B_TEST_MODE": "0",
        "G2B_AUTO_SYNC_DISABLE": "1", "G2B_256MIB_DYNAMIC_SOURCE_BUDGET": "1",
        "G2B_APP_SCHEMA": f"g2b_app_parent_4303_{scenario}",
        "G2B_BUDGET_SCHEMA": f"g2b_budget_parent_4303_{scenario}"}


@pytest.mark.parametrize("scenario", fixture.SCENARIOS)
def test_authorization_exact_disposable_scopes(scenario):
    env = allowed_env(scenario)
    assert fixture.authorized(env)
    for key, value in (("G2B_DATABASE_URL", "postgresql://production/g2b"),
                       ("G2B_TEST_MODE", "1"), ("G2B_AUTO_SYNC_DISABLE", "0"),
                       ("G2B_CI_PARENT_4303", "0"), ("G2B_APP_SCHEMA", "g2b_app"),
                       ("G2B_BUDGET_SCHEMA", "g2b_budget"), ("G2B_SERVICE_KEY", "unexpected"),
                       ("LOFIN_API_KEY", "unexpected"), ("EDUINFO_API_KEY", "unexpected"),
                       ("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "0")):
        assert not fixture.authorized({**env, key: value}), key


def test_kernel_peak_strict_decimal_and_all_oom_events():
    good = {"max": 0, "oom": 0, "oom_kill": 0}
    harness.check_kernel_evidence(good, good, 219_999_999)
    for peak in (0, 220_000_000, 220 * fixture.MIB):
        with pytest.raises(AssertionError):
            harness.check_kernel_evidence(good, good, peak)
    for key in good:
        with pytest.raises(AssertionError):
            harness.check_kernel_evidence(good, {**good, key: 1}, 200_000_000)
    with pytest.raises(AssertionError):
        harness.check_kernel_evidence({}, good, 200_000_000)


def test_baseline_requires_raw_effective_private_clean_disk():
    state = {"source": "cgroup-v2", "limit_bytes": 256 * fixture.MIB,
        "current_bytes": 173 * fixture.MIB, "effective_bytes": 115 * fixture.MIB,
        "file_bytes": 90 * fixture.MIB, "inactive_file_bytes": 90 * fixture.MIB,
        "shmem_bytes": 0, "file_dirty_bytes": 0, "file_writeback_bytes": 0}
    fixture.baseline_contract(state)
    for key, value in (("current_bytes", 170 * fixture.MIB),
                       ("effective_bytes", 112 * fixture.MIB), ("shmem_bytes", 4 * fixture.MIB),
                       ("file_dirty_bytes", 2 * fixture.MIB), ("file_writeback_bytes", 2 * fixture.MIB),
                       ("inactive_file_bytes", 30 * fixture.MIB)):
        with pytest.raises(AssertionError):
            fixture.baseline_contract({**state, key: value})


@pytest.mark.parametrize("mode", ("shopping", "budget"))
@pytest.mark.parametrize("fmt", ("json", "xml"))
def test_fixture_bytes_use_actual_bounded_parser_and_canonical_hash(tmp_path, monkeypatch, mode, fmt):
    import g2b_dynamic_source_budget as dynamic
    import lofin_vnext_http
    import vnext_http
    import vnext_source_guard
    monkeypatch.setenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "1")
    monkeypatch.setenv("G2B_SOURCE_ONLY_RUNTIME", "1")
    monkeypatch.setattr(dynamic, "allocation_checkpoint", lambda **kwargs: None)
    path = tmp_path / ("fixture." + fmt)
    fixture.write_fixture(path, fmt, mode, 1, "2026-10-16", rows=3, pages=1)
    parse = vnext_http.parse_response if mode == "shopping" else lofin_vnext_http.parse_response
    rows, total = parse(path.read_bytes())[:2]
    expected = [fixture.fixture_row(mode, 1, index, "2026-10-16") for index in range(3)]
    assert rows == expected and total == 3
    assert vnext_source_guard.source_transport_result_sha256(rows, total) == vnext_source_guard.source_transport_result_sha256(expected, total)


@pytest.mark.parametrize("fmt", ("json", "xml"))
def test_near_cap_fixture_is_exact_legal_transport_length(tmp_path, fmt):
    path = tmp_path / ("near." + fmt)
    fixture.write_fixture(path, fmt, "budget", 1, "2026-10-16", rows=1000, pages=1, near_cap=True)
    assert path.stat().st_size == 16 * fixture.MIB - 1


def test_lifecycle_requires_real_parent_serial_reap_pairs():
    events = [{"event": "spawned", "parent_pid": 7, "child_pid": 8, "mode": "shopping"},
              {"event": "reaped", "parent_pid": 7, "child_pid": 8},
              {"event": "spawned", "parent_pid": 7, "child_pid": 9, "mode": "budget"},
              {"event": "reaped", "parent_pid": 7, "child_pid": 9}]
    assert [x["mode"] for x in harness.validate_lifecycle(events, 7)] == ["shopping", "budget"]
    for invalid in (events[:1], [events[0], events[2], events[1], events[3]],
                    [{**events[0], "parent_pid": 99}, *events[1:]]):
        with pytest.raises(AssertionError):
            harness.validate_lifecycle(invalid, 7)


def test_checkpoint_requires_exact_receipts_and_transaction_counts():
    cp = {"verified": True, "page_no": 4, "page_size": 32, "fetched_count": 96,
          "saved_count": 96, "status": "INCOMPLETE", "cursor_value": json.dumps({"generation": "g"})}
    worker = {"checkpoint": cp, "page_size": 32, "item_count": 96, "state_count": 96, "receipts": [1, 2, 3]}
    assert harness.check_budget_checkpoint(worker, 4, status="INCOMPLETE") == "g"
    for change in ({"state_count": 98}, {"receipts": [1, 2, 3, 4]}, {"item_count": 64}):
        with pytest.raises(AssertionError):
            harness.check_budget_checkpoint({**worker, **change}, 4, status="INCOMPLETE")


def test_harness_is_not_a_production_hook_and_cannot_claim_skipped_run():
    shell = (ROOT / "scripts/g2b_256mib_dynamic_4303_ci.sh").read_text()
    assert "DOCKER_UNAVAILABLE_REAL_CGROUP_NOT_RUN" in shell and "exit 2" in shell
    web = (ROOT / "scripts/g2b_4303_parent_web_ci.py").read_text()
    child = (ROOT / "scripts/g2b_4303_parent_child_ci.py").read_text()
    assert "original_launch(mode)" in web and "original_wait(process, mode)" in web
    assert "app._run_low_memory_automatic_cycle()" in web
    assert "require_attested_transport_result =" not in child
    assert "source_transport_result_sha256 = measured_digest" in child
    assert "original_digest(*args, **kwargs)" in child
    for path in ROOT.glob("*.py"):
        assert "g2b_4303_parent_" not in path.read_text(), path


def test_normal_budget_scope_keeps_1000_page_size_and_2477_backlog():
    for name in ("json", "xml", "rollback", "no_progress"):
        size, pages = fixture.scenario_shape(name, "budget")
        total = fixture.scenario_total(name, "budget")
        assert size == 1000 and pages == 3 and total == 2477
        assert [min(size, total - (page - 1) * size) for page in range(1, pages + 1)] == [1000, 1000, 477]


def test_partial_final_1000_page_checkpoint_preserves_exact_counts():
    cp = {"verified": True, "page_no": 4, "page_size": 1000, "fetched_count": 2477,
          "saved_count": 2477, "status": "COMPLETE", "cursor_value": json.dumps({"generation": "same"})}
    worker = {"checkpoint": cp, "page_size": 1000, "total_rows": 2477,
              "item_count": 2477, "state_count": 2477, "receipts": [1, 2, 3]}
    assert harness.check_budget_checkpoint(worker, 4, status="COMPLETE") == "same"


def test_candidate_stays_default_off(monkeypatch):
    import g2b_dynamic_source_budget as dynamic
    monkeypatch.delenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", raising=False)
    assert not dynamic.enabled("shopping") and not dynamic.enabled("budget")
    assert dynamic.START_RESERVE_BYTES == 52 * fixture.MIB


def test_measured_http_response_enforces_parser_read_limit():
    from io import BytesIO
    from types import SimpleNamespace
    from g2b_4303_parent_child_ci import MeasuredResponse
    evidence = SimpleNamespace(read_calls=0, read_bytes=0, max_read_request_bytes=0)
    with MeasuredResponse(BytesIO(b"x" * 20000), evidence) as response:
        assert len(response.read(16384)) == 8191
        assert len(response.read(16384)) == 127
        with pytest.raises(AssertionError):
            response.read(-1)
        with pytest.raises(AssertionError):
            response.read(16385)
    assert evidence.read_bytes == 8318 and evidence.max_read_request_bytes == 16384


def test_baseline_calibrates_after_real_idle_heap_reclamation(tmp_path, monkeypatch):
    import g2b_idle_memory_recovery as recovery
    events = []
    memory = {"anon": 60 * fixture.MIB, "file": 32 * fixture.MIB,
              "kernel": 4 * fixture.MIB}
    ballast = fixture.Ballast(tmp_path / "private-disk")
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == "/proc/self/mountinfo":
            return "1 0 8:1 / / rw - ext4 /dev/test rw\n"
        return original_read(path, *args, **kwargs)
    def snapshot():
        return {"source": "cgroup-v2", "limit_bytes": 256 * fixture.MIB,
                "current_bytes": sum(memory.values()),
                "effective_bytes": memory["anon"] + memory["kernel"] + min(memory["file"], 32 * fixture.MIB),
                "file_bytes": memory["file"], "inactive_file_bytes": memory["file"],
                "shmem_bytes": 0, "file_dirty_bytes": 0, "file_writeback_bytes": 0}
    def trim():
        events.append("trim")
        memory["anon"] -= 7 * fixture.MIB
        return True
    def add_cache(size):
        assert events[:2] == ["gc", "trim"]
        memory["file"] += size
    def add_anon(size, owner):
        assert events[:2] == ["gc", "trim"]
        events.append("private-anon")
        memory["anon"] += size
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(fixture.gc, "collect", lambda: events.append("gc"))
    monkeypatch.setattr(recovery, "_trim_allocator", trim)
    monkeypatch.setattr(fixture, "cgroup_snapshot", snapshot)
    monkeypatch.setattr(fixture.time, "sleep", lambda _: None)
    monkeypatch.setattr(ballast, "add_cache", add_cache)
    monkeypatch.setattr(ballast, "add_anon", add_anon)
    try:
        result = ballast.prepare()
    finally:
        ballast.cache.close()
    assert events[:3] == ["gc", "trim", "private-anon"]
    assert result["effective_bytes"] == 115 * fixture.MIB
    assert result["current_bytes"] == 173 * fixture.MIB
    # The 7MiB reclaimed warmup heap must be replaced before testing HOLDs.
    assert memory["anon"] == 79 * fixture.MIB
