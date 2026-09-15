import contract_vnext


def test_fetch_contract_page_has_no_keyword_prefilter(monkeypatch):
    seen = {}
    monkeypatch.setattr(contract_vnext, "_service_key", lambda: "KEY")

    def fake_request(url, kind):
        seen["url"] = url
        seen["kind"] = kind
        return [], 0

    monkeypatch.setattr(contract_vnext, "_request", fake_request)
    contract_vnext.fetch_page("2026-09-01", "2026-09-15", page=1, rows=100)

    assert seen["kind"] == "bid"
    assert "/getCntrctInfoListServc?" in seen["url"]
    assert "cntrctNm=" not in seen["url"]
    assert "LED" not in seen["url"]
    assert "%EC%A1%B0%EB%AA%85" not in seen["url"]
    assert "inqryBgnDt=202609010000" in seen["url"]
    assert "inqryEndDt=202609152359" in seen["url"]


def test_contract_source_key_uses_contract_identity_not_title():
    ordinary = {"untyCntrctNo": "U1", "dcsnCntrctNo": "C1", "cntrctNm": "청소 용역"}
    lighting = {"untyCntrctNo": "U2", "dcsnCntrctNo": "C2", "cntrctNm": "LED 조명 용역"}
    assert contract_vnext._source_key(ordinary) == "U1|C1"
    assert contract_vnext._source_key(lighting) == "U2|C2"


def test_collect_contracts_preserves_ordinary_and_lighting(monkeypatch):
    rows = [
        {"untyCntrctNo": "U1", "dcsnCntrctNo": "C1", "cntrctNm": "청소 용역"},
        {"untyCntrctNo": "U2", "dcsnCntrctNo": "C2", "cntrctNm": "LED 조명 용역"},
    ]
    preserved = []
    monkeypatch.setattr(contract_vnext, "get_checkpoint", lambda *args, **kwargs: None)
    monkeypatch.setattr(contract_vnext, "fetch_page", lambda *args, **kwargs: (rows, 2))
    monkeypatch.setattr(contract_vnext, "save_checkpoint", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        contract_vnext,
        "preserve_raw",
        lambda dataset, source_key, payload, **kwargs: preserved.append(payload["cntrctNm"]),
    )

    result = contract_vnext.collect_all("2026-09-15", "2026-09-15")

    assert result["complete"] is True
    assert preserved == ["청소 용역", "LED 조명 용역"]
