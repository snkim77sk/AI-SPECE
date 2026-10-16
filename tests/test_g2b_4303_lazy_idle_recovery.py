"""The production lazy launcher owns idle recovery's async lifecycle.

Only fake runtimes are used: no database, source API or cgroup writes.
"""
import asyncio
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace


def _bootstrap(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    spec = importlib.util.spec_from_file_location(
        "g2b_lazy_recovery_test_main", Path(__file__).resolve().parents[1] / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "bridge_cafe24_budget_database_url", lambda: "")
    return module


def test_lazy_startup_does_not_wait_for_import_and_monitor_stops(monkeypatch):
    main = _bootstrap(monkeypatch)
    release = threading.Event()
    import_threads = []
    calls = []

    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()
        loop_thread = threading.get_ident()

        async def monitor(*, run_cycle):
            assert threading.get_ident() == loop_thread
            assert run_cycle is cycle
            calls.append("monitor")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        def cycle():
            raise AssertionError("the test must not run maintenance")

        runtime = SimpleNamespace(
            app=object(), TEST_MODE=False, is_unified=lambda: True,
            schedule_backend_init=lambda: calls.append("backend"),
            g2b_idle_memory_recovery=SimpleNamespace(monitor_idle_memory_recovery=monitor),
            _idle_memory_recovery_cycle=cycle,
        )

        def importer(_name):
            import_threads.append(threading.get_ident())
            assert release.wait(2)
            return runtime

        app = main.LazyRuntimeApp(importer=importer)
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()
        await incoming.put({"type": "lifespan.startup"})
        task = asyncio.create_task(app._lifespan(incoming.get, outgoing.put))
        try:
            message = await asyncio.wait_for(outgoing.get(), 1)
            assert message["type"] == "lifespan.startup.complete"
            assert not app.runtime_ready
            assert not started.is_set()
            # The warmup /live remains responsive while import is blocked.
            sent = []

            async def send(item):
                sent.append(item)

            await app._bootstrap_http({"path": "/live", "method": "GET"}, None, send)
            assert sent[0]["status"] == 200
            release.set()
            await asyncio.wait_for(started.wait(), 1)
            assert app.runtime_ready
            assert app.start_loader() is False
            assert import_threads == [app._thread.ident]
            assert import_threads[0] != loop_thread
            assert calls == ["backend", "monitor"]
            await incoming.put({"type": "lifespan.shutdown"})
            message = await asyncio.wait_for(outgoing.get(), 1)
            assert message["type"] == "lifespan.shutdown.complete"
            assert stopped.is_set()
            await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            app._thread.join(timeout=1)

    asyncio.run(scenario())


def test_shutdown_before_import_never_starts_monitor(monkeypatch):
    main = _bootstrap(monkeypatch)
    calls = []

    async def scenario():
        app = main.LazyRuntimeApp()
        monkeypatch.setattr(app, "start_loader", lambda: False)
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message["type"])

        await incoming.put({"type": "lifespan.startup"})
        await incoming.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(app._lifespan(incoming.get, send), 1)
        assert outgoing == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        app._runtime_module = SimpleNamespace(TEST_MODE=False, is_unified=lambda: calls.append(1))
        await asyncio.sleep(0)
        assert calls == []

    asyncio.run(scenario())


def test_test_mode_and_result_server_never_start_monitor(monkeypatch):
    main = _bootstrap(monkeypatch)

    async def scenario():
        for test_mode, unified in ((True, True), (False, False)):
            app = main.LazyRuntimeApp()
            app._runtime_module = SimpleNamespace(
                TEST_MODE=test_mode, is_unified=lambda: unified,
            )
            await app._monitor_runtime_idle_memory()

    asyncio.run(scenario())
