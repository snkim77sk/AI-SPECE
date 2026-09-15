import contract_projection


def test_parse_single_contract_party():
    value = "[1^단독^^가나다주식회사^홍길동^대한민국^100^^담당자^123-45-67890]"
    parties = contract_projection.parse_contract_parties(value)
    assert parties == [{"name": "가나다주식회사", "bizno": "1234567890", "share": "100"}]


def test_parse_joint_contract_preserves_multiple_parties():
    value = (
        "[1^대표사^^가나다주식회사^홍길동^대한민국^60^^담당자^1234567890],"
        "[2^구성사^^라마바주식회사^김대표^대한민국^40^^담당자^9876543210]"
    )
    parties = contract_projection.parse_contract_parties(value)
    assert len(parties) == 2
    assert parties[0]["name"] == "가나다주식회사"
    assert parties[1]["bizno"] == "9876543210"


def test_exact_contract_link_projects_contract_fields(monkeypatch):
    award_seen = {}
    links = []
    monkeypatch.setattr(contract_projection, "resolve_notice_key", lambda row: "R26BK00000001|000")
    monkeypatch.setattr(
        contract_projection,
        "upsert_award_result",
        lambda key, **facts: award_seen.update({"key": key, **facts}),
    )
    monkeypatch.setattr(
        contract_projection,
        "save_lifecycle_link",
        lambda from_type, from_key, to_type, to_key, link_type, **kwargs: links.append(
            (from_type, from_key, to_type, to_key, link_type)
        ),
    )
    row = {
        "dcsnCntrctNo": "R26TA0000000100",
        "thtmCntrctAmt": "101,500,000",
        "corpList": "[1^단독^^최종계약주식회사^대표^대한민국^100^^담당자^987-65-43210]",
        "ntceNo": "R26BK00000001000",
    }

    result = contract_projection.project_contract_row(row, raw_source_key="RAW-1")

    assert result["linked"] is True
    assert award_seen["key"] == "R26BK00000001|000"
    assert award_seen["contract_no"] == "R26TA0000000100"
    assert award_seen["contract_vendor"] == "최종계약주식회사"
    assert award_seen["contract_vendor_bizno"] == "9876543210"
    assert award_seen["contract_amount"] == 101500000
    assert any(link[-1] == "HAS_CONTRACT" for link in links)
    assert any(link[-1] == "RESULTED_IN_CONTRACT" for link in links)


def test_ambiguous_notice_reference_is_not_guessed(monkeypatch):
    called = {"award": False, "link": False}
    monkeypatch.setattr(contract_projection, "resolve_notice_key", lambda row: "")
    monkeypatch.setattr(
        contract_projection,
        "upsert_award_result",
        lambda *args, **kwargs: called.update(award=True),
    )
    monkeypatch.setattr(
        contract_projection,
        "save_lifecycle_link",
        lambda *args, **kwargs: called.update(link=True),
    )
    row = {"dcsnCntrctNo": "C-AMBIG", "ntceNo": "AMBIGUOUS"}

    result = contract_projection.project_contract_row(row)

    assert result["reason"] == "notice_unresolved"
    assert called == {"award": False, "link": False}
