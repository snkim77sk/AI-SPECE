import contract_projection


def test_parse_single_contract_party():
    value = "[1^단독^^가나다주식회사^홍길동^대한민국^100^^담당자^123-45-67890]"
    assert contract_projection.parse_contract_parties(value) == [
        {"name": "가나다주식회사", "bizno": "1234567890", "share": "100"}
    ]


def test_parse_joint_contract_preserves_multiple_parties():
    value = ("[1^대표사^^가나다주식회사^홍길동^대한민국^60^^담당자^1234567890],"
             "[2^구성사^^라마바주식회사^김대표^대한민국^40^^담당자^9876543210]")
    parties = contract_projection.parse_contract_parties(value)
    assert len(parties) == 2
    assert parties[0]["name"] == "가나다주식회사"
    assert parties[1]["bizno"] == "9876543210"


def _patch_projection(monkeypatch, award_key="R26BK00000001|000|1|0"):
    seen = {}
    links = []
    monkeypatch.setattr(contract_projection, "_retire_previous_raw_projection", lambda raw: 0)
    monkeypatch.setattr(contract_projection, "resolve_notice_key", lambda row: "R26BK00000001|000")
    monkeypatch.setattr(contract_projection, "resolve_unique_final_award_key", lambda notice: award_key)
    monkeypatch.setattr(
        contract_projection, "replace_fact_group",
        lambda key, group, raw_source_key="", **facts: seen.update(
            {"key": key, "group": group, "raw_source_key": raw_source_key, **facts}
        ),
    )
    monkeypatch.setattr(
        contract_projection, "save_lifecycle_link",
        lambda from_type, from_key, to_type, to_key, link_type, **kwargs: links.append(
            (from_type, from_key, to_type, to_key, link_type)
        ),
    )
    return seen, links


def test_exact_contract_link_merges_into_unique_final_award_execution(monkeypatch):
    seen, links = _patch_projection(monkeypatch)
    row = {"dcsnCntrctNo": "R26TA0000000100", "thtmCntrctAmt": "101,500,000",
           "corpList": "[1^단독^^최종계약주식회사^대표^대한민국^100^^담당자^987-65-43210]",
           "ntceNo": "R26BK00000001000"}
    result = contract_projection.project_contract_row(row, raw_source_key="RAW-1")
    assert result["linked"] is True
    assert result["award_summary_linked"] is True
    assert seen["key"] == "R26BK00000001|000|1|0"
    assert seen["group"] == "contract"
    assert seen["contract_vendor"] == "최종계약주식회사"
    assert seen["contract_vendor_bizno"] == "9876543210"
    assert seen["contract_amount"] == 101500000
    assert any(link[-1] == "HAS_CONTRACT" for link in links)
    assert any(link[-1] == "RESULTED_IN_CONTRACT" for link in links)


def test_contract_keeps_notice_link_but_does_not_guess_ambiguous_award_execution(monkeypatch):
    seen, links = _patch_projection(monkeypatch, award_key="")
    row = {"dcsnCntrctNo": "C-1", "thtmCntrctAmt": "100", "ntceNo": "R26BK00000001000"}
    result = contract_projection.project_contract_row(row)
    assert result["award_summary_linked"] is False
    assert seen == {}
    assert any(link[-1] == "HAS_CONTRACT" for link in links)
    assert not any(link[-1] == "RESULTED_IN_CONTRACT" for link in links)


def test_ambiguous_notice_reference_is_not_guessed(monkeypatch):
    called = {"replace": False, "link": False}
    monkeypatch.setattr(contract_projection, "_retire_previous_raw_projection", lambda raw: 0)
    monkeypatch.setattr(contract_projection, "resolve_notice_key", lambda row: "")
    monkeypatch.setattr(contract_projection, "replace_fact_group", lambda *a, **k: called.update(replace=True))
    monkeypatch.setattr(contract_projection, "save_lifecycle_link", lambda *a, **k: called.update(link=True))
    result = contract_projection.project_contract_row({"dcsnCntrctNo": "C-AMBIG", "ntceNo": "AMBIGUOUS"})
    assert result["reason"] == "notice_unresolved"
    assert called == {"replace": False, "link": False}


def test_malformed_joint_party_never_collapses_to_single_vendor(monkeypatch):
    seen, _ = _patch_projection(monkeypatch)
    row = {"dcsnCntrctNo": "C-2", "ntceNo": "R26BK00000001000",
           "corpList": "[1^대표사^^정상업체^대표^대한민국^60^^담당자^1234567890],[broken]"}
    result = contract_projection.project_contract_row(row, raw_source_key="RAW-2")
    assert result["party_parse_ambiguous"] is True
    assert result["single_party_projected"] is False
    assert seen["contract_vendor"] == ""
    assert seen["contract_vendor_bizno"] == ""
