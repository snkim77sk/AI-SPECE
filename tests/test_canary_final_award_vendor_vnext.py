import datetime as dt

import award_vnext
import g2b_vnext_canary


def _probe(row):
    fields = ["bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo",
              "bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt", "sucsfbidRate", "rlOpengDt"]

    def fetcher(start, end, page, rows):
        return [row], 1

    return g2b_vnext_canary._probe_one_day(
        fetcher, fields, g2b_vnext_canary._award_shape,
        identity_validator=award_vnext._identity_problem,
        fact_validator=g2b_vnext_canary._award_fact_verified,
        today=dt.date(2026, 9, 17), rows=10, lookback_days=1,
    )


def test_amount_without_final_award_vendor_pair_is_not_conclusive():
    result = _probe({
        "bidNtceNo": "A", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": 0,
        "bidwinnrNm": "", "bidwinnrBizno": "", "sucsfbidAmt": "100",
        "sucsfbidRate": "88", "rlOpengDt": "20260917",
    })
    assert result["identity_verified"] is True
    assert result["schema_verified"] is True
    assert result["fact_verified"] is False
    assert result["conclusive"] is False


def test_name_without_business_number_is_not_conclusive():
    result = _probe({
        "bidNtceNo": "A", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": 0,
        "bidwinnrNm": "업체", "bidwinnrBizno": "", "sucsfbidAmt": "100",
        "sucsfbidRate": "88", "rlOpengDt": "20260917",
    })
    assert result["fact_verified"] is False


def test_final_award_vendor_pair_is_conclusive_even_if_amount_is_optional():
    result = _probe({
        "bidNtceNo": "A", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": 0,
        "bidwinnrNm": "업체", "bidwinnrBizno": "1234567890", "sucsfbidAmt": "",
        "sucsfbidRate": "", "rlOpengDt": "20260917",
    })
    assert result["fact_verified"] is True
    assert result["conclusive"] is True
