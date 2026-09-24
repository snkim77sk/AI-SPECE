import db
import json
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


def test_raw_source_key_uses_execution_and_rebid_identity():
    one = {"bidNtceNo": "20260915001", "bidNtceOrd": "00", "bidClsfcNo": "1", "rbidNo": "0", "opengCorpInfo": "A"}
    changed = {"bidNtceNo": "20260915001", "bidNtceOrd": "00", "bidClsfcNo": "1", "rbidNo": "0", "opengCorpInfo": "B"}
    rebid = {"bidNtceNo": "20260915001", "bidNtceOrd": "00", "bidClsfcNo": "1", "rbidNo": "1", "opengCorpInfo": "C"}
    other_execution = {"bidNtceNo": "20260915001", "bidNtceOrd": "00", "bidClsfcNo": "2", "rbidNo": "0", "opengCorpInfo": "D"}

    assert award_vnext._notice_key(one) == "20260915001|00"
    assert award_vnext._raw_source_key(one) == award_vnext._raw_source_key(changed)
    assert award_vnext._raw_source_key(one) != award_vnext._raw_source_key(rebid)
    assert award_vnext._raw_source_key(one) != award_vnext._raw_source_key(other_execution)


def test_collect_opening_preserves_all_rows_and_links_notice(monkeypatch):
    rows=[{'bidNtceNo':'A','bidNtceOrd':'00','bidClsfcNo':'1','rbidNo':'0','corpNm':'일반업체'},
          {'bidNtceNo':'B','bidNtceOrd':'00','bidClsfcNo':'1','rbidNo':'0','corpNm':'조명업체'}]
    monkeypatch.setattr(award_vnext,'fetch_page',lambda *a,**k:(rows,2))
    result=award_vnext.collect_service_opening('2026-09-15','2026-09-15')
    with db.connect() as conn:
        saved=[json.loads(x['payload_json']) for x in conn.execute("SELECT payload_json FROM raw_records WHERE dataset='opening_result_service' ORDER BY id")]
        links=[(x['from_key'],x['link_type']) for x in conn.execute("SELECT * FROM lifecycle_links WHERE confidence>0 ORDER BY id")]
    assert result['complete'] and saved==rows
    assert links==[('A|00','HAS_OPENING_RESULT'),('B|00','HAS_OPENING_RESULT')]
