import db
import json
import vnext_store
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

    assert seen["kind"] == "bid_notice"
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

    assert seen["kind"] == "bid_notice"
    assert "/getBidPblancListInfoServc?" in seen["url"]
    assert "bidNtceNm=" not in seen["url"]
    assert "pageNo=2" in seen["url"]


def test_collect_all_preserves_ordinary_and_lighting_rows(monkeypatch):
    rows=[{'bidNtceNo':'A','bidNtceOrd':'00','bidNtceNm':'청사 청소 용역'},
          {'bidNtceNo':'B','bidNtceOrd':'00','bidNtceNm':'LED 조명 설계 용역'}]
    monkeypatch.setattr(bid_vnext,'fetch_page',lambda *a,**k:(rows,2))
    result=bid_vnext.collect_all('service','2026-09-15','2026-09-15')
    assert result['complete'] and result['fetched']==2
    with db.connect() as conn:
        saved=[json.loads(x['payload_json']) for x in conn.execute("SELECT payload_json FROM raw_records WHERE dataset='bid_notice_service' ORDER BY id")]
    assert saved==rows
    assert vnext_store.get_checkpoint('bid_notice_service','2026-09-15:2026-09-15')['status']=='COMPLETE'
