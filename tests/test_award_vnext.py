import award_vnext


def test_opening_fetch_uses_service_opening_operation_without_keyword(monkeypatch):
    seen = {}
    monkeypatch.setattr(award_vnext, "_service_key", lambda: "KEY")

    def fake_request(url, kind):
        seen["url"] = url
        seen["kind"] = kind
        return [], 0

    monkeypatch.setattr(award_vnext, "_request", fake_request)
    award_vnext.fetch_page("opening", "2026-09-01", "2026-09-15", page=1, rows=100)

    assert seen["kind"] == "opening"
    assert "/getOpengResultListInfoServc?" in seen["url"]
    assert "bidNtceNm=" not in seen["url"]
    assert "LED" not in seen["url"]
    assert "inqryBgnDt=202609010000" in seen["url"]
    assert "inqryEndDt=202609152359" in seen["url"]


def test_award_fetch_uses_service_award_operation_without_keyword(monkeypatch):
    seen = {}
    monkeypatch.setattr(award_vnext, "_service_key", lambda: "KEY")

    def fake_request(url, kind):
        seen["url"] = url
        seen["kind"] = kind
        return [], 0

    monkeypatch.setattr(award_vnext, "_request", fake_request)
    award_vnext.fetch_page("award", "2026-09-01", "2026-09-15", page=2, rows=999)

    assert seen["kind"] == "award"
    assert "/getScsbidListSttusServc?" in seen["url"]
    assert "bidNtceNm=" not in seen["url"]
    assert "pageNo=2" in seen["url"]


def test_raw_source_key_keeps_multiple_rows_for_one_notice():
    one = {"bidNtceNo": "20260915001", "bidNtceOrd": "00", "corpNm": "A사", "rank": "1"}
    two = {"bidNtceNo": "20260915001", "bidNtceOrd": "00", "corpNm": "B사", "rank": "2"}
    assert award_vnext._notice_key(one) == "20260915001|00"
    assert award_vnext._raw_source_key(one) != award_vnext._raw_source_key(two)


def test_collect_opening_preserves_all_rows_and_links_notice(monkeypatch):
    rows = [
        {"bidNtceNo": "A", "bidNtceOrd": "00", "corpNm": "일반업체"},
        {"bidNtceNo": "B", "bidNtceOrd": "00", "corpNm": "조명업체"},
    ]
    preserved = []
    linked = []

    monkeypatch.setattr(award_vnext, "get_checkpoint", lambda *args, **kwargs: None)
    monkeypatch.setattr(award_vnext, "fetch_page", lambda *args, **kwargs: (rows, 2))
    monkeypatch.setattr(
        award_vnext,
        "preserve_raw",
        lambda dataset, source_key, payload, **kwargs: preserved.append((dataset, source_key, payload["corpNm"])),
    )
    monkeypatch.setattr(award_vnext, "save_checkpoint", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        award_vnext,
        "save_lifecycle_link",
        lambda from_type, from_key, to_type, to_key, link_type, **kwargs: linked.append((from_key, link_type)),
    )

    result = award_vnext.collect_service_opening("2026-09-15", "2026-09-15")

    assert result["complete"] is True
    assert [x[2] for x in preserved] == ["일반업체", "조명업체"]
    assert linked == [("A|00", "HAS_OPENING_RESULT"), ("B|00", "HAS_OPENING_RESULT")]
