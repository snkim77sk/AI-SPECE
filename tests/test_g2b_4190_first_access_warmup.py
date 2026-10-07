import asyncio

from starlette.requests import Request

import vnext_clean_app


def _request(path, method="GET"):
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
            "root_path": "",
        }
    )


def _warming_state():
    return {
        "initialized": False,
        "initializing": True,
        "backend_ok": False,
        "backend_error": "",
        "attempts": 1,
    }


def test_browser_get_gets_200_warmup_page_while_backend_initializes(monkeypatch):
    state = _warming_state()
    monkeypatch.setattr(vnext_clean_app, "backend_status", lambda: dict(state))
    monkeypatch.setattr(vnext_clean_app, "schedule_backend_init", lambda: False)

    async def forbidden(_request):
        raise AssertionError("call_next must not run while backend is warming")

    response = asyncio.run(
        vnext_clean_app.backend_gate(
            _request("/collection-monitor"),
            forbidden,
        )
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["retry-after"] == "2"
    assert "http-equiv='refresh' content='2'" in body
    assert "G2B vNext 시작 중" in body
    assert "PostgreSQL 저장소를 백그라운드에서 준비 중" in body


def test_api_get_still_fails_closed_while_backend_initializes(monkeypatch):
    state = _warming_state()
    monkeypatch.setattr(vnext_clean_app, "backend_status", lambda: dict(state))
    monkeypatch.setattr(vnext_clean_app, "schedule_backend_init", lambda: False)

    async def forbidden(_request):
        raise AssertionError("call_next must not run while backend is warming")

    response = asyncio.run(
        vnext_clean_app.backend_gate(
            _request("/api/collection-status"),
            forbidden,
        )
    )

    assert response.status_code == 503


def test_failed_backend_does_not_hide_failure_behind_warmup_page(monkeypatch):
    state = {
        "initialized": True,
        "initializing": False,
        "backend_ok": False,
        "backend_error": "RuntimeError: synthetic",
        "attempts": 2,
    }
    monkeypatch.setattr(vnext_clean_app, "backend_status", lambda: dict(state))
    monkeypatch.setattr(vnext_clean_app, "schedule_backend_init", lambda: False)

    async def forbidden(_request):
        raise AssertionError("call_next must not run after backend failure")

    response = asyncio.run(
        vnext_clean_app.backend_gate(
            _request("/collection-monitor"),
            forbidden,
        )
    )
    body = response.body.decode("utf-8")

    assert response.status_code == 503
    assert "저장소 초기화 실패" in body


def test_root_uses_same_fast_warmup_page(monkeypatch):
    state = _warming_state()
    monkeypatch.setattr(vnext_clean_app, "backend_status", lambda: dict(state))
    monkeypatch.setattr(vnext_clean_app, "schedule_backend_init", lambda: False)

    response = vnext_clean_app.root(_request("/"))
    body = response.body.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["retry-after"] == "2"
    assert "http-equiv='refresh' content='2'" in body
