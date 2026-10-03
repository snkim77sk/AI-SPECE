import datetime as dt
import io
import json
import urllib.error
import urllib.parse

import pytest

import db
import lofin_vnext_http
import vnext_http
import vnext_live_gate
import vnext_source_guard


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "http.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    return path


def _shopping_url(page=1):
    params = {
        "serviceKey": "redacted",
        "pageNo": int(page),
        "numOfRows": 10,
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDate": "20260916",
        "inqryEndDate": "20260916",
    }
    return (
        "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/"
        "getDlvrReqDtlInfoList?" + urllib.parse.urlencode(params)
    )


def _bounded_context(monkeypatch, requests=4):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "a" * 40)
    monkeypatch.setattr(
        vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 17)
    )
    return vnext_source_guard.bounded_canary_source_context(
        validation_date="2026-09-16",
        max_requests=requests,
    )


class _FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self, *args): return self.payload


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
    with _bounded_context(monkeypatch):
        items,total = vnext_http.request(_shopping_url(), "shopping_delivery", retries=1)
    assert items == [{"bidNtceNo":"A"}] and total == 1
    with db.connect() as conn:
        after = {key: conn.execute("SELECT value FROM app_settings WHERE key=?",(key,)).fetchone()["value"] for key in legacy}
    assert after == legacy


def test_vnext_quota_accepts_only_shopping_aliases(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "2")
    monkeypatch.setattr(vnext_http, "_quota_today", lambda: "2026-10-03")

    assert vnext_http._quota_take("shopping") == (1, 2)
    assert vnext_http._quota_take("shopping_delivery") == (2, 2)

    for kind in ("contract", "bid_notice", "budget"):
        with pytest.raises(vnext_http.VNextApiError, match="G2B_VNEXT_SHOPPING_ONLY"):
            vnext_http._quota_take(kind)


def test_vnext_daily_limit_is_hard_capped_at_900_and_ignores_legacy_setting(
    monkeypatch, tmp_path
):
    _fresh_db(monkeypatch, tmp_path)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('api_daily_limit','5000')"
        )

    monkeypatch.delenv("G2B_VNEXT_API_DAILY_LIMIT", raising=False)
    assert vnext_http._daily_limit() == 900

    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "5000")
    assert vnext_http._daily_limit() == 900

    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "450")
    assert vnext_http._daily_limit() == 450


def test_g2b_900_and_lofin_100_boundaries_are_independent(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    day = "2026-10-03"
    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "900")
    monkeypatch.setenv("LOFIN_VNEXT_API_DAILY_LIMIT", "100")
    monkeypatch.setattr(vnext_http, "_quota_today", lambda: day)
    monkeypatch.setattr(lofin_vnext_http, "_quota_today", lambda: day)

    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO app_settings(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            [
                ("vnext_api_calls_date", day),
                ("vnext_api_calls_total", "899"),
                ("vnext_api_calls_shopping_count", "899"),
                ("lofin_vnext_calls_date", day),
                ("lofin_vnext_calls_count", "99"),
            ],
        )

    assert vnext_http._quota_take("shopping") == (900, 900)
    with pytest.raises(vnext_http.VNextQuotaReached):
        vnext_http._quota_take("shopping")

    assert lofin_vnext_http._quota_take() == 100
    with pytest.raises(
        lofin_vnext_http.LofinVNextApiError,
        match="LOCAL_DAILY_QUOTA_REACHED",
    ):
        lofin_vnext_http._quota_take()

    with db.connect() as conn:
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key='vnext_api_calls_total'"
        ).fetchone()["value"] == "900"
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key='lofin_vnext_calls_count'"
        ).fetchone()["value"] == "100"


def test_vnext_retry_attempts_each_consume_one_shopping_quota(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "900")
    monkeypatch.setattr(vnext_http, "_quota_today", lambda: "2026-10-03")
    calls = {"n": 0}

    transient = {
        "response": {
            "header": {"resultCode": "05", "resultMsg": "temporary"},
            "body": {"items": [], "totalCount": 0},
        }
    }
    success = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {"items": [{"id": 1}], "totalCount": 1},
        }
    }

    def fake(req, timeout=45):
        calls["n"] += 1
        payload = transient if calls["n"] == 1 else success
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fake)
    monkeypatch.setattr(vnext_http.time, "sleep", lambda *a: None)

    with _bounded_context(monkeypatch, requests=3):
        items, total = vnext_http.request(
            _shopping_url(), "shopping", retries=2
        )

    assert calls["n"] == 2
    assert items == [{"id": 1}]
    assert total == 1
    assert vnext_http.api_usage("shopping") == {
        "date": "2026-10-03",
        "total": 2,
        "limit": 900,
        "kind": "shopping",
        "kind_count": 2,
    }


