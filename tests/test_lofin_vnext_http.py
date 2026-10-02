import json
import pytest

import budget_vnext
import lofin_vnext_http


def test_budget_vnext_uses_independent_lofin_fetcher():
    assert budget_vnext.fetch_budget_page.__module__ == "lofin_vnext_http"


def test_parse_json_preserves_all_rows_and_total():
    payload = {"QWGJK": [{"head": [{"list_total_count": 2},
                                     {"RESULT": {"CODE": "INFO-000", "MESSAGE": "OK"}}],
                            "row": [{"dbiz_nm": "일반 행정사업", "dbiz_cd": "1"},
                                    {"dbiz_nm": "LED 가로등 교체", "dbiz_cd": "2"}]}]}
    rows, total, code, message = lofin_vnext_http.parse_response(json.dumps(payload).encode())
    assert [row["dbiz_cd"] for row in rows] == ["1", "2"]
    assert total == 2
    assert code == "INFO-000"


def test_missing_total_remains_unknown_instead_of_page_length():
    payload = {"QWGJK": [{"head": [{"RESULT": {"CODE": "INFO-000", "MESSAGE": "OK"}}],
                            "row": [{"dbiz_cd": "1"}, {"dbiz_cd": "2"}]}]}
    rows, total, *_ = lofin_vnext_http.parse_response(json.dumps(payload).encode())
    assert len(rows) == 2
    assert total is None


def test_malformed_json_and_html_are_errors():
    with pytest.raises(lofin_vnext_http.LofinVNextApiError):
        lofin_vnext_http.parse_response(json.dumps({"error": "temporary failure"}).encode())
    with pytest.raises(lofin_vnext_http.LofinVNextApiError):
        lofin_vnext_http.parse_response(b"<html><body>Service unavailable</body></html>")


def test_fetch_budget_page_sends_empty_keyword_without_legacy_state(monkeypatch):
    captured = {}
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "secret")
    def fake_request(params, retries=3, timeout=45):
        captured.update(params)
        return [], 0, "INFO-200", "NO DATA"
    monkeypatch.setattr(lofin_vnext_http, "_request", fake_request)
    lofin_vnext_http.fetch_budget_page(2026, "2026-09-16", "", page=3, size=77)
    assert captured["fyr"] == 2026
    assert captured["exe_ymd"] == "20260916"
    assert captured["dbiz_nm"] == ""
    assert captured["pIndex"] == 3
    assert captured["pSize"] == 77


def test_fetch_budget_page_can_send_wide_area_partition_without_keyword(monkeypatch):
    captured = {}
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "secret")

    def fake_request(params, retries=3, timeout=45):
        captured.update(params)
        return [], 0, "INFO-200", "NO DATA"

    monkeypatch.setattr(lofin_vnext_http, "_request", fake_request)
    lofin_vnext_http.fetch_budget_page(
        2026, "2026-09-16", "", page=2, size=1000, region_code="4100000"
    )

    assert captured["fyr"] == 2026
    assert captured["exe_ymd"] == "20260916"
    assert captured["dbiz_nm"] == ""
    assert captured["wa_laf_cd"] == "4100000"
    assert captured["pIndex"] == 2
    assert captured["pSize"] == 1000


def test_lofin_daily_limit_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("LOFIN_VNEXT_API_DAILY_LIMIT", "not-a-number")
    assert lofin_vnext_http._daily_limit() == 100


def test_lofin_corrupt_stored_quota_count_recovers_to_zero():
    assert lofin_vnext_http._stored_quota_count("bad") == 0
    assert lofin_vnext_http._stored_quota_count("-7") == 0
    assert lofin_vnext_http._stored_quota_count("12") == 12


def test_lofin_daily_quota_status_reports_remaining(monkeypatch):
    import db

    monkeypatch.setenv("LOFIN_VNEXT_API_DAILY_LIMIT", "100")
    monkeypatch.setattr(lofin_vnext_http, "_quota_today", lambda: "2026-10-02")
    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO app_settings(key,value) VALUES(?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            [
                ("lofin_vnext_calls_date", "2026-10-02"),
                ("lofin_vnext_calls_count", "24"),
            ],
        )

    assert lofin_vnext_http.daily_quota_status() == {
        "date": "2026-10-02",
        "limit": 100,
        "used": 24,
        "remaining": 76,
    }


def test_lofin_quota_take_uses_transaction_advisory_lock_on_postgres(monkeypatch):
    calls = []

    class Row:
        def __getitem__(self, key):
            return {
                "key": "lofin_vnext_calls_count",
                "value": "0",
            }[key]

    class Result:
        def __iter__(self):
            return iter([])

    class FakeConn:
        def execute(self, sql, params=()):
            calls.append((str(sql), tuple(params)))
            return Result()

        def executemany(self, sql, params):
            calls.append((str(sql), tuple(tuple(x) for x in params)))

    class FakeContext:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(lofin_vnext_http, "backend_name", lambda: "POSTGRESQL")
    monkeypatch.setattr(lofin_vnext_http, "connect", lambda: FakeContext())
    monkeypatch.setattr(lofin_vnext_http, "_quota_today", lambda: "2026-10-02")
    monkeypatch.setattr(lofin_vnext_http, "_daily_limit", lambda: 100)

    assert lofin_vnext_http._quota_take() == 1
    assert any(
        "pg_advisory_xact_lock" in sql
        and params == ("g2b_lofin_daily_quota",)
        for sql, params in calls
    )
    assert not any(sql.strip().upper() == "BEGIN IMMEDIATE" for sql, _ in calls)
