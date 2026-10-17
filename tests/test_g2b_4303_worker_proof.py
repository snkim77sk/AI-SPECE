"""Startup/transaction safety unit evidence, not real-cgroup peak evidence."""
import builtins
from contextlib import nullcontext
import pytest
import memory_guard as guard
import g2b_heavy_worker as worker


def profile(raw=173 * (1 << 20), limit=256 * (1 << 20)):
    return dict(source='cgroup-v2', limit_bytes=limit, current_bytes=raw,
                cgroup_source='cgroup-v2', cgroup_limit_bytes=limit,
                cgroup_current_bytes=raw, cgroup_limit_mib=256,
                process_guard_ok=True, heavy_work_ok=False, rss_mib=95,
                soft_limit_mib=112, oom_group=0)


@pytest.mark.parametrize('limit', [0, None, float('nan')])
def test_admitted_child_lost_first_probe_exits_before_app_or_database(monkeypatch, limit):
    monkeypatch.setattr(worker.os, 'environ', dict(worker.os.environ))
    monkeypatch.setenv('G2B_256MIB_DYNAMIC_SOURCE_BUDGET', '1')
    monkeypatch.setenv('G2B_TEST_MODE', '0')
    monkeypatch.setenv('G2B_SOURCE_DYNAMIC_CHILD', '1')
    monkeypatch.setattr(worker, '_worker_slot', lambda **kw: nullcontext())
    monkeypatch.setattr(worker, '_deprioritize', lambda: None)
    monkeypatch.setattr(worker, '_start_parent_watchdog', lambda: None)
    monkeypatch.setattr(guard, 'apply_default_process_tuning', lambda: None)
    monkeypatch.setattr(guard, 'snapshot', lambda **kw: profile(limit=limit))
    monkeypatch.setattr(worker, '_persist_source_failure_detail',
                        lambda *a: pytest.fail('no database import on startup proof failure'))
    real_import = builtins.__import__
    def checked_import(name, *args, **kwargs):
        if name in {'vnext_clean_app', 'db'}:
            pytest.fail('no full app/database import on startup proof failure')
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', checked_import)
    assert worker.main(['budget']) == 75
    assert worker.os.environ['G2B_SOURCE_ONLY_RUNTIME'] == '1'


def test_dynamic_worker_does_not_wait_at_legacy_effective_threshold(monkeypatch):
    monkeypatch.setenv('G2B_256MIB_DYNAMIC_SOURCE_BUDGET', '1')
    monkeypatch.setenv('G2B_RUNTIME_ROLE', 'LOCAL_COLLECTOR')
    monkeypatch.setenv('G2B_SOURCE_ONLY_RUNTIME', '1')
    monkeypatch.setenv('G2B_TEST_MODE', '0')
    sample = profile(206 * (1 << 20))
    monkeypatch.setattr(guard, 'snapshot', lambda **kw: sample)
    monkeypatch.setattr(guard, 'container_budget_snapshot', lambda: sample)
    monkeypatch.setattr(guard, 'current_rss_mib', lambda: 95)
    monkeypatch.setattr(guard, 'soft_limit_mib', lambda: 112)
    monkeypatch.setattr(guard.time, 'sleep', lambda *_: pytest.fail('no sleeping heavy child'))
    assert guard.wait_for_heavy_work_budget() is sample
    sample['current_bytes'] = sample['cgroup_current_bytes'] = 220_000_000
    with pytest.raises(guard.MemoryPressureError):
        guard.wait_for_heavy_work_budget()


def test_verified_512_tier_keeps_legacy_policy(monkeypatch):
    monkeypatch.setenv('G2B_256MIB_DYNAMIC_SOURCE_BUDGET', '1')
    monkeypatch.setenv('G2B_RUNTIME_ROLE', 'LOCAL_COLLECTOR')
    monkeypatch.setenv('G2B_SOURCE_ONLY_RUNTIME', '1')
    monkeypatch.setenv('G2B_TEST_MODE', '0')
    monkeypatch.setattr(guard, 'container_budget_snapshot', lambda: profile(limit=512*(1 << 20)))
    assert guard.source_dynamic_budget_enabled() is False


def test_partial_reclaim_small_remaining_shortfall_still_requests_bounded_cache(monkeypatch):
    import g2b_idle_memory_recovery as rec
    monkeypatch.setenv('G2B_256MIB_DYNAMIC_SOURCE_BUDGET', '1')
    monkeypatch.setenv('G2B_RUNTIME_ROLE', 'UNIFIED')
    monkeypatch.setenv('G2B_IDLE_CGROUP_FILE_RECLAIM', '1')
    before = profile(160 * (1 << 20))
    before.update(guard_ok=True, cgroup_current_mib=160, cgroup_effective_mib=115)
    cache = dict(source='cgroup-v2', limit_mib=256, current_mib=160,
                 effective_mib=115, current_bytes=160*(1 << 20),
                 file_bytes=100*(1 << 20), inactive_file_bytes=68*(1 << 20))
    written = []
    monkeypatch.setattr(rec.memory_guard, 'container_budget_snapshot', lambda: cache)
    monkeypatch.setattr(rec.memory_guard, 'snapshot', lambda **kw: before)
    monkeypatch.setattr(rec, '_write_cgroup_file_reclaim', written.append)
    result, sample = rec._idle_cgroup_cache_reclaim(before)
    assert 1*(1 << 20) <= written[0] < 8*(1 << 20)
    assert result['status'] == 'no-effect'  # successful write is not evidence of release
    assert sample == before


def test_deterministic_response_bound_has_distinct_reaped_exit():
    assert worker._source_memory_exit_code('MEMORY_PRESSURE:SOURCE_RESPONSE_LIMIT:field bytes') == 79
    assert worker._source_memory_exit_code('CGRP_DYNAMIC_READ_HOLD') == 75


def test_operator_marker_failure_does_not_mask_original_deterministic_exit(monkeypatch):
    import g2b_dynamic_source_budget as dynamic
    monkeypatch.setattr(dynamic, 'record_operator_hold', lambda: (_ for _ in ()).throw(RuntimeError('fixture')))
    detail = 'SOURCE_RESPONSE_LIMIT:stored classification row bytes'
    worker._persist_dynamic_operator_hold(detail)
    assert worker._source_memory_exit_code(detail) == 79


def test_dynamic_monitor_uses_decimal_mb_and_actual_start_threshold():
    import vnext_clean_app as app
    html = app._collection_worker_capacity_notice({
        'shopping_run_state': 'WAITING_MEMORY',
        'memory_worker_dynamic_source_enabled': True,
        'memory_worker_admission_reason': 'CGRP_DYNAMIC_START_HOLD',
        'memory_cgroup_current_bytes': 173*(1 << 20),
        'memory_cgroup_effective_mib': 115,
    })
    assert '220.00MB (209.81MiB)' in html
    assert '181.40MB' in html
    assert '165.47MB 미만' in html
    assert '52MiB' in html
    assert '64MiB' not in html