def test_retry_stops_before_901st_network_call(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    day = "2026-10-03"
    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "900")
    monkeypatch.setattr(vnext_http, "_quota_today", lambda: day)
    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO app_settings(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            [
                ("vnext_api_calls_date", day),
                ("vnext_api_calls_total", "899"),
                ("vnext_api_calls_shopping_count", "899"),
            ],
        )

    calls = {"n": 0}

    def fail(req, timeout=45):
        calls["n"] += 1
        raise urllib.error.URLError("synthetic network failure")

    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fail)
    monkeypatch.setattr(vnext_http.time, "sleep", lambda *a: None)

    with _bounded_context(monkeypatch, requests=3):
        with pytest.raises(vnext_http.VNextQuotaReached):
            vnext_http.request(_shopping_url(), "shopping", retries=3)

    assert calls["n"] == 1
    usage = vnext_http.api_usage("shopping")
    assert usage["total"] == 900
    assert usage["kind_count"] == 900


def test_daily_rollover_resets_only_namespaced_per_kind_counters(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_VNEXT_API_DAILY_LIMIT", "10")
    monkeypatch.setattr(vnext_http, "_quota_today", lambda: "2026-10-03")
    with db.connect() as conn:
        rows = {
            "vnext_api_calls_date": "1900-01-01",
            "vnext_api_calls_total": "9",
            "vnext_api_calls_shopping_count": "7",
            "vnext_api_calls_contract_count": "3",
            "vnextXapiXcallsXrogueXcount": "88",
            "api_calls_shop_count": "17",
        }
        for key, value in rows.items():
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    assert vnext_http._quota_take("shopping") == (1, 10)

    with db.connect() as conn:
        values = {
            key: conn.execute(
                "SELECT value FROM app_settings WHERE key=?", (key,)
            ).fetchone()["value"]
            for key in rows
        }
    assert values["vnext_api_calls_shopping_count"] == "1"
    assert values["vnext_api_calls_contract_count"] == "0"
    assert values["vnextXapiXcallsXrogueXcount"] == "88"
    assert values["api_calls_shop_count"] == "17"


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
    with _bounded_context(monkeypatch):
        with pytest.raises(RuntimeError, match="HTTP 503"):
            vnext_http.request(
                _shopping_url(),
                "shopping_delivery",
                retries=1,
            )


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


def test_gateway_xml_error_envelope_reports_safe_g2b_code(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    raw = b"""<?xml version='1.0' encoding='UTF-8'?>
<OpenAPI_ServiceResponse><cmmMsgHeader>
<returnReasonCode>30</returnReasonCode>
<returnAuthMsg>SECRET-KEY-MUST-NOT-LEAK</returnAuthMsg>
</cmmMsgHeader></OpenAPI_ServiceResponse>"""
    with pytest.raises(vnext_http.VNextApiError) as caught:
        vnext_http.parse_response(raw)
    assert caught.value.code == "30"
    assert caught.value.message == "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"
    assert "SECRET" not in str(caught.value)


def test_http_403_uses_gateway_error_code_instead_of_generic_403(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    body = b"""<?xml version='1.0' encoding='UTF-8'?>
<OpenAPI_ServiceResponse><cmmMsgHeader>
<returnReasonCode>32</returnReasonCode>
<returnAuthMsg>IP BLOCK DETAIL</returnAuthMsg>
</cmmMsgHeader></OpenAPI_ServiceResponse>"""
    def fail(req, timeout=45):
        raise urllib.error.HTTPError(
            "https://example.invalid", 403, "forbidden", {}, io.BytesIO(body)
        )
    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fail)
    with _bounded_context(monkeypatch):
        with pytest.raises(vnext_http.VNextApiError) as caught:
            vnext_http.request(_shopping_url(), "shopping_delivery", retries=1)
    assert caught.value.code == "32"
    assert caught.value.message == "UNREGISTERED_IP_ERROR"
    assert "IP BLOCK DETAIL" not in str(caught.value)


def test_response_size_limit_fails_closed(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    payload = b"x" * (vnext_http.MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(
        vnext_http.urllib.request,
        "urlopen",
        lambda req, timeout=45: _FakeResponse(payload),
    )
    with _bounded_context(monkeypatch):
        with pytest.raises(vnext_http.VNextResponseError) as caught:
            vnext_http.request(_shopping_url(), "shopping_delivery", retries=1)
    assert caught.value.code == "SOURCE_RESPONSE_TOO_LARGE"


def test_transient_source_error_code_retries(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    calls = {"n": 0}
    transient = {
        "response": {
            "header": {"resultCode": "05", "resultMsg": "sensitive upstream text"},
            "body": {"items": [], "totalCount": 0},
        }
    }
    success = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "OK"},
            "body": {"items": [{"id": 1}], "totalCount": 1},
        }
    }

    def fake(req, timeout=45):
        calls["n"] += 1
        payload = transient if calls["n"] == 1 else success
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", fake)
    monkeypatch.setattr(vnext_http.time, "sleep", lambda *a: None)
    with _bounded_context(monkeypatch, requests=3):
        items, total = vnext_http.request(
            _shopping_url(), "shopping_delivery", retries=2
        )
    assert calls["n"] == 2
    assert items == [{"id": 1}]
    assert total == 1


def test_blacklist_ip_error_has_safe_mapping():
    exc = vnext_http._api_error("29", "ignored upstream message")
    assert exc.code == "29"
    assert exc.message == "BLACKLIST_IP_ACCESS_ERROR"
