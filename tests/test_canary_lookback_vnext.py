import datetime as dt

import award_vnext
import g2b_vnext_canary


FIELDS = [
    "bidNtceNo", "bidNtceOrd", "bidClsfcNo", "rbidNo",
    "bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt", "sucsfbidRate", "rlOpengDt",
]


def _award_probe(fetcher, lookback_days=3):
    return g2b_vnext_canary._probe_one_day(
        fetcher,
        FIELDS,
        g2b_vnext_canary._award_shape,
        identity_validator=award_vnext._identity_problem,
        fact_validator=g2b_vnext_canary._award_fact_verified,
        today=dt.date(2026, 9, 17),
        rows=10,
        lookback_days=lookback_days,
    )


def test_partial_nonempty_day_does_not_hide_older_conclusive_sample():
    calls = []

    def fetcher(start, end, page, rows):
        calls.append(start)
        if start == "2026-09-17":
            return [{
                "bidNtceNo": "A", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": 0,
                "bidwinnrNm": "", "bidwinnrBizno": "", "sucsfbidAmt": "100",
                "sucsfbidRate": "88", "rlOpengDt": "20260917",
            }], 1
        if start == "2026-09-16":
            return [{
                "bidNtceNo": "B", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": 0,
                "bidwinnrNm": "업체", "bidwinnrBizno": "1234567890", "sucsfbidAmt": "200",
                "sucsfbidRate": "88", "rlOpengDt": "20260916",
            }], 1
        return [], 0

    result = _award_probe(fetcher)

    assert calls == ["2026-09-17", "2026-09-16"]
    assert result["selected_day"] == "2026-09-16"
    assert result["conclusive"] is True
    assert result["nonempty_days_seen"] == 2
    assert result["attempts"][0]["conclusive"] is False
    assert result["attempts"][1]["conclusive"] is True


def test_latest_partial_is_returned_if_no_conclusive_sample_exists():
    calls = []

    def fetcher(start, end, page, rows):
        calls.append(start)
        return [{
            "bidNtceNo": start, "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": 0,
            "bidwinnrNm": "", "bidwinnrBizno": "", "sucsfbidAmt": "100",
            "sucsfbidRate": "88", "rlOpengDt": start.replace("-", ""),
        }], 1

    result = _award_probe(fetcher)

    assert calls == ["2026-09-17", "2026-09-16", "2026-09-15"]
    assert result["selected_day"] == "2026-09-17"
    assert result["conclusive"] is False
    assert result["fact_verified"] is False
    assert result["nonempty_days_seen"] == 3
    assert len(result["attempts"]) == 3


def test_transport_error_after_partial_sample_remains_fail_closed():
    def fetcher(start, end, page, rows):
        if start == "2026-09-17":
            return [{
                "bidNtceNo": "A", "bidNtceOrd": "000", "bidClsfcNo": "1", "rbidNo": 0,
                "bidwinnrNm": "", "bidwinnrBizno": "", "sucsfbidAmt": "100",
                "sucsfbidRate": "88", "rlOpengDt": "20260917",
            }], 1
        raise RuntimeError("temporary source error")

    result = _award_probe(fetcher)

    assert result["status"] == "ERROR"
    assert result["conclusive"] is False
    assert result["nonempty_days_seen"] == 1
    assert result["attempts"][-1]["request_failed"] is True
