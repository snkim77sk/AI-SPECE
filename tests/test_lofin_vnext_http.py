import json

import budget_vnext
import lofin_vnext_http


def test_budget_vnext_uses_independent_lofin_fetcher():
    assert budget_vnext.fetch_budget_page.__module__ == "lofin_vnext_http"


def test_parse_json_preserves_all_rows_and_total():
    payload = {
        "QWGJK": [{
            "head": [
                {"list_total_count": 2},
                {"RESULT": {"CODE": "INFO-000", "MESSAGE": "OK"}},
            ],
            "row": [
                {"dbiz_nm": "일반 행정사업", "dbiz_cd": "1"},
                {"dbiz_nm": "LED 가로등 교체", "dbiz_cd": "2"},
            ],
        }]
    }
    rows, total, code, message = lofin_vnext_http.parse_response(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    assert [row["dbiz_cd"] for row in rows] == ["1", "2"]
    assert total == 2
    assert code == "INFO-000"
    assert message == "OK"


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
