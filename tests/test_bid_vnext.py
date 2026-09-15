import bid_vnext


def test_source_key_ignores_notice_title_and_target_keywords():
    ordinary = {
        "bidNtceNo": "20260915001",
        "bidNtceOrd": "00",
        "bidNtceNm": "일반 행정업무 위탁",
    }
    lighting = {
        "bidNtceNo": "20260915002",
        "bidNtceOrd": "00",
        "bidNtceNm": "LED 가로등 교체 용역",
    }
    assert bid_vnext._source_key(ordinary) == "20260915001|00"
    assert bid_vnext._source_key(lighting) == "20260915002|00"


def test_fetch_goods_page_has_no_keyword_prefilter(monkeypatch):
    seen = {}
    monkeypatch.setattr(bid_vnext, "_service_key", lambda: "KEY")

    def fake_request(url, kind):
        seen["url"] = url
        seen["kind"] = kind
        return [], 0

    monkeypatch.setattr(bid_vnext, "_request", fake_request)
    bid_vnext.fetch_page("goods", "2026-09-01", "2026-09-15", page=1, rows=100)

    assert seen["kind"] == "bid"
    assert "/getBidPblancListInfoThng?" in seen["url"]
    assert "bidNtceNm=" not in seen["url"]
    assert "LED" not in seen["url"]
    assert "%EC%A1%B0%EB%AA%85" not in seen["url"]
    assert "inqryBgnDt=202609010000" in seen["url"]
    assert "inqryEndDt=202609152359" in seen["url"]


def test_fetch_service_page_has_no_keyword_prefilter(monkeypatch):
    seen = {}
    monkeypatch.setattr(bid_vnext, "_service_key", lambda: "KEY")

    def fake_request(url, kind):
        seen["url"] = url
        seen["kind"] = kind
        return [], 0

    monkeypatch.setattr(bid_vnext, "_request", fake_request)
    bid_vnext.fetch_page("service", "2026-09-01", "2026-09-15", page=2, rows=999)

    assert seen["kind"] == "bid"
    assert "/getBidPblancListInfoServc?" in seen["url"]
    assert "bidNtceNm=" not in seen["url"]
    assert "pageNo=2" in seen["url"]


def test_collect_all_preserves_ordinary_and_lighting_rows(monkeypatch):
    rows = [
        {"bidNtceNo": "A", "bidNtceOrd": "00", "bidNtceNm": "청사 청소 용역", "bidNtceDt": "202609151000"},
        {"bidNtceNo": "B", "bidNtceOrd": "00", "bidNtceNm": "LED 조명 설계 용역", "bidNtceDt": "202609151100"},
    ]
    preserved = []
    checkpoints = []

    monkeypatch.setattr(bid_vnext, "get_checkpoint", lambda *args, **kwargs: None)
    monkeypatch.setattr(bid_vnext, "fetch_page", lambda *args, **kwargs: (rows, 2))
    monkeypatch.setattr(
        bid_vnext,
        "preserve_raw",
        lambda dataset, source_key, payload, **kwargs: preserved.append((dataset, source_key, payload["bidNtceNm"])),
    )
    monkeypatch.setattr(
        bid_vnext,
        "save_checkpoint",
        lambda dataset, scope, **kwargs: checkpoints.append((dataset, scope, kwargs.get("status"))),
    )

    result = bid_vnext.collect_all("service", "2026-09-15", "2026-09-15")

    assert result["complete"] is True
    assert result["fetched"] == 2
    assert [item[2] for item in preserved] == ["청사 청소 용역", "LED 조명 설계 용역"]
    assert checkpoints[-1][2] == "COMPLETE"
