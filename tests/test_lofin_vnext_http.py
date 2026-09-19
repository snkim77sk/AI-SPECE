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
