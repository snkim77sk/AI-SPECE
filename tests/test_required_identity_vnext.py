import db

import award_vnext
import bid_vnext
import contract_vnext
import g2b_vnext_canary
import shopping_vnext


def _raw_count(dataset):
    with db.connect() as conn:
        return int(conn.execute("SELECT COUNT(*) n FROM raw_records WHERE dataset=?", (dataset,)).fetchone()["n"])


def test_bid_missing_identity_is_preserved_but_collection_fails_closed(monkeypatch):
    rows = [{"bidNtceNm": "식별자 없는 공고"}]
    monkeypatch.setattr(bid_vnext, "fetch_page", lambda *a, **k: (rows, 1))
    result = bid_vnext.collect_all("goods", "2026-09-01", "2026-09-01")
    assert result["complete"] is False
    assert result["status"] == "INCOMPLETE"
    assert result["reason"] == "MISSING_BID_NOTICE_IDENTITY"
    assert _raw_count("bid_notice_goods") == 1


def test_award_missing_execution_identity_is_preserved_but_incomplete(monkeypatch):
    rows = [{"bidNtceNo": "A", "bidNtceOrd": "00", "bidwinnrNm": "업체"}]
    monkeypatch.setattr(award_vnext, "fetch_page", lambda *a, **k: (rows, 1))
    result = award_vnext.collect_service_awards("2026-09-01", "2026-09-01")
    assert result["complete"] is False
    assert result["reason"] == "MISSING_AWARD_EXECUTION_IDENTITY"
    assert _raw_count("award_result_service") == 1


def test_contract_missing_contract_identity_is_preserved_but_incomplete(monkeypatch):
    rows = [{"ntceNo": "A", "corpList": "[]"}]
    monkeypatch.setattr(contract_vnext, "fetch_page", lambda *a, **k: (rows, 1))
    result = contract_vnext.collect_all("2026-09-01", "2026-09-01")
    assert result["complete"] is False
    assert result["reason"] == "MISSING_CONTRACT_IDENTITY"
    assert _raw_count("contract_service") == 1


def test_contract_unified_only_identity_is_preserved_but_incomplete(monkeypatch):
    rows = [{"untyCntrctNo": "U-1", "ntceNo": "A", "corpList": "[]"}]
    monkeypatch.setattr(contract_vnext, "fetch_page", lambda *a, **k: (rows, 1))
    result = contract_vnext.collect_all("2026-09-01", "2026-09-01")
    assert result["complete"] is False
    assert result["reason"] == "MISSING_CONTRACT_IDENTITY"
    assert _raw_count("contract_service") == 1


def test_contract_decided_number_is_canonical_identity():
    before = {"dcsnCntrctNo": "C-1"}
    after = {"untyCntrctNo": "U-1", "dcsnCntrctNo": "C-1"}
    assert contract_vnext._identity_problem(before) == ""
    assert contract_vnext._identity_problem(after) == ""
    assert contract_vnext._source_key(before) == "C-1"
    assert contract_vnext._source_key(after) == "C-1"


def test_shopping_missing_detail_identity_is_preserved_but_incomplete(monkeypatch):
    rows = [{"dlvrReqNo": "REQ-1", "prdctNm": "일반제품"}]
    monkeypatch.setattr(shopping_vnext, "fetch_page", lambda *a, **k: (rows, 1))
    result = shopping_vnext.collect_all("2026-09-01", "2026-09-01")
    assert result["complete"] is False
    assert result["reason"] == "MISSING_SHOPPING_DELIVERY_IDENTITY"
    assert _raw_count("shopping_delivery") == 1


def test_numeric_zero_is_valid_execution_and_detail_identity():
    award = {"bidNtceNo": "A", "bidNtceOrd": 0, "bidClsfcNo": 0, "rbidNo": 0}
    assert award_vnext._identity_problem(award) == ""
    assert award_vnext._raw_source_key(award) == "A|0|0|0"

    bid = {"bidNtceNo": "A", "bidNtceOrd": 0}
    assert bid_vnext._identity_problem(bid) == ""
    assert bid_vnext._source_key(bid) == "A|0"

    shopping = {"dlvrReqNo": "REQ", "prdctSno": 0}
    assert shopping_vnext._identity_problem(shopping) == ""


def test_canary_award_probe_requires_execution_and_rebid_identity():
    def missing_execution(start, end, page, rows):
        return [{
            "bidNtceNo": "A", "bidNtceOrd": "00",
            "bidwinnrNm": "업체", "bidwinnrBizno": "123",
            "sucsfbidAmt": "100", "sucsfbidRate": "88", "rlOpengDt": "20260901",
        }], 1

    result = g2b_vnext_canary._probe_one_day(
        missing_execution,
        ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo", "bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt", "sucsfbidRate", "rlOpengDt"],
        today=__import__("datetime").date(2026, 9, 1), rows=10, lookback_days=1,
    )
    assert result["conclusive"] is False
    assert result["schema_verified"] is False


def test_canary_award_probe_accepts_complete_execution_identity():
    def complete_execution(start, end, page, rows):
        return [{
            "bidNtceNo": "A", "bidNtceOrd": "00", "bidClsfcNo": "1", "rbidNo": 0,
            "bidwinnrNm": "업체", "bidwinnrBizno": "123",
            "sucsfbidAmt": "100", "sucsfbidRate": "88", "rlOpengDt": "20260901",
        }], 1

    result = g2b_vnext_canary._probe_one_day(
        complete_execution,
        ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo", "bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt", "sucsfbidRate", "rlOpengDt"],
        today=__import__("datetime").date(2026, 9, 1), rows=10, lookback_days=1,
    )
    assert result["conclusive"] is True
    assert result["schema_verified"] is True


def test_canary_contract_requires_decided_contract_number_not_optional_unified_number():
    def unified_only(start, end, page, rows):
        return [{
            "untyCntrctNo": "U-1", "ntceNo": "A", "thtmCntrctAmt": "100",
            "corpList": "업체^123", "cntrctCnclsDate": "20260901",
        }], 1

    result = g2b_vnext_canary._probe_one_day(
        unified_only,
        ["dcsnCntrctNo", "ntceNo", "thtmCntrctAmt", "corpList", "cntrctCnclsDate"],
        today=__import__("datetime").date(2026, 9, 1), rows=10, lookback_days=1,
    )
    assert result["conclusive"] is False
    assert result["schema_verified"] is False
