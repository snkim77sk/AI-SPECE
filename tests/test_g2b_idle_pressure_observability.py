"""Source-free pressure rechecks: visibility without relaxed collection guards."""
import asyncio
import time
from types import SimpleNamespace

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
    probes = ("/health", "/live", "/ready", "/__ai_space_health")
    async def handler(scope, receive, send):
        expected = 0 if scope["path"] in probes else 1
        assert rec.recovery_status()["active_requests"] == expected
    async def scenario():
        middleware = rec.ActivityMiddleware(handler)
        for path in (*probes, "/", "/collection-monitor", "/api/collection-status", "/favicon.ico"):
            for method in ("GET", "HEAD"):
                await middleware({"type": "http", "path": path, "method": method}, None, None)
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
    assert "SINSUNG G2B VNEXT 4.1.307" in workflow


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


@pytest.fixture
def probe_clock(monkeypatch, idle):
    # Keep asyncio's real clock intact; only the recovery module sees this time.
    clock = SimpleNamespace(now=100000.0)
    monkeypatch.setattr(rec, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(rec, "_START_MONOTONIC", clock.now)
    monkeypatch.setattr(rec, "_LAST_ACTIVITY", clock.now)
    monkeypatch.setattr(rec, "_trim_allocator", lambda: idle.append("trim") or True)
    return clock


@pytest.mark.parametrize("interval", [20, 30])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_repeated_root_probes_allow_idle_pressure_reclaim(probe_clock, idle, interval, method):
    before = rec._LAST_ACTIVITY
    assert rec.PRESSURE_IDLE_SECONDS == 45

    async def handler(scope, receive, send):
        assert rec.recovery_status()["active_requests"] == 1
        assert cycle()["reason"] == "requests-active"

    async def scenario():
        middleware = rec.ActivityMiddleware(handler)
        for tick in range(1, 7):
            probe_clock.now = before + interval * tick
            await middleware({"type": "http", "path": "/", "method": method}, None, None)
            assert rec.recovery_status()["active_requests"] == 0
            if interval * tick < 45:
                assert cycle()["reason"] == "recent-visitor-activity"
            elif rec.recovery_status()["attempts"] == 0:
                # Regression: main kept returning recent-visitor-activity here.
                assert cycle()["status"] == "completed"
            else:
                assert cycle()["reason"] == "cooldown"
        assert rec._LAST_ACTIVITY == before
        assert rec.recovery_status()["last_activity_category"] == "startup"

    asyncio.run(scenario())
    assert rec.recovery_status()["attempts"] == 1
    assert idle == ["gc", "trim"]


@pytest.mark.parametrize("path,method,category", [
    ("/", method, "mutation") for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS")
] + [
    (path, "POST", "mutation")
    for path in ("/login", "/collection-monitor", "/api/collection-status", "/favicon.ico")
] + [
    ("/login", "GET", "login"),
    ("/dashboard", "GET", "dashboard"),
    *[(path, "GET", "other-read") for path in (
        "/shopping", "/budget", "/smartled", "/budget/export.xlsx", "/logout", "/unknown",
    )],
])
def test_root_passive_classification_preserves_other_request_activity(probe_clock, idle, path, method, category):
    probe_clock.now += 60

    async def handler(scope, receive, send):
        assert rec.recovery_status()["active_requests"] == 1
        assert cycle()["reason"] == "requests-active"

    asyncio.run(rec.ActivityMiddleware(handler)(
        {"type": "http", "path": path, "method": method}, None, None,
    ))
    assert rec._LAST_ACTIVITY == probe_clock.now
    assert rec.recovery_status()["active_requests"] == 0
    assert rec.recovery_status()["last_activity_category"] == category
    assert cycle()["reason"] == "recent-visitor-activity"
    assert idle == []


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("failure", ["exception", "cancellation"])
def test_root_handler_failure_restores_count_without_resetting_idle(probe_clock, idle, method, failure):
    before = rec._LAST_ACTIVITY
    probe_clock.now += 60

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(scope, receive, send):
            assert rec.recovery_status()["active_requests"] == 1
            assert cycle()["reason"] == "requests-active"
            entered.set()
            await release.wait()
            raise RuntimeError("synthetic root response failure")

        request = asyncio.create_task(rec.ActivityMiddleware(handler)(
            {"type": "http", "path": "/", "method": method}, None, None,
        ))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert idle == []
            if failure == "cancellation":
                request.cancel()
                error = asyncio.CancelledError
            else:
                release.set()
                error = RuntimeError
            with pytest.raises(error):
                await asyncio.wait_for(request, timeout=1)
        finally:
            if not request.done():
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request

    asyncio.run(scenario())
    assert rec.recovery_status()["active_requests"] == 0
    assert rec._LAST_ACTIVITY == before
    assert cycle()["status"] == "completed"
    assert idle == ["gc", "trim"]


async def _http_response(path="/", method="GET"):
    """Exercise the installed ASGI stack without sockets or lifespan startup."""
    messages = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    await asyncio.wait_for(app.app({
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": [], "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }, receive, send), timeout=2)
    return next(item for item in messages if item["type"] == "http.response.start")


def test_actual_root_redirect_is_db_free_and_followed_login_is_activity(monkeypatch, probe_clock, idle):
    before = rec._LAST_ACTIVITY
    probe_clock.now += 60
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(app, "_BACKEND_STATE", {"backend_ok": True, "initializing": False})

    def forbidden(*args, **kwargs):
        raise AssertionError("root must not access DB/session or initialize ready backend")

    for name in ("connect", "users_empty", "session_user", "schedule_backend_init"):
        monkeypatch.setattr(app, name, forbidden)

    async def scenario():
        response = await _http_response()
        assert response["status"] == 302
        assert dict(response["headers"])[b"location"] == b"/login"
        assert rec._LAST_ACTIVITY == before
        assert rec.recovery_status()["active_requests"] == 0
        # Preserve the existing FastAPI GET-only route; do not add HEAD behavior.
        assert (await _http_response(method="HEAD"))["status"] == 405
        assert rec._LAST_ACTIVITY == before

        reads = []

        def users_empty():
            assert rec.recovery_status()["active_requests"] == 1
            assert cycle()["reason"] == "requests-active"
            reads.append("login")
            return False

        monkeypatch.setattr(app, "users_empty", users_empty)
        monkeypatch.setattr(app, "session_user", lambda token: None)
        probe_clock.now += 1
        # Follow the actual redirect target as a new request, as a browser does.
        target = dict(response["headers"])[b"location"].decode()
        assert (await _http_response(target))["status"] == 200
        assert reads == ["login"]
        assert rec._LAST_ACTIVITY == probe_clock.now
        assert rec.recovery_status()["last_activity_category"] == "login"

    asyncio.run(scenario())
    assert rec.recovery_status()["active_requests"] == 0
    assert cycle()["reason"] == "recent-visitor-activity"
    assert idle == []


def test_actual_root_warmup_keeps_backend_guard_closed(monkeypatch, probe_clock, idle):
    before = rec._LAST_ACTIVITY
    probe_clock.now += 60
    monkeypatch.setattr(app, "_BACKEND_STATE", {"backend_ok": False, "initializing": True})
    scheduled = []

    def schedule():
        assert rec.recovery_status()["active_requests"] == 1
        scheduled.append(True)
        return False

    monkeypatch.setattr(app, "schedule_backend_init", schedule)
    response = asyncio.run(_http_response())
    assert response["status"] == 200
    assert dict(response["headers"])[b"retry-after"] == b"2"
    assert scheduled == [True]
    assert rec.recovery_status()["active_requests"] == 0
    assert rec._LAST_ACTIVITY == before
    assert app._idle_memory_recovery_cycle()["reason"] == "backend-not-ready"
    assert idle == []
