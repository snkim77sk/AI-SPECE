"""G2B 4.1.244: bounded shopping GET/JSON read paths with safe failure UI."""
import json
from contextlib import contextmanager

from starlette.requests import Request

import procurement_read_vnext
import vnext_clean_app


def _req(path, query=b"category=LIGHTING"):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "query_string": query, "headers": [],
    })


def test_production_shopping_http_timeout_applies_transaction_local_sql(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(procurement_read_vnext, "backend_name", lambda: "POSTGRESQL")
    calls = []

    class Connection:
        def execute(self, sql, params=()):
            calls.append((str(sql), tuple(params)))
            if "SELECT set_config" in str(sql):
                return object()
            return type("Result", (), {
                "fetchall": lambda _: [{
                    "source_key": "A",
                    "primary_category": "LIGHTING",
                    "source_date": "2026-10-01",
                    "demand_region": "인천광역시",
                    "quantity": 2,
                    "amount": 5000,
                    "unit_price": 2500,
                }],
            })()

    @contextmanager
    def fake_connect():
        yield Connection()

    monkeypatch.setattr(procurement_read_vnext, "connect", fake_connect)

    rows = procurement_read_vnext.shopping_rows(
        categories=("LIGHTING",),
        query="LED",
        limit=10,
        web_timeout_ms=3500,
    )
    assert len(rows) == 1 and rows[0]["source_key"] == "A"
    assert len(calls) == 2
    assert "set_config('statement_timeout'" in calls[0][0]
    assert "set_config('work_mem', '4MB', true)" in calls[0][0]
    assert calls[0][1] == ("3500ms",)
    assert "LIMIT ? OFFSET ?" in calls[1][0]
    assert calls[1][1][-2:] == (10, 0)


def test_shopping_page_timeout_is_informative_not_zero_data_or_500(monkeypatch, capsys):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "operator"})
    monkeypatch.setattr(app, "is_result_server", lambda: False)

    def failed(**_kwargs):
        raise TimeoutError("secret db details")

    monkeypatch.setattr(procurement_read_vnext, "shopping_rows", failed)
    response = app.shopping_page(_req("/shopping"))
    body = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "기존 납품자료·수집 이력은 보존" in body
    assert "자료 조회 대기" in body
    assert "현재 조건의 조달내역 없음" not in body
    assert "secret db details" not in body
    printed = capsys.readouterr().out
    assert "G2B_SHOPPING_READ_DEGRADED TimeoutError" in printed
    assert "G2B_SHOPPING_RENDER_MS" in printed
    assert "G2B_SHOPPING_READ_MS" in printed
    assert "secret db details" not in printed


def test_api_shopping_timeout_returns_503_without_internal_details(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "operator"})
    monkeypatch.setattr(app, "is_result_server", lambda: False)

    def failed(**kwargs):
        assert kwargs["web_timeout_ms"] == 3500
        raise TimeoutError("password=never-print")

    monkeypatch.setattr(procurement_read_vnext, "shopping_rows", failed)
    response = app.api_shopping(_req("/api/shopping"))
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "ok": False, "error": "SHOPPING_READ_TEMPORARILY_UNAVAILABLE"
    }
    assert b"never-print" not in response.body


def test_shopping_page_blocks_heavy_read_when_cgroup_is_tight(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "operator"})
    monkeypatch.setattr(app.memory_guard, "low_memory_web_hold", lambda: True)
    monkeypatch.setattr(app.memory_guard, "snapshot", lambda **kw: {
        "guard_ok": True,
        "rss_mib": 148,
        "cgroup_limit_mib": 256,
        "cgroup_effective_mib": 201,
    })
    monkeypatch.setattr(procurement_read_vnext, "shopping_rows", lambda **kw: (
        (_ for _ in ()).throw(AssertionError("no DB read when under pressure"))
    ))
    response = app.shopping_page(_req("/shopping"))
    assert response.status_code == 200
    assert "조달내역 조회 잠시 대기" in response.body.decode("utf-8")


def test_shopping_regular_result_keeps_date_filter_and_display(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "operator"})
    monkeypatch.setattr(app, "is_result_server", lambda: False)

    calls = []
    def successful(**kw):
        calls.append(kw)
        return [{
            "source_date": "2026-10-01",
            "demand_region": "인천광역시",
            "demand_org": "수요기관",
            "detail_item_no": "3911160302",
            "detail_item_name": "LED 조명",
            "item_id": "01234567",
            "item_name": "가로등기구",
            "model_name": "LS-1",
            "vendor_name": "라이팅업체",
            "quantity": 2,
            "unit_price": 5000,
            "amount": 10000,
        }]
    monkeypatch.setattr(procurement_read_vnext, "shopping_rows", successful)
    response = app.shopping_page(_req("/shopping", b"category=POLE&start_date=2026-01-01&end_date=2026-12-31"))
    assert response.status_code == 200
    assert "라이팅업체" in response.body.decode("utf-8")
    assert "10,000" in response.body.decode("utf-8")
    assert calls[0]["categories"] == ("POLE",)
    assert calls[0]["start_date"] == "2026-01-01"
    assert calls[0]["end_date"] == "2026-12-31"
