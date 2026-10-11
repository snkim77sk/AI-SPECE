"""4.1.251: bounded menu admission and safe pre-route PG session failures."""

import asyncio
import json
import threading

from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.exc import TimeoutError as SAPoolTimeoutError
from starlette.requests import Request
from fastapi.responses import HTMLResponse

import vnext_clean_app as app


def _request(path, *, method="GET"):
    return Request({
        "type": "http", "method": method, "path": path,
        "query_string": b"", "headers": [],
    })


def _production_web(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(app, "db_is_persistent", lambda: True)
    monkeypatch.setattr(app, "_WEB_MENU_READ_SLOTS", threading.BoundedSemaphore(2))


def test_known_get_menu_paths_limited_not_health_login_or_writes():
    expected = (
        "/dashboard", "/budget", "/shopping", "/vendors", "/settings",
        "/collection-monitor", "/budget/project", "/budget/export.xlsx",
        "/api/dashboard-summary", "/api/status",
        "/api/collection-status", "/api/shopping", "/api/vendors", "/api/budget",
    )
    for path in expected:
        assert app._is_bounded_web_menu_read(_request(path))
    for path in ("/live", "/health", "/ready", "/", "/login", "/setup", "/logout"):
        assert not app._is_bounded_web_menu_read(_request(path))
    assert not app._is_bounded_web_menu_read(_request("/settings", method="POST"))
    assert not app._is_bounded_web_menu_read(_request("/api/result-sync", method="POST"))
    assert not app._is_bounded_web_menu_read(_request("/api/unknown"))


def test_concurrent_menu_requests_reject_fast_without_starving_health(monkeypatch, capsys):
    _production_web(monkeypatch)

    async def scenario():
        release = asyncio.Event()
        entered = asyncio.Event()
        active = 0

        async def slow_next(_request):
            nonlocal active
            active += 1
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return HTMLResponse("ok")
            finally:
                active -= 1

        tasks = [
            asyncio.create_task(app.backend_gate(_request("/budget"), slow_next)),
            asyncio.create_task(app.backend_gate(_request("/shopping"), slow_next)),
        ]
        await asyncio.wait_for(entered.wait(), 2)

        async def forbidden(_request):
            raise AssertionError("busy route must not enter application or DB")

        third = await asyncio.wait_for(
            app.backend_gate(_request("/vendors"), forbidden), 1
        )
        assert third.status_code == 503
        assert third.headers["retry-after"] == "2"
        assert "조회가 잠시 지연" in third.body.decode("utf-8")
        assert "content-security-policy" in third.headers

        api = await asyncio.wait_for(
            app.backend_gate(_request("/api/status"), forbidden), 1
        )
        assert api.status_code == 503
        assert json.loads(api.body) == {
            "ok": False, "error": "WEB_DB_BUSY_RETRY"
        }

        # /live is not blocked even when both menu slots are occupied.
        async def live_next(_request):
            return HTMLResponse("live")
        live = await asyncio.wait_for(
            app.backend_gate(_request("/live"), live_next), 1
        )
        assert live.status_code == 200

        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert all(result.status_code == 200 for result in results)
        retry = await app.backend_gate(_request("/settings"), live_next)
        assert retry.status_code == 200

    asyncio.run(scenario())
    assert "G2B_WEB_MENU_POOL_BUSY /vendors" in capsys.readouterr().out


def test_read_slot_released_if_request_raises(monkeypatch):
    _production_web(monkeypatch)

    async def fail(_request):
        raise RuntimeError("synthetic view error")

    async def ok(_request):
        return HTMLResponse("retry works")

    async def scenario():
        for _ in range(3):
            try:
                await app.backend_gate(_request("/budget"), fail)
            except RuntimeError:
                pass
            else:
                raise AssertionError("view exception must propagate")
        response = await app.backend_gate(_request("/budget"), ok)
        assert response.status_code == 200

    asyncio.run(scenario())


def test_sqlalchemy_pool_timeout_before_require_user_degrades_without_secret(monkeypatch, capsys):
    _production_web(monkeypatch)
    assert app.app.exception_handlers[SAPoolTimeoutError] is app._web_db_connection_exception_handler
    assert app.app.exception_handlers[SAOperationalError] is app._web_db_connection_exception_handler

    async def scenario():
        secret_error = SAPoolTimeoutError("password=SECRET_INTERNAL_DSN")
        api = await app._web_db_connection_exception_handler(
            _request("/api/budget"), secret_error
        )
        assert api.status_code == 503
        assert json.loads(api.body) == {
            "ok": False, "error": "WEB_DB_CONNECTION_UNAVAILABLE"
        }
        page = await app._web_db_connection_exception_handler(
            _request("/dashboard"), secret_error
        )
        assert page.status_code == 503
        assert "로그인" not in page.body.decode("utf-8")
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["retry-after"] == "2"
        assert b"SECRET_INTERNAL_DSN" not in api.body + page.body

    asyncio.run(scenario())
    logs = capsys.readouterr().out
    assert "G2B_WEB_DB_CONNECTION_UNAVAILABLE" in logs
    assert "SECRET_INTERNAL_DSN" not in logs


def test_early_session_lookup_failure_is_handled_without_forcing_logout(monkeypatch):
    _production_web(monkeypatch)
    monkeypatch.setattr(
        app, "session_user",
        lambda _token: (_ for _ in ()).throw(
            SAPoolTimeoutError("synthetic pool saturation")
        ),
    )
    req = _request("/budget")
    req.scope["headers"] = [(b"cookie", b"g2b_vnext_session=VALID_TOKEN")]
    try:
        app.require_user(req)
    except SAPoolTimeoutError:
        pass
    else:
        raise AssertionError("session DB failure must not be interpreted as logged out")
    response = asyncio.run(
        app._web_db_connection_exception_handler(
            req, SAPoolTimeoutError("synthetic pool saturation")
        )
    )
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert "location" not in response.headers
