import datetime as dt
import json

import pytest

import db
import vnext_http


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "http.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    return path


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def test_vnext_request_never_mutates_legacy_quota_or_last_result(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    today = dt.date.today().isoformat()
    legacy = {
        "api_calls_bid_date": today,
        "api_calls_bid_count": "41",
        "api_calls_shop_date": today,
        "api_calls_shop_count": "17",
        "last_api_result_code": "LEGACY",
        "last_api_result_message": "KEEP-ME",
    }
    with db.connect() as conn:
        for key, value in legacy.items():
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    payload = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {"items": [{"bidNtceNo": "A"}], "totalCount": 1},
        }
    }
    monkeypatch.setattr(
        vnext_http.urllib.request,
        "urlopen",
        lambda req, timeout=45: _FakeResponse(json.dumps(payload).encode("utf-8")),
    )

    items, total = vnext_http.request("https://example.invalid/api", "bid_notice", retries=1)
    assert items == [{"bidNtceNo": "A"}]
    assert total == 1

    with db.connect() as conn:
        after = {
            key: conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()["value"]
            for key in legacy
        }
        vnext = {
            row["key"]: row["value"]
            for row in conn.execute(
                "SELECT key,value FROM app_settings WHERE key LIKE 'vnext_%' ORDER BY key"
            ).fetchall()
        }
    assert after == legacy
    assert vnext["vnext_api_calls_total"] == "1"
    assert vnext["vnext_api_calls_bid_notice_count"] == "1"
    assert vnext["vnext_last_api_result_code"] == "00"
    assert vnext["vnext_last_api_result_message"] == "OK"


def test_vnext_daily_limit_is_shared_across_request_kinds(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "2")

    assert vnext_http._quota_take("shopping") == (1, 2)
    assert vnext_http._quota_take("contract") == (2, 2)
    with pytest.raises(vnext_http.VNextQuotaReached):
        vnext_http._quota_take("bid_notice")

    assert vnext_http.api_usage("shopping")["total"] == 2
    assert vnext_http.api_usage("shopping")["kind_count"] == 1
    assert vnext_http.api_usage("contract")["kind_count"] == 1
    assert vnext_http.api_usage("bid_notice")["kind_count"] == 0


def test_vnext_parser_records_only_namespaced_error_state(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('last_api_result_code','LEGACY') "
            "ON CONFLICT(key) DO UPDATE SET value='LEGACY'"
        )

    payload = {
        "response": {
            "header": {"resultCode": "03", "resultMsg": "NO DATA"},
            "body": {"items": [], "totalCount": 0},
        }
    }
    with pytest.raises(vnext_http.VNextApiError) as exc:
        vnext_http.parse_response(json.dumps(payload).encode("utf-8"))
    assert exc.value.code == "03"

    with db.connect() as conn:
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key='last_api_result_code'"
        ).fetchone()["value"] == "LEGACY"
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key='vnext_last_api_result_code'"
        ).fetchone()["value"] == "03"


def test_parse_response_accepts_xml_single_item(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    raw = b"""<?xml version='1.0' encoding='UTF-8'?>
    <response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>
    <body><items><item><bidNtceNo>A</bidNtceNo><bidNtceNm>Test</bidNtceNm></item></items>
    <totalCount>1</totalCount></body></response>"""
    items, total = vnext_http.parse_response(raw)
    assert items == [{"bidNtceNo": "A", "bidNtceNm": "Test"}]
    assert total == 1
