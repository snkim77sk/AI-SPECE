import award_projection


def _capture(monkeypatch):
    seen = {}
    links = []

    def fake_replace(source_key, group, raw_source_key="", base_facts=None, **facts):
        seen.clear()
        seen.update({"key": source_key, "group": group, "raw_source_key": raw_source_key})
        seen.update(base_facts or {})
        seen.update(facts)
        return facts

    monkeypatch.setattr(award_projection, "replace_fact_group", fake_replace)
    monkeypatch.setattr(award_projection, "_invalidate_contract_projections_for_notice", lambda notice: 0)
    monkeypatch.setattr(
        award_projection,
        "save_lifecycle_link",
        lambda from_type, from_key, to_type, to_key, link_type, **kwargs: links.append(
            (from_type, from_key, to_type, to_key, link_type)
        ),
    )
    return seen, links


def test_single_completed_opening_projects_first_rank_per_execution(monkeypatch):
    seen, links = _capture(monkeypatch)
    row = {
        "bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": "0",
        "opengDt": "2026-09-15 10:00:00", "prtcptCnum": "17", "progrsDivCdNm": "개찰완료",
        "opengCorpInfo": "가나다주식회사^123-45-67890^홍길동^100,000,000^88.123",
    }
    result = award_projection.project_opening_row(row, raw_source_key="RAW-O")
    assert result["first_rank_projected"] is True
    assert result["notice_key"] == "R26BK00000001|000"
    assert result["award_summary_key"] == "R26BK00000001|000|1|0"
    assert seen["key"] == "R26BK00000001|000|1|0"
    assert seen["group"] == "opening"
    assert seen["raw_source_key"] == "RAW-O"
    assert seen["first_rank_vendor"] == "가나다주식회사"
    assert seen["first_rank_bizno"] == "1234567890"
    assert seen["first_rank_amount"] == 100000000
    assert ("bid_notice", "R26BK00000001|000", "award_summary", "R26BK00000001|000|1|0", "HAS_AWARD_EXECUTION") in links


def test_multiple_award_opening_clears_first_rank(monkeypatch):
    seen, _ = _capture(monkeypatch)
    row = {"bidNtceNo": "R26BK00000002", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": "0",
           "progrsDivCdNm": "개찰완료", "opengCorpInfo": "낙찰예정자 다수^기타정보^100000000^88.1"}
    result = award_projection.project_opening_row(row)
    assert result["opening_case"] == "multiple"
    assert result["first_rank_projected"] is False
    assert seen["first_rank_vendor"] == ""
    assert seen["first_rank_bizno"] == ""
    assert seen["first_rank_amount"] == 0


def test_negotiation_opening_clears_price_rank(monkeypatch):
    seen, _ = _capture(monkeypatch)
    row = {"bidNtceNo": "R26BK00000003", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": "0",
           "progrsDivCdNm": "개찰완료", "opengCorpInfo": "협상대상업체^1234567890^대표자^^"}
    result = award_projection.project_opening_row(row)
    assert result["opening_case"] == "negotiation"
    assert result["first_rank_projected"] is False
    assert seen["first_rank_amount"] == 0


def test_final_award_uses_official_final_fields_only(monkeypatch):
    seen, links = _capture(monkeypatch)
    row = {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": "0",
           "rlOpengDt": "2026-09-15 10:00:00", "prtcptCnum": "17",
           "bidwinnrNm": "최종낙찰주식회사", "bidwinnrBizno": "987-65-43210",
           "sucsfbidAmt": "101,000,000", "sucsfbidRate": "89.012",
           "opengCorpInfo": "다른1순위업체^1111111111^대표^100000000^88.123"}
    result = award_projection.project_final_award_row(row, raw_source_key="RAW-A")
    assert result["final_award_projected"] is True
    assert seen["group"] == "final_award"
    assert seen["final_vendor"] == "최종낙찰주식회사"
    assert seen["final_vendor_bizno"] == "9876543210"
    assert seen["final_award_amount"] == 101000000
    assert seen["award_rate"] == 89.012
    assert any(link[-1] == "HAS_AWARD_EXECUTION" for link in links)


def test_rebid_execution_keys_remain_separate():
    base = {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidClsfcNo": "1"}
    assert award_projection.execution_key(dict(base, rbidNo="0")) == "R26BK00000001|000|1|0"
    assert award_projection.execution_key(dict(base, rbidNo="1")) == "R26BK00000001|000|1|1"
