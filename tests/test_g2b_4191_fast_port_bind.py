import asyncio
import threading
from types import SimpleNamespace

import main


def _http_scope(path="/collection-monitor", method="GET"):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "root_path": "",
    }


async def _call(app, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


def _status_body(messages):
    start = next(item for item in messages if item["type"] == "http.response.start")
    body = b"".join(
        item.get("body", b"")
        for item in messages
        if item["type"] == "http.response.body"
    )
    return int(start["status"]), body.decode("utf-8")


def test_lazy_runtime_returns_browser_warmup_while_import_is_blocked():
    started = threading.Event()
    release = threading.Event()

    def importer(name):
        assert name == "vnext_clean_app"
        started.set()
        release.wait(timeout=2)
        return SimpleNamespace(
            app=lambda scope, receive, send: None,
            schedule_backend_init=lambda: None,
        )

    app = main.LazyRuntimeApp(importer=importer)
    app.start_loader()
    assert started.wait(timeout=1)

    messages = asyncio.run(_call(app, _http_scope()))
    status, body = _status_body(messages)

    assert status == 200
    assert "G2B vNext 시작 중" in body
    assert "http-equiv='refresh' content='2'" in body
    release.set()


def test_lazy_runtime_live_is_immediate_and_ready_fails_closed():
    release = threading.Event()

    def importer(_name):
        release.wait(timeout=2)
        return SimpleNamespace(
            app=lambda scope, receive, send: None,
            schedule_backend_init=lambda: None,
        )

    app = main.LazyRuntimeApp(importer=importer)

    live_status, live_body = _status_body(
        asyncio.run(_call(app, _http_scope("/live")))
    )
    ready_status, ready_body = _status_body(
        asyncio.run(_call(app, _http_scope("/ready")))
    )

    assert live_status == 200
    assert "G2B_VNEXT_BOOTSTRAP_LOADING" in live_body
    assert ready_status == 503
    assert '"status":"not_ready"' in ready_body
    release.set()


def test_production_main_source_keeps_heavy_runtime_out_of_module_startup():
    source = __import__("pathlib").Path("main.py").read_text(encoding="utf-8")
    production = source.split('if _flag_on("G2B_TEST_MODE"):', 1)[1]
    assert "app = LazyRuntimeApp()" in production
    assert "app, BOOTSTRAP_IMPORT_ERROR = build_runtime()" in source
