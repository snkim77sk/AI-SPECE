import datetime as dt
import json

import g2b_vnext_canary


def test_canary_summaries_never_emit_raw_vendor_or_business_values():
    rows=[{"bidNtceNo":"R26BK00000001","bidClsfcNo":"1","rbidNo":"0",
           "opengCorpInfo":"비밀업체^1234567890^대표자^100000000^88.12","progrsDivCdNm":"개찰완료"}]
    out=g2b_vnext_canary.summarize_rows(rows,["bidNtceNo","opengCorpInfo"],g2b_vnext_canary._opening_shape,
        requirements=[{"name":"notice","fields":["bidNtceNo"]}])
    text=json.dumps(out,ensure_ascii=False)
    assert "비밀업체" not in text and "1234567890" not in text
    assert out["validation"]["required_ok"] is True


def test_probe_stops_at_first_nonempty_day_even_if_structure_is_invalid():
    calls=[]
    def fetcher(start,end,page,rows):
        calls.append(start)
        if start == "2026-09-15": return [{"unexpected_field":"x"}],1
        if start == "2026-09-14": return [{"bidNtceNo":"X"}],1
        return [],0
    result=g2b_vnext_canary._probe_one_day(fetcher,["bidNtceNo"],requirements=[{"name":"notice","fields":["bidNtceNo"]}],
                                           today=dt.date(2026,9,16),lookback_days=7)
    assert calls == ["2026-09-16","2026-09-15"]
    assert result["selected_day"] == "2026-09-15"
    assert result["conclusive"] is False


def test_run_canary_requires_real_structural_fields(monkeypatch):
    monkeypatch.setattr(g2b_vnext_canary.db,"init_db",lambda:None)
    good={"bidNtceNo":"N","bidNtceOrd":"000","bidClsfcNo":"1","rbidNo":"0",
          "opengCorpInfo":"업체^1234567890^대표^100^88","progrsDivCdNm":"개찰완료",
          "bidwinnrNm":"업체","sucsfbidAmt":"100","untyCntrctNo":"C","ntceNo":"N000",
          "corpList":"[1^단독^^업체^대표^대한민국^100^^담당자^1234567890]","dlvrReqNo":"D"}
    monkeypatch.setattr(g2b_vnext_canary.bid_vnext,"fetch_page",lambda *a,**k:([good],1))
    monkeypatch.setattr(g2b_vnext_canary.award_vnext,"fetch_page",lambda *a,**k:([good],1))
    monkeypatch.setattr(g2b_vnext_canary.contract_vnext,"fetch_page",lambda *a,**k:([good],1))
    monkeypatch.setattr(g2b_vnext_canary.shopping_vnext,"fetch_page",lambda *a,**k:([good],1))
    report=g2b_vnext_canary.run_canary(today=dt.date(2026,9,16),rows=10,lookback_days=1)
    assert report["status"] == "CONCLUSIVE"
    assert report["probe_count"] == 6


def test_unexpected_field_only_never_yields_conclusive(monkeypatch):
    monkeypatch.setattr(g2b_vnext_canary.db,"init_db",lambda:None)
    simple=lambda *a,**k:([{"unexpected_field":"x"}],1)
    monkeypatch.setattr(g2b_vnext_canary.bid_vnext,"fetch_page",simple)
    monkeypatch.setattr(g2b_vnext_canary.award_vnext,"fetch_page",simple)
    monkeypatch.setattr(g2b_vnext_canary.contract_vnext,"fetch_page",simple)
    monkeypatch.setattr(g2b_vnext_canary.shopping_vnext,"fetch_page",simple)
    report=g2b_vnext_canary.run_canary(today=dt.date(2026,9,16),rows=10,lookback_days=1)
    assert report["status"] == "PARTIAL"
    assert report["conclusive_probe_count"] == 0
