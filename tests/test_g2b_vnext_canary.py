import datetime as dt
import json

import g2b_vnext_canary
import shopping_vnext


def test_canary_summaries_never_emit_raw_vendor_or_business_values():
    opening_rows = [{
        "bidNtceNo": "R26BK00000001",
        "bidNtceOrd": "000",
        "opengCorpInfo": "비밀업체^1234567890^대표자^100000000^88.12",
        "progrsDivCdNm": "개찰완료",
    }]
    contract_rows = [{
        "dcsnCntrctNo": "R26TA0000000100",
        "ntceNo": "R26BK00000001000",
        "corpList": "[1^단독^단독^비밀업체^대표자^대한민국^100^^담당자^1234567890]",
    }]

    opening = g2b_vnext_canary.summarize_rows(
        opening_rows,
        ["bidNtceNo", "opengCorpInfo", "progrsDivCdNm"],
        g2b_vnext_canary._opening_shape,
    )
    contract = g2b_vnext_canary.summarize_rows(
        contract_rows,
        ["dcsnCntrctNo", "ntceNo", "corpList"],
        g2b_vnext_canary._contract_shape,
    )
    text = json.dumps({"opening": opening, "contract": contract}, ensure_ascii=False)

    assert "비밀업체" not in text
    assert "1234567890" not in text
    assert "R26TA0000000100" not in text
    assert "R26BK00000001000" not in text
    assert opening["shape"]["parser_cases"] == {"single": 1}
    assert opening["shape"]["conservative_first_rank_candidates"] == 1
    assert contract["shape"]["ntceNo_length_counts"] == {16: 1}
    assert contract["shape"]["parsed_party_count_distribution"] == {1: 1}


def test_probe_uses_separate_one_day_windows_and_stops_at_first_nonempty_day():
    calls = []

    def fetcher(start, end, page, rows):
        calls.append((start, end, page, rows))
        if start == "2026-09-14":
            return [{"bidNtceNo": "X"}], 1
        return [], 0

    result = g2b_vnext_canary._probe_one_day(
        fetcher,
        ["bidNtceNo"],
        today=dt.date(2026, 9, 16),
        rows=100,
        lookback_days=7,
    )

    assert calls == [
        ("2026-09-16", "2026-09-16", 1, 100),
        ("2026-09-15", "2026-09-15", 1, 100),
        ("2026-09-14", "2026-09-14", 1, 100),
    ]
    assert result["selected_day"] == "2026-09-14"
    assert result["conclusive"] is True


def test_probe_can_share_collector_identity_validator_for_aliases():
    def fetcher(start, end, page, rows):
        return [{"deliveryReqNo": "REQ-1", "dlvrReqDtlSeq": 0}], 1

    result = g2b_vnext_canary._probe_one_day(
        fetcher,
        ["dlvrReqNo", "deliveryReqNo", "reqNo", "prdctSno", "dlvrReqDtlSeq"],
        required_fields=[],
        required_any_groups=(
            ("dlvrReqNo", "deliveryReqNo", "reqNo"),
            ("prdctSno", "dlvrReqDtlSeq", "dlvrReqDtlSn", "detailSeq", "seq"),
        ),
        identity_validator=shopping_vnext._identity_problem,
        today=dt.date(2026, 9, 16), rows=10, lookback_days=1,
    )

    assert result["identity_verified"] is True
    assert result["schema_verified"] is True
    assert result["conclusive"] is True


def test_probe_rejects_alias_row_when_collector_identity_is_incomplete():
    def fetcher(start, end, page, rows):
        return [{"deliveryReqNo": "REQ-1"}], 1

    result = g2b_vnext_canary._probe_one_day(
        fetcher,
        ["deliveryReqNo", "dlvrReqDtlSeq"],
        required_fields=[],
        required_any_groups=(
            ("dlvrReqNo", "deliveryReqNo", "reqNo"),
            ("prdctSno", "dlvrReqDtlSeq", "dlvrReqDtlSn", "detailSeq", "seq"),
        ),
        identity_validator=shopping_vnext._identity_problem,
        today=dt.date(2026, 9, 16), rows=10, lookback_days=1,
    )

    assert result["identity_verified"] is False
    assert result["schema_verified"] is False
    assert result["conclusive"] is False


def test_run_canary_includes_shopping_delivery_probe(monkeypatch):
    monkeypatch.setattr(g2b_vnext_canary.db, "init_db", lambda: None)

    def simple_row(*args, **kwargs):
        return [{"x": "1"}], 1

    monkeypatch.setattr(g2b_vnext_canary.bid_vnext, "fetch_page", simple_row)
    monkeypatch.setattr(g2b_vnext_canary.award_vnext, "fetch_page", simple_row)
    monkeypatch.setattr(g2b_vnext_canary.contract_vnext, "fetch_page", simple_row)
    monkeypatch.setattr(g2b_vnext_canary.shopping_vnext, "fetch_page", simple_row)

    report = g2b_vnext_canary.run_canary(
        today=dt.date(2026, 9, 16), rows=10, lookback_days=1
    )

    assert report["probe_count"] == 6
    assert "shopping_delivery" in report["probes"]
    assert report["probes"]["shopping_delivery"]["conclusive"] is False
    assert report["status"] != "CONCLUSIVE"
