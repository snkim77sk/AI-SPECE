import award_projection


def test_single_completed_opening_projects_first_rank(monkeypatch):
    seen = {}
    monkeypatch.setattr(award_projection, "upsert_award_result", lambda key, **facts: seen.update({"key": key, **facts}))
    row = {
        "bidNtceNo": "R26BK00000001",
        "bidNtceOrd": "000",
        "opengDt": "2026-09-15 10:00:00",
        "prtcptCnum": "17",
        "progrsDivCdNm": "개찰완료",
        "opengCorpInfo": "가나다주식회사^123-45-67890^홍길동^100,000,000^88.123",
    }
    result = award_projection.project_opening_row(row)
    assert result["first_rank_projected"] is True
    assert seen["key"] == "R26BK00000001|000"
    assert seen["first_rank_vendor"] == "가나다주식회사"
    assert seen["first_rank_bizno"] == "1234567890"
    assert seen["first_rank_amount"] == 100000000
    assert "final_vendor" not in seen


def test_multiple_award_opening_does_not_invent_first_rank(monkeypatch):
    seen = {}
    monkeypatch.setattr(award_projection, "upsert_award_result", lambda key, **facts: seen.update({"key": key, **facts}))
    row = {
        "bidNtceNo": "R26BK00000002",
        "bidNtceOrd": "000",
        "progrsDivCdNm": "개찰완료",
        "opengCorpInfo": "낙찰예정자 다수^기타정보^100000000^88.1",
    }
    result = award_projection.project_opening_row(row)
    assert result["opening_case"] == "multiple"
    assert result["first_rank_projected"] is False
    assert "first_rank_vendor" not in seen


def test_negotiation_opening_does_not_invent_price_rank(monkeypatch):
    seen = {}
    monkeypatch.setattr(award_projection, "upsert_award_result", lambda key, **facts: seen.update({"key": key, **facts}))
    row = {
        "bidNtceNo": "R26BK00000003",
        "bidNtceOrd": "000",
        "progrsDivCdNm": "개찰완료",
        "opengCorpInfo": "협상대상업체^1234567890^대표자^^",
    }
    result = award_projection.project_opening_row(row)
    assert result["opening_case"] == "negotiation"
    assert result["first_rank_projected"] is False
    assert "first_rank_amount" not in seen


def test_final_award_uses_official_final_fields_only(monkeypatch):
    seen = {}
    monkeypatch.setattr(award_projection, "upsert_award_result", lambda key, **facts: seen.update({"key": key, **facts}))
    row = {
        "bidNtceNo": "R26BK00000001",
        "bidNtceOrd": "000",
        "rlOpengDt": "2026-09-15 10:00:00",
        "prtcptCnum": "17",
        "bidwinnrNm": "최종낙찰주식회사",
        "bidwinnrBizno": "987-65-43210",
        "sucsfbidAmt": "101,000,000",
        "sucsfbidRate": "89.012",
        "opengCorpInfo": "다른1순위업체^1111111111^대표^100000000^88.123",
    }
    result = award_projection.project_final_award_row(row)
    assert result["final_award_projected"] is True
    assert seen["final_vendor"] == "최종낙찰주식회사"
    assert seen["final_vendor_bizno"] == "9876543210"
    assert seen["final_award_amount"] == 101000000
    assert seen["award_rate"] == 89.012
    assert "first_rank_vendor" not in seen
