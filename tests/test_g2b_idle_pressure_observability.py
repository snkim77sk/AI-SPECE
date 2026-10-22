"""Source-free pressure rechecks: visibility without relaxed collection guards."""
import asyncio
import time

import pytest

import g2b_idle_memory_recovery as rec
import vnext_clean_app as app


@pytest.fixture
def idle(monkeypatch):
    now = time.monotonic()
    for name, value in {
        "_START_MONOTONIC": now,
        "_LAST_ACTIVITY": now - 200,
        "_LAST_ATTEMPT": 0.0,
        "_ACTIVE_REQUESTS": 0,
        "_ATTEMPTS": 0,
        "_SKIPS": 0,
        "_LAST_RESULT": {"status": "waiting", "reason": "startup"},
        "_LAST_CHECK_RESULT": {"status": "waiting", "reason": "startup"},
        "_LAST_CHECK_MONOTONIC": 0.0,
        "_NEXT_CHECK_MONOTONIC": 0.0,
        "_LAST_ACTIVITY_CATEGORY": "startup",
    }.items():
        monkeypatch.setattr(rec, name, value)
    monkeypatch.setattr(rec.memory_guard, "snapshot", lambda **kw: {
        "guard_ok": True, "rss_mib": 95, "cgroup_limit_mib": 256,
        "cgroup_current_mib": 173, "cgroup_effective_mib": 115,
        "cgroup_oom_group": 0,
    })
    monkeypatch.setattr(rec, "_trim_allocator", lambda: True)
    calls = []
    monkeypatch.setattr(rec.gc, "collect", lambda: calls.append("gc") or 0)
    return calls


def cycle(**kwargs):
    return rec.run_recovery_cycle(
        workers_busy=kwargs.get("workers_busy", lambda: False),
        backend_ready=kwargs.get("backend_ready", lambda: True),
    )


def test_skip_reports_actual_reason_without_hiding_last_attempt(idle):
    assert cycle()["status"] == "completed"
    completed = rec.recovery_status()["last_result"]
    rec.mark_request_start()
    try:
        assert cycle()["reason"] == "requests-active"
        status = rec.recovery_status()
        assert status["last_check"] == {"status": "skipped", "reason": "requests-active"}
        assert status["last_result"] == completed
        assert status["attempts"] == 1 and status["skips"] == 1
        assert status["seconds_since_last_check"] >= 0
    finally:
        rec.mark_request_end()
    assert idle == ["gc"]


def test_launch_barrier_skip_is_visible_without_counting_reclaim(idle):
    with app._ISOLATED_HEAVY_LOCK:
        assert app._idle_memory_recovery_cycle()["reason"] == "worker-launch-in-progress"
    status = rec.recovery_status()
    assert status["last_check"]["reason"] == "worker-launch-in-progress"
    assert status["skips"] == 1 and status["attempts"] == 0
    assert idle == []


def test_one_minute_retry_can_reclaim_after_visitor_becomes_idle(monkeypatch, idle):
    now = time.monotonic()
    monkeypatch.setattr(rec, "_LAST_ACTIVITY", now - 20)
    assert cycle()["reason"] == "recent-visitor-activity"
    monkeypatch.setattr(rec.time, "monotonic", lambda: now + 60)
    assert cycle()["status"] == "completed"
    # Minute checks do not turn the one-hour actual-attempt cooldown into a minute.
    for minute in range(2, 60):
        monkeypatch.setattr(rec.time, "monotonic", lambda minute=minute: now + minute * 60)
        assert cycle()["reason"] == "cooldown"
    assert idle == ["gc"]


def test_normal_schedule_stays_48_to_72_hours(monkeypatch, idle):
    monkeypatch.setattr(rec.memory_guard, "snapshot", lambda **kw: {
        "guard_ok": True, "rss_mib": 70, "cgroup_limit_mib": 512,
        "cgroup_current_mib": 140, "cgroup_effective_mib": 125,
        "cgroup_oom_group": 0,
    })
    assert cycle()["reason"] == "periodic-not-due"
    assert rec.periodic_seconds() == 72 * 3600
    monkeypatch.setenv("G2B_IDLE_MEMORY_RECLAIM_HOURS", "1")
    assert rec.periodic_seconds() == 48 * 3600
    assert idle == []


