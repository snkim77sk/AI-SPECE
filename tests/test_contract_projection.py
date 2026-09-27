import db
import vnext_store
import award_projection


def _seed_notice_and_final(no='R26BK00000001', execution='1'):
    vnext_store.preserve_raw('bid_notice_service',no+'|000',{'bidNtceNo':no,'bidNtceOrd':'000'})
    row={'bidNtceNo':no,'bidNtceOrd':'000','bidClsfcNo':execution,'rbidNo':'0',
         'bidwinnrNm':'최종업체','bidwinnrBizno':'9876543210','sucsfbidAmt':'100'}
    key=award_projection.execution_key(row)
    vnext_store.preserve_raw('award_result_service',key,row)
    award_projection.project_final_award_row(row,raw_source_key=key)
    return key


def _db_rows(sql):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql)]

import contract_projection


def test_parse_single_contract_party():
    value = "[1^단독^^가나다주식회사^홍길동^대한민국^100^^담당자^123-45-67890]"
    parties = contract_projection.parse_contract_parties(value)
    assert parties == [{"name": "가나다주식회사", "bizno": "1234567890", "share": "100", "valid": True}]


def test_parse_joint_contract_preserves_multiple_parties():
    value = (
        "[1^대표사^^가나다주식회사^홍길동^대한민국^60^^담당자^1234567890],"
        "[2^구성사^^라마바주식회사^김대표^대한민국^40^^담당자^9876543210]"
    )
    parties = contract_projection.parse_contract_parties(value)
    assert len(parties) == 2
    assert parties[0]["name"] == "가나다주식회사"
    assert parties[1]["bizno"] == "9876543210"


def test_exact_contract_link_merges_into_unique_final_award_execution(monkeypatch):
    key=_seed_notice_and_final()
    row={'dcsnCntrctNo':'R26TA0000000100','thtmCntrctAmt':'101,500,000',
         'corpList':'[1^단독^^최종계약주식회사^대표^대한민국^100^^담당자^987-65-43210]',
         'ntceNo':'R26BK00000001000'}
    vnext_store.preserve_raw('contract_service','RAW-1',row)
    result=contract_projection.project_contract_row(row,raw_source_key='RAW-1')
    assert result['linked'] and result['award_summary_linked'] and result['award_summary_key']==key
    facts=_db_rows('SELECT * FROM award_results')[0]
    assert facts['contract_no']=='R26TA0000000100'
    assert facts['contract_vendor']=='최종계약주식회사' and facts['contract_vendor_bizno']=='9876543210'
    assert facts['contract_amount']==101500000
    kinds={r['link_type'] for r in _db_rows('SELECT * FROM lifecycle_links WHERE confidence>0')}
    assert {'HAS_CONTRACT','RESULTED_IN_CONTRACT'}.issubset(kinds)


def test_contract_keeps_notice_link_but_does_not_guess_ambiguous_award_execution(monkeypatch):
    _seed_notice_and_final(execution='1'); _seed_notice_and_final(execution='2')
    row={'dcsnCntrctNo':'C-1','thtmCntrctAmt':'100','ntceNo':'R26BK00000001000'}
    vnext_store.preserve_raw('contract_service','RAW-1',row)
    result=contract_projection.project_contract_row(row,raw_source_key='RAW-1')
    assert result['linked'] and not result['award_summary_linked'] and result['award_summary_key']==''
    assert all(r['contract_no']=='' for r in _db_rows('SELECT * FROM award_results'))
    kinds={r['link_type'] for r in _db_rows('SELECT * FROM lifecycle_links WHERE confidence>0')}
    assert 'HAS_CONTRACT' in kinds and 'RESULTED_IN_CONTRACT' not in kinds


def test_ambiguous_notice_reference_is_not_guessed(monkeypatch):
    for order in ('000','001'):
        vnext_store.preserve_raw('bid_notice_service','AMBIGUOUS|'+order,{'bidNtceNo':'AMBIGUOUS','bidNtceOrd':order})
    row={'dcsnCntrctNo':'C-AMBIG','ntceNo':'AMBIGUOUS'}
    result=contract_projection.project_contract_row(row,raw_source_key='RAW-1')
    assert result['reason']=='notice_unresolved' and not result['linked']
    assert not _db_rows('SELECT * FROM award_results')
    assert not _db_rows('SELECT * FROM lifecycle_links WHERE confidence>0')

def test_unprocessed_alternate_notice_field_final_award_blocks_contract_assignment():
    first = _seed_notice_and_final(execution="1")
    second_row = {
        "bidNoticeNo": "R26BK00000001",
        "bidNoticeOrd": "000",
        "bidClsfcNo": "2",
        "rbidNo": "0",
        "bidwinnrNm": "다른최종업체",
        "bidwinnrBizno": "1112223334",
        "sucsfbidAmt": "101",
    }
    second_key = award_projection.execution_key(second_row)
    assert second_key == "R26BK00000001|000|2|0"
    vnext_store.preserve_raw(
        "award_result_service",
        second_key,
        second_row,
    )

    # The second official final-award RAW has not been normalized yet, but its
    # accepted alternate notice-number field must already make execution assignment
    # ambiguous. Never keep assigning contracts to the older first execution.
    assert contract_projection.resolve_unique_final_award_key(
        "R26BK00000001|000"
    ) == ""
    assert first != second_key

