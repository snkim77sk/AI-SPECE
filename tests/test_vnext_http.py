import datetime as dt
import io
import json
import urllib.error

import pytest

import db
import vnext_http


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "http.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    return path


class _FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self): return self.payload


def test_vnext_request_never_mutates_legacy_quota_or_last_result(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    today = dt.date.today().isoformat()
    legacy = {"api_calls_bid_date": today, "api_calls_bid_count": "41",
              "api_calls_shop_date": today, "api_calls_shop_count": "17",
              "last_api_result_code": "LEGACY", "last_api_result_message": "KEEP-ME"}
    with db.connect() as conn:
        for key, value in legacy.items():
            conn.execute("INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key,value))
    payload = {"response":{"header":{"resultCode":"00","resultMsg":"OK"},
                           "body":{"items":[{"bidNtceNo":"A"}],"totalCount":1}}}
    monkeypatch.setattr(vnext_http.urllib.request, "urlopen",
                        lambda req, timeout=45: _FakeResponse(json.dumps(payload).encode()))
    items,total = vnext_http.request("https://example.invalid/api", "bid_notice", retries=1)
    assert items == [{"bidNtceNo":"A"}] and total == 1
    with db.connect() as conn:
        after = {key: conn.execute("SELECT value FROM app_settings WHERE key=?",(key,)).fetchone()["value"] for key in legacy}
    assert after == legacy


def test_vnext_daily_limit_is_shared_across_request_kinds(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "2")
    assert vnext_http._quota_take("shopping") == (1,2)
    assert vnext_http._quota_take("contract") == (2,2)
    with pytest.raises(vnext_http.VNextQuotaReached):
        vnext_http._quota_take("bid_notice")


def test_missing_total_remains_unknown(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    payload = {"response":{"header":{"resultCode":"00","resultMsg":"OK"},
                           "body":{"items":[{"id":1},{"id":2}]}}}
    items,total = vnext_http.parse_response(json.dumps(payload).encode())
    assert len(items) == 2
    assert total is None


def test_unrecognized_json_and_html_never_become_zero_rows(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with pytest.raises(vnext_http.VNextResponseError):
        vnext_http.parse_response(json.dumps({"error":"temporary failure"}).encode())
    with pytest.raises(vnext_http.VNextResponseError):
        vnext_http.parse_response(b"<html><body>Service unavailable</body></html>")


def test_http_503_success_like_body_is_never_returned_as_success(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    payload = json.dumps({"response":{"header":{"resultCode":"00","resultMsg":"OK"},
                                      "body":{"items":[{"id":1}],"totalCount":1}}}).encode()
    def fail(req, timeout=45):
        raise urllib.error.HTTPError("https://example.invalid",503,"down",{},io.BytesIO(payload))
    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fail)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        vnext_http.request("https://example.invalid", "bid_notice", retries=1)


def test_vnext_parser_records_only_namespaced_error_state(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with db.connect() as conn:
        conn.execute("INSERT INTO app_settings(key,value) VALUES('last_api_result_code','LEGACY')")
    payload = {"response":{"header":{"resultCode":"03","resultMsg":"NO DATA"},
                           "body":{"items":[],"totalCount":0}}}
    with pytest.raises(vnext_http.VNextApiError):
        vnext_http.parse_response(json.dumps(payload).encode())
    with db.connect() as conn:
        assert conn.execute("SELECT value FROM app_settings WHERE key='last_api_result_code'").fetchone()["value"] == "LEGACY"
        assert conn.execute("SELECT value FROM app_settings WHERE key='vnext_last_api_result_code'").fetchone()["value"] == "03"


def test_parse_response_accepts_xml_single_item(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    raw = b"<?xml version='1.0'?><response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header><body><items><item><bidNtceNo>A</bidNtceNo></item></items><totalCount>1</totalCount></body></response>"
    items,total = vnext_http.parse_response(raw)
    assert items == [{"bidNtceNo":"A"}]
    assert total == 1
