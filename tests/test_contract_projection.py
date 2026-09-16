import db
import award_projection
import contract_projection
import vnext_store


def _fresh_db(monkeypatch,tmp_path):
    path=tmp_path/"contract.sqlite3"
    monkeypatch.setattr(db,"DB_PATH",str(path)); db.init_db(); vnext_store.ensure_foundation()


def test_parse_single_contract_party():
    value="[1^단독^^가나다주식회사^홍길동^대한민국^100^^담당자^123-45-67890]"
    assert contract_projection.parse_contract_parties(value)==[{"name":"가나다주식회사","bizno":"1234567890","share":"100"}]


def test_malformed_joint_party_never_collapses_to_single_vendor(monkeypatch):
    seen={}
    monkeypatch.setattr(contract_projection,"_retire_previous_raw_projection",lambda raw:0)
    monkeypatch.setattr(contract_projection,"resolve_notice_key",lambda row:"N|000")
    monkeypatch.setattr(contract_projection,"resolve_unique_final_award_key",lambda notice:"N|000|1|0")
    monkeypatch.setattr(contract_projection,"replace_fact_group",lambda key,group,raw_source_key="",**facts:seen.update(facts))
    monkeypatch.setattr(contract_projection,"save_lifecycle_link",lambda *a,**k:None)
    row={"dcsnCntrctNo":"C","corpList":"[1^대표사^^정상업체^대표^대한민국^60^^담당자^1234567890],[broken]"}
    result=contract_projection.project_contract_row(row,raw_source_key="RAW")
    assert result["party_parse_ambiguous"] is True
    assert result["single_party_projected"] is False
    assert seen["contract_vendor"] == ""


def test_contract_revision_to_unresolved_notice_clears_old_fact_and_retires_links(monkeypatch,tmp_path):
    _fresh_db(monkeypatch,tmp_path)
    notice="N|000"; execution="N|000|1|0"; contract_raw="RAW-C"
    vnext_store.preserve_raw("bid_notice_service",notice,{"bidNtceNo":"N","bidNtceOrd":"000"})
    award_raw="RAW-A"
    vnext_store.preserve_raw("award_result_service",award_raw,
        {"bidNtceNo":"N","bidNtceOrd":"000","bidClsfcNo":"1","rbidNo":"0","bidwinnrNm":"낙찰사","sucsfbidAmt":"100"})
    award_projection.normalize_dataset("award_result_service")
    first_contract={"dcsnCntrctNo":"C1","bidNtceNo":"N","bidNtceOrd":"000","thtmCntrctAmt":"100",
                    "corpList":"[1^단독^^계약사^대표^대한민국^100^^담당자^1234567890]"}
    vnext_store.preserve_raw("contract_service",contract_raw,first_contract)
    contract_projection.normalize_contracts()
    with db.connect() as conn:
        row=conn.execute("SELECT * FROM award_results WHERE source_key=?",(execution,)).fetchone()
        assert row["contract_no"] == "C1"
        assert row["contract_vendor"] == "계약사"
    changed=dict(first_contract,bidNtceNo="UNKNOWN",bidNtceOrd="999")
    vnext_store.preserve_raw("contract_service",contract_raw,changed)
    result=contract_projection.normalize_contracts()
    assert result["unresolved"] == 1
    with db.connect() as conn:
        row=conn.execute("SELECT * FROM award_results WHERE source_key=?",(execution,)).fetchone()
        assert row["contract_no"] == ""
        assert row["contract_vendor"] == ""
        link=conn.execute("SELECT confidence FROM lifecycle_links WHERE from_type='award_summary' AND from_key=? AND to_type='contract' AND to_key='C1' AND link_type='RESULTED_IN_CONTRACT'",(execution,)).fetchone()
        assert link is not None and float(link["confidence"]) == 0.0


def test_second_final_execution_invalidates_contract_until_reprocessed(monkeypatch,tmp_path):
    _fresh_db(monkeypatch,tmp_path)
    notice="N|000"; contract_raw="RAW-C"
    vnext_store.preserve_raw("bid_notice_service",notice,{"bidNtceNo":"N","bidNtceOrd":"000"})
    for rebid in ("0",):
        raw=f"A{rebid}"
        vnext_store.preserve_raw("award_result_service",raw,{"bidNtceNo":"N","bidNtceOrd":"000","bidClsfcNo":"1","rbidNo":rebid,"bidwinnrNm":"낙찰사","sucsfbidAmt":"100"})
    award_projection.normalize_dataset("award_result_service")
    vnext_store.preserve_raw("contract_service",contract_raw,{"dcsnCntrctNo":"C1","bidNtceNo":"N","bidNtceOrd":"000","corpList":"[1^단독^^계약사^대표^대한민국^100^^담당자^1234567890]"})
    contract_projection.normalize_contracts()
    vnext_store.preserve_raw("award_result_service","A1",{"bidNtceNo":"N","bidNtceOrd":"000","bidClsfcNo":"1","rbidNo":"1","bidwinnrNm":"재낙찰","sucsfbidAmt":"101"})
    award_projection.normalize_dataset("award_result_service")
    with db.connect() as conn:
        raw=conn.execute("SELECT normalized_at FROM raw_records WHERE dataset='contract_service' AND source_key=?",(contract_raw,)).fetchone()
        assert raw["normalized_at"] == ""
    contract_projection.normalize_contracts()
    with db.connect() as conn:
        rows=conn.execute("SELECT contract_no FROM award_results WHERE notice_no='N'").fetchall()
        assert all(row["contract_no"] == "" for row in rows)
