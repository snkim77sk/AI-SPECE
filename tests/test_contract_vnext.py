import db
import json
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

    assert seen["kind"] == "contract"
    assert "/getCntrctInfoListServc?" in seen["url"]
    assert "cntrctNm=" not in seen["url"]
    assert "LED" not in seen["url"]
    assert "%EC%A1%B0%EB%AA%85" not in seen["url"]
    assert "inqryBgnDt=202609010000" in seen["url"]
    assert "inqryEndDt=202609152359" in seen["url"]


def test_contract_source_key_uses_required_contract_number_not_optional_unified_number():
    ordinary = {"untyCntrctNo": "U1", "dcsnCntrctNo": "C1", "cntrctNm": "청소 용역"}
    lighting = {"untyCntrctNo": "U2", "dcsnCntrctNo": "C2", "cntrctNm": "LED 조명 용역"}
    assert contract_vnext._source_key(ordinary) == "C1"
    assert contract_vnext._source_key(lighting) == "C2"


def test_contract_source_key_does_not_change_when_optional_unified_number_appears():
    before = {"dcsnCntrctNo": "C1", "cntrctNm": "청소 용역"}
    after = {"untyCntrctNo": "U1", "dcsnCntrctNo": "C1", "cntrctNm": "청소 용역"}
    assert contract_vnext._source_key(before) == "C1"
    assert contract_vnext._source_key(after) == "C1"


def test_unified_only_contract_is_preserved_but_not_complete(monkeypatch):
    rows = [{"untyCntrctNo": "U1", "cntrctNm": "청소 용역"}]
    monkeypatch.setattr(contract_vnext, "fetch_page", lambda *a, **k: (rows, 1))
    result = contract_vnext.collect_all("2026-09-15", "2026-09-15")
    with db.connect() as conn:
        saved = [json.loads(x["payload_json"]) for x in conn.execute(
            "SELECT payload_json FROM raw_records WHERE dataset='contract_service' ORDER BY id"
        )]
    assert result["complete"] is False
    assert result["reason"] == "MISSING_CONTRACT_IDENTITY"
    assert saved == rows


def test_collect_contracts_preserves_ordinary_and_lighting(monkeypatch):
    rows=[{'untyCntrctNo':'U1','dcsnCntrctNo':'C1','cntrctNm':'청소 용역'},
          {'untyCntrctNo':'U2','dcsnCntrctNo':'C2','cntrctNm':'LED 조명 용역'}]
    monkeypatch.setattr(contract_vnext,'fetch_page',lambda *a,**k:(rows,2))
    result=contract_vnext.collect_all('2026-09-15','2026-09-15')
    with db.connect() as conn:
        saved=[json.loads(x['payload_json']) for x in conn.execute("SELECT payload_json FROM raw_records WHERE dataset='contract_service' ORDER BY id")]
    assert result['complete'] and saved==rows
