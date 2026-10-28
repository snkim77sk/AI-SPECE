"""A slow idle recovery must not block HTTP liveness or leak request counts."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest


def _recovery(monkeypatch):
    # Isolate lifecycle synchronization from all OS memory probes and cgroups.
    monkeypatch.setitem(sys.modules, "memory_guard", SimpleNamespace())
    spec = importlib.util.spec_from_file_location(
        "g2b_idle_liveness_test_module",
        Path(__file__).resolve().parents[1] / "g2b_idle_memory_recovery.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path,method", [("/dashboard", "GET"), ("/", "GET"), ("/", "HEAD")])
def test_waiting_request_keeps_live_responsive_and_counts_after_release(monkeypatch, path, method):
    rec = _recovery(monkeypatch)
    held = threading.Event()
    release = threading.Event()

    def slow_recovery():
        with rec._LOCK:
            held.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=slow_recovery)
    thread.start()
    assert held.wait(timeout=1)
    calls = []

    async def endpoint(scope, receive, send):
        calls.append((scope["path"], rec._ACTIVE_REQUESTS))

    async def scenario():
        middleware = rec.ActivityMiddleware(endpoint)
        request = asyncio.create_task(middleware(
            {"type": "http", "path": path, "method": method}, None, None
        ))
        try:
            await asyncio.sleep(0.03)
            assert not request.done()
            assert calls == []
            assert rec._ACTIVE_REQUESTS == 0
            await asyncio.wait_for(middleware(
                {"type": "http", "path": "/live"}, None, None
            ), timeout=0.2)
            assert calls == [("/live", 0)]
            release.set()
            await asyncio.wait_for(request, timeout=1)
            assert calls == [("/live", 0), (path, 1)]
            assert rec._ACTIVE_REQUESTS == 0
        finally:
            release.set()
            if not request.done():
                request.cancel()
                try:
                    await request
                except asyncio.CancelledError:
                    pass

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        thread.join(timeout=1)


@pytest.mark.parametrize("path,method", [("/dashboard", "GET"), ("/", "GET"), ("/", "HEAD")])
def test_cancelled_waiter_never_runs_handler_or_leaks_count(monkeypatch, path, method):
    rec = _recovery(monkeypatch)
    held = threading.Event()
    release = threading.Event()

    def slow_recovery():
        with rec._LOCK:
            held.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=slow_recovery)
    thread.start()
    assert held.wait(timeout=1)

    async def endpoint(*_args):
        raise AssertionError("cancelled waiter entered handler")

    async def scenario():
        request = asyncio.create_task(rec.ActivityMiddleware(endpoint)(
            {"type": "http", "path": path, "method": method}, None, None
        ))
        await asyncio.sleep(0.03)
        assert not request.done()
        request.cancel()
        try:
            await request
        except asyncio.CancelledError:
            pass
        assert rec._ACTIVE_REQUESTS == 0

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        thread.join(timeout=1)
    assert rec._ACTIVE_REQUESTS == 0