def test_request_categories_do_not_store_paths_or_query_values(idle):
    async def handler(scope, receive, send):
        assert rec.recovery_status()["active_requests"] == 1
        assert cycle()["reason"] == "requests-active"
    async def scenario():
        middleware = rec.ActivityMiddleware(handler)
        await middleware({"type": "http", "path": "/api/dashboard-summary", "method": "GET"}, None, None)
        assert rec.recovery_status()["last_activity_category"] == "dashboard-summary"
        await middleware({"type": "http", "path": "/download/private-identifier", "query_string": b"token=secret", "method": "GET"}, None, None)
        assert rec.recovery_status()["last_activity_category"] == "other-read"
        await middleware({"type": "http", "path": "/settings/private", "method": "POST"}, None, None)
        assert rec.recovery_status()["last_activity_category"] == "mutation"
    asyncio.run(scenario())
    assert "secret" not in repr(rec.recovery_status())
    assert "private" not in repr(rec.recovery_status())
    assert idle == []


def test_passive_monitor_and_health_cannot_reset_activity_category(idle):
    before = rec._LAST_ACTIVITY
    async def handler(scope, receive, send):
        expected = 0 if scope["path"] == "/health" else 1
        assert rec.recovery_status()["active_requests"] == expected
    async def scenario():
        middleware = rec.ActivityMiddleware(handler)
        for path in ("/health", "/collection-monitor", "/api/collection-status"):
            await middleware({"type": "http", "path": path, "method": "GET"}, None, None)
    asyncio.run(scenario())
    assert rec._LAST_ACTIVITY == before
    assert rec.recovery_status()["last_activity_category"] == "startup"


def test_monitor_first_check_and_retry_due_are_bounded_and_cancel_clear(monkeypatch, idle):
    sleeps = []
    cycles = []
    async def sleep(delay):
        sleeps.append(delay)
        due = rec.recovery_status()["next_check_due_seconds"]
        assert 59 <= due <= 60
        if len(sleeps) == 2:
            raise asyncio.CancelledError
    async def to_thread(fn):
        return fn()
    monkeypatch.setattr(rec.asyncio, "sleep", sleep)
    monkeypatch.setattr(rec.asyncio, "to_thread", to_thread)
    def run():
        cycles.append(True)
        return rec.record_skipped_cycle("recent-visitor-activity")
    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await rec.monitor_idle_memory_recovery(run_cycle=run)
    asyncio.run(scenario())
    assert sleeps == [60, 60] and cycles == [True]
    assert rec.recovery_status()["next_check_due_seconds"] is None
    assert rec.recovery_status()["last_check"]["reason"] == "recent-visitor-activity"
    assert idle == []


def test_monitor_html_uses_actual_interval_and_last_check_not_fixed_15_minutes():
    import inspect
    source = inspect.getsource(app.collection_monitor_page)
    assert "15분마다 확인" not in source
    assert 'idle_memory.get("check_interval_seconds")' in source
    assert 'idle_memory.get("last_check")' in source
    assert 'idle_memory.get("next_check_due_seconds")' in source


def test_versioned_review_branch_keeps_real_256_mib_ci_enabled():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/g2b-vnext.yml").read_text()
    assert "startsWith(github.head_ref, 'fix/g2b-4.1.303-')" in workflow
    assert "github.event_name == 'pull_request'" in workflow
    assert "SINSUNG G2B VNEXT 4.1.306" in workflow


def test_browser_favicon_poll_does_not_starve_idle_reclaim(monkeypatch, idle):
    before = rec._LAST_ACTIVITY
    async def handler(scope, receive, send):
        # It is still an active request: never reclaim during its response.
        assert rec.recovery_status()['active_requests'] == 1
        assert cycle()['reason'] == 'requests-active'
    async def scenario():
        middleware = rec.ActivityMiddleware(handler)
        for method in ('GET', 'HEAD', 'GET'):
            await middleware({'type': 'http', 'path': '/favicon.ico', 'method': method}, None, None)
    asyncio.run(scenario())
    assert rec._LAST_ACTIVITY == before
    assert cycle()['status'] == 'completed'
    assert idle == ['gc']


def test_passive_paths_do_not_hide_mutation_activity(idle):
    before = rec._LAST_ACTIVITY
    async def handler(scope, receive, send):
        assert rec.recovery_status()['active_requests'] == 1
    async def scenario():
        middleware = rec.ActivityMiddleware(handler)
        await middleware({'type': 'http', 'path': '/favicon.ico', 'method': 'POST'}, None, None)
    asyncio.run(scenario())
    assert rec._LAST_ACTIVITY > before
    assert rec.recovery_status()['last_activity_category'] == 'mutation'
    assert cycle()['reason'] == 'recent-visitor-activity'
