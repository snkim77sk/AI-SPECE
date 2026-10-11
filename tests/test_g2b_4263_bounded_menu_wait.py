"""4.1.263 bounded async menu admission without increasing PostgreSQL concurrency."""

import asyncio
import threading
import time

import pytest
from fastapi.responses import HTMLResponse
from starlette.requests import Request
import vnext_clean_app as app


def _request(path):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "headers": [], "query_string": b"",
    })


def _prod(monkeypatch):
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(app, "db_is_persistent", lambda: True)
    monkeypatch.setattr(app, "_WEB_MENU_READ_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(app, "_WEB_MENU_READ_WAITERS", threading.BoundedSemaphore(4))
    monkeypatch.setattr(app, "_WEB_MENU_READ_WAIT_SECONDS", 0.6)
    monkeypatch.setattr(app, "_WEB_MENU_READ_WAIT_POLL_SECONDS", 0.01)


def test_short_interactive_wait_succeeds_but_api_fails_fast_and_live_survives(monkeypatch):
    _prod(monkeypatch)

    async def scenario():
        release = asyncio.Event()
        entered = asyncio.Event()
        active = 0
        peak = 0

        async def slow(_request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return HTMLResponse("first")
            finally:
                active -= 1

        first = [
            asyncio.create_task(app.backend_gate(_request(path), slow))
            for path in ("/budget", "/shopping")
        ]
        await asyncio.wait_for(entered.wait(), 2)

        async def quick(_request):
            return HTMLResponse("next")

        navigation = asyncio.create_task(app.backend_gate(_request("/settings"), quick))
        await asyncio.sleep(0.05)
        assert not navigation.done(), "menu was rejected rather than briefly queued"
        assert (await app.backend_gate(_request("/api/status"), quick)).status_code == 503
        assert (await app.backend_gate(_request("/live"), quick)).status_code == 200
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*first, navigation), 2)
        assert [response.status_code for response in results] == [200, 200, 200]
        assert peak == 2

    asyncio.run(scenario())


def test_four_waiters_are_bounded_and_fifth_fails_fast(monkeypatch):
    _prod(monkeypatch)

    async def scenario():
        release = asyncio.Event()
        entered = asyncio.Event()
        count = 0

        async def slow(_request):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            await release.wait()
            return HTMLResponse("ok")

        occupied = [
            asyncio.create_task(app.backend_gate(_request(path), slow))
            for path in ("/budget", "/shopping")
        ]
        await asyncio.wait_for(entered.wait(), 2)

        async def quick(_request):
            return HTMLResponse("ok")

        queued = [
            asyncio.create_task(app.backend_gate(_request(path), quick))
            for path in ("/dashboard", "/vendors", "/settings", "/collection-monitor")
        ]
        await asyncio.sleep(0.05)
        assert not any(task.done() for task in queued)
        begin = time.monotonic()
        rejected = await app.backend_gate(_request("/budget/project"), quick)
        assert rejected.status_code == 503
        assert time.monotonic() - begin < 0.2
        assert (await app.backend_gate(_request("/health"), quick)).status_code == 200
        release.set()
        done = await asyncio.wait_for(asyncio.gather(*occupied, *queued), 2)
        assert all(response.status_code == 200 for response in done)
        assert app._WEB_MENU_READ_WAITERS.acquire(blocking=False)
        app._WEB_MENU_READ_WAITERS.release()

    asyncio.run(scenario())


def test_interactive_timeout_returns_503_and_does_not_leak_db_slot(monkeypatch):
    _prod(monkeypatch)
    monkeypatch.setattr(app, "_WEB_MENU_READ_WAIT_SECONDS", 0.07)

    async def scenario():
        assert app._WEB_MENU_READ_SLOTS.acquire(blocking=False)
        assert app._WEB_MENU_READ_SLOTS.acquire(blocking=False)
        try:
            started = time.monotonic()
            assert await app._acquire_web_menu_read_slot(_request("/settings")) is False
            assert 0.055 <= time.monotonic() - started < 0.25
        finally:
            app._WEB_MENU_READ_SLOTS.release()
            app._WEB_MENU_READ_SLOTS.release()
        assert await app._acquire_web_menu_read_slot(_request("/settings")) is True
        app._WEB_MENU_READ_SLOTS.release()

    asyncio.run(scenario())


def test_cancelled_navigation_waiter_releases_queue_capacity(monkeypatch):
    _prod(monkeypatch)

    async def scenario():
        assert app._WEB_MENU_READ_SLOTS.acquire(blocking=False)
        assert app._WEB_MENU_READ_SLOTS.acquire(blocking=False)
        try:
            task = asyncio.create_task(app._acquire_web_menu_read_slot(_request("/settings")))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert all(app._WEB_MENU_READ_WAITERS.acquire(blocking=False) for _ in range(4))
            for _ in range(4):
                app._WEB_MENU_READ_WAITERS.release()
        finally:
            app._WEB_MENU_READ_SLOTS.release()
            app._WEB_MENU_READ_SLOTS.release()
        assert await app._acquire_web_menu_read_slot(_request("/budget")) is True
        app._WEB_MENU_READ_SLOTS.release()

    asyncio.run(scenario())


@pytest.mark.parametrize("path", [
    "/api/status", "/api/budget", "/api/shopping", "/api/vendors",
    "/api/dashboard-summary", "/budget/export.xlsx",
])
def test_api_polling_and_export_do_not_wait_for_menu_slot(monkeypatch, path):
    _prod(monkeypatch)

    async def scenario():
        assert app._WEB_MENU_READ_SLOTS.acquire(blocking=False)
        assert app._WEB_MENU_READ_SLOTS.acquire(blocking=False)
        try:
            begin = time.monotonic()
            assert await app._acquire_web_menu_read_slot(_request(path)) is False
            assert time.monotonic() - begin < 0.1
        finally:
            app._WEB_MENU_READ_SLOTS.release()
            app._WEB_MENU_READ_SLOTS.release()

    asyncio.run(scenario())
