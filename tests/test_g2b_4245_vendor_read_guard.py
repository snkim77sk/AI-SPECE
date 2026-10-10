"""G2B 4.1.245: safe vendor ranking scan under 256MiB web limits."""
import json
from contextlib import contextmanager

import pytest
from starlette.requests import Request

import memory_guard
import procurement_read_vnext
import vnext_clean_app


def req(path="/vendors", query=b""):
    return Request({
        "type": "http", "method": "GET", "path": path,
        "query_string": query, "headers": [],
    })


def _row(index):
    return {
        "source_key": f"V-{index}",
        "source_date": "2026-10-01",
        "fetched_at": "2026-10-01T00:00:00Z",
        "primary_category": "LIGHTING",
        "delivery_req_no": f"R-{index}",
        "detail_seq": "1",
        "delivery_change_order": "0",
        "is_final_delivery_request": "Y",
        "demand_org": "정상기관",
        "vendor_name": "기존납품업체",
        "vendor_bizno": "123-45-67890",
        "amount": 100,
        "delivery_req_total_amount": 100,
    }


def test_vendor_stream_web_uses_transaction_local_statement_limit(monkeypatch):
    monkeypatch.setattr(memory_guard, "snapshot", lambda **_: {
        "guard_ok": True, "rss_mib": 85.0,
        "cgroup_limit_mib": 256, "cgroup_effective_mib": 100,
    })
    sql_calls = []

    class Cursor:
        def __init__(self): self.count = 0
        def fetchmany(self, size):
            assert size == 250
            self.count += 1
            return [_row(0)] if self.count == 1 else []

    class Conn:
        def execute(self, sql, params=()):
            sql_calls.append((str(sql), tuple(params)))
        def execute_streaming(self, sql, params=(), *, max_row_buffer=250):
            assert max_row_buffer == 250
            sql_calls.append((str(sql), tuple(params)))
            return Cursor()

    @contextmanager
    def connect():
        yield Conn()

    monkeypatch.setattr(procurement_read_vnext, "connect", connect)
    rows = list(procurement_read_vnext._iter_latest_normalized_vendor_rows(
        web_timeout_ms=10000
    ))
    assert len(rows) == 1
    assert len(sql_calls) == 2
    assert "set_config('statement_timeout'" in sql_calls[0][0]
    assert "set_config('work_mem', '4MB', true)" in sql_calls[0][0]
    assert sql_calls[0][1] == ("5000ms",)
    assert "ORDER BY delivery_req_no,detail_seq,source_key" in sql_calls[1][0]


def test_vendor_web_scan_aborts_over_deadline_not_partial_results(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(procurement_read_vnext.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(memory_guard, "snapshot", lambda **_: {
        "guard_ok": True, "rss_mib": 85,
    })
    class Cursor:
        def fetchmany(self, size):
            clock[0] += 6
            return [_row(0)]

    class Conn:
        def execute(self, *_args, **_kwargs):
            pass
        def execute_streaming(self, *_args, **_kwargs):
            return Cursor()

    @contextmanager
    def connect():
        yield Conn()
    monkeypatch.setattr(procurement_read_vnext, "connect", connect)
    with pytest.raises(TimeoutError, match="G2B_VENDOR_WEB_DEADLINE_EXCEEDED"):
        list(procurement_read_vnext._iter_latest_normalized_vendor_rows(
            web_timeout_ms=10000,
        ))


def test_vendor_web_scan_aborts_before_memory_oom(monkeypatch):
    monkeypatch.setattr(memory_guard, "snapshot", lambda **_: {
        "guard_ok": False, "rss_mib": 151,
        "cgroup_limit_mib": 256, "cgroup_effective_mib": 225,
    })
    class Cursor:
        def fetchmany(self, size):
            return [_row(0)]

    class Conn:
        def execute(self, *_args, **_kwargs):
            pass
        def execute_streaming(self, *_args, **_kwargs):
            return Cursor()

    @contextmanager
    def connect():
        yield Conn()
    monkeypatch.setattr(procurement_read_vnext, "connect", connect)
    with pytest.raises(MemoryError, match="G2B_VENDOR_WEB_MEMORY_HOLD"):
        list(procurement_read_vnext._iter_latest_normalized_vendor_rows(
            web_timeout_ms=10000,
        ))


def test_vendor_page_database_timeout_is_200_with_truthful_notice(monkeypatch, capsys):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "operator"})
    monkeypatch.setattr(app, "is_result_server", lambda: False)
    monkeypatch.setattr(procurement_read_vnext, "vendor_rows",
                        lambda **_: (_ for _ in ()).throw(TimeoutError("secret")))
    result = app.vendors_page(req())
    body = result.body.decode("utf-8")
    assert result.status_code == 200
    assert "업체 분석 조회 대기" in body
    assert "현재 조건의 업체 실적 없음" not in body
    assert "secret" not in body
    printed = capsys.readouterr().out
    assert "G2B_VENDORS_READ_DEGRADED TimeoutError" in printed
    assert "G2B_VENDORS_RENDER_MS" in printed


def test_vendor_api_database_timeout_is_safe_503(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", False)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "operator"})
    monkeypatch.setattr(app, "is_result_server", lambda: False)
    def failed(**kw):
        assert kw["web_timeout_ms"] == 10000
        raise TimeoutError("db password leaked")
    monkeypatch.setattr(procurement_read_vnext, "vendor_rows", failed)
    result = app.api_vendors(req("/api/vendors"))
    assert result.status_code == 503
    assert json.loads(result.body) == {
        "ok": False, "error": "VENDOR_READ_TEMPORARILY_UNAVAILABLE",
    }
    assert b"password" not in result.body


def test_vendor_snapshot_role_never_uses_postgres_when_missing(monkeypatch):
    app = vnext_clean_app
    monkeypatch.setattr(app, "TEST_MODE", True)
    monkeypatch.setattr(app, "require_user", lambda _: {"username": "operator"})
    monkeypatch.setattr(app, "is_result_server", lambda: True)
    monkeypatch.setattr(app.result_snapshot_vnext, "snapshot_available", lambda: False)
    monkeypatch.setattr(procurement_read_vnext, "vendor_rows",
        lambda **_: (_ for _ in ()).throw(AssertionError("snapshot only")))
    result = app.vendors_page(req())
    assert result.status_code == 200
    assert "스냅샷이 아직 준비되지 않았습니다" in result.body.decode("utf-8")
    api = app.api_vendors(req("/api/vendors"))
    assert api.status_code == 503
    assert json.loads(api.body)["error"] == "VENDOR_SNAPSHOT_NOT_READY"
