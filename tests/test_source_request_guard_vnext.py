import datetime as dt
import urllib.parse

import pytest

import lofin_vnext_http
import vnext_http
import vnext_live_gate
import vnext_source_guard


def _small_context(monkeypatch, *, date="2026-09-16", max_requests=2):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "b" * 40)
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 17))
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda path: {"source_commit_sha": "b" * 40},
    )
    return vnext_source_guard.small_validation_source_context(
        "approval.json", validation_date=date, max_requests=max_requests
    )


def _g2b_url(start="20260916", end="20260916", *, page=1):
    params = {
        "serviceKey": "redacted",
        "pageNo": page,
        "numOfRows": 999,
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDate": start,
        "inqryEndDate": end,
    }
    return (
        "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/"
        "getDlvrReqDtlInfoList?" + urllib.parse.urlencode(params)
    )


def _lofin_params(date="20260916"):
    return {
        "Key": "redacted",
        "Type": "json",
        "pIndex": 1,
        "pSize": 1000,
        "fyr": 2026,
        "exe_ymd": date,
        "dbiz_nm": "",
    }


def test_g2b_direct_request_is_blocked_before_quota_or_network(monkeypatch):
    monkeypatch.setattr(vnext_http, "_quota_take", lambda *a, **k: (_ for _ in ()).throw(AssertionError("quota must not be touched")))
    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")))
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_REQUIRED"):
        vnext_http.request("https://example.invalid", "bid_notice", retries=1)


def test_lofin_direct_request_is_blocked_before_quota_or_network(monkeypatch):
    monkeypatch.setattr(lofin_vnext_http, "_quota_take", lambda: (_ for _ in ()).throw(AssertionError("quota must not be touched")))
    monkeypatch.setattr(lofin_vnext_http.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")))
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_REQUIRED"):
        lofin_vnext_http._request({"Key": "redacted"}, retries=1)


def test_source_mode_requires_active_matching_context():
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="CONTEXT_REQUIRED"):
        vnext_source_guard.require_source_request_mode(vnext_source_guard.SMALL_VALIDATION)


def test_bounded_canary_context_has_hard_attempt_budget_and_resets(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "a" * 40)
    monkeypatch.setattr(
        vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 17)
    )
    with vnext_source_guard.bounded_canary_source_context(
        validation_date="2026-09-16",
        max_requests=2,
    ):
        assert vnext_source_guard.require_source_request_mode(
            vnext_source_guard.BOUNDED_CANARY
        ) == vnext_source_guard.BOUNDED_CANARY
        assert vnext_source_guard.require_source_request_context(
            g2b_url=_g2b_url()
        ) == vnext_source_guard.BOUNDED_CANARY
        assert vnext_source_guard.require_source_request_context(
            lofin_params=_lofin_params()
        ) == vnext_source_guard.BOUNDED_CANARY
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 2
        assert context["requests_used"] == 0
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="BUDGET_EXHAUSTED",
        ):
            vnext_source_guard.require_source_request_context(
                g2b_url=_g2b_url(page=2)
            )
    assert vnext_source_guard.current_source_request_context() is None
    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="CONTEXT_REQUIRED",
    ):
        vnext_source_guard.require_source_request_context()


def test_bounded_canary_rejects_removed_service_target_before_budget_consumption(
    monkeypatch,
):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "a" * 40)
    monkeypatch.setattr(
        vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 17)
    )
    params = {
        "serviceKey": "redacted",
        "pageNo": 1,
        "numOfRows": 10,
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDt": "202609160000",
        "inqryEndDt": "202609162359",
    }
    service_url = (
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/"
        "getBidPblancListInfoServc?" + urllib.parse.urlencode(params)
    )
    with vnext_source_guard.bounded_canary_source_context(
        validation_date="2026-09-16",
        max_requests=2,
    ):
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="BOUNDED_CANARY_G2B_TARGET_INVALID",
        ):
            vnext_source_guard.require_source_request_context(
                g2b_url=service_url
            )
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 0
        assert context["requests_used"] == 0


def test_small_validation_context_requires_same_commit_date_and_cannot_be_reused(monkeypatch):
    with _small_context(monkeypatch, max_requests=1):
        context = vnext_source_guard.current_source_request_context()
        assert context["validation_date_kst"] == "2026-09-16"
        assert vnext_source_guard.require_source_request_mode(vnext_source_guard.SMALL_VALIDATION) == vnext_source_guard.SMALL_VALIDATION
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="MODE_MISMATCH"):
            vnext_source_guard.require_source_request_mode(vnext_source_guard.APPROVED_HISTORICAL)
        assert vnext_source_guard.require_source_request_context(g2b_url=_g2b_url()) == vnext_source_guard.SMALL_VALIDATION
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 1
        assert context["requests_used"] == 0
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="BUDGET_EXHAUSTED"):
            vnext_source_guard.require_source_request_context(g2b_url=_g2b_url(page=2))


def test_small_validation_context_rejects_missing_or_invalid_date(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "b" * 40)
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda path: {"source_commit_sha": "b" * 40},
    )
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="DATE_REQUIRED"):
        with vnext_source_guard.small_validation_source_context(
            "approval.json", validation_date="", max_requests=1
        ):
            pass
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="DATE_INVALID"):
        with vnext_source_guard.small_validation_source_context(
            "approval.json", validation_date="not-a-date", max_requests=1
        ):
            pass


def test_small_validation_context_rejects_current_and_stale_dates(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "b" * 40)
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 17))
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda path: {"source_commit_sha": "b" * 40},
    )
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="DATE_NOT_COMPLETED"):
        with vnext_source_guard.small_validation_source_context(
            "approval.json", validation_date="2026-09-17", max_requests=1
        ):
            pass
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="DATE_TOO_OLD"):
        with vnext_source_guard.small_validation_source_context(
            "approval.json", validation_date="2026-09-09", max_requests=1
        ):
            pass
    with vnext_source_guard.small_validation_source_context(
        "approval.json", validation_date="2026-09-10", max_requests=1
    ):
        assert vnext_source_guard.current_source_request_context()["validation_date_kst"] == "2026-09-10"


def test_small_validation_context_rejects_commit_mismatch(monkeypatch):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "c" * 40)
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 17))
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda path: {"source_commit_sha": "d" * 40},
    )
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="SOURCE_SHA_MISMATCH"):
        with vnext_source_guard.small_validation_source_context(
            "approval.json", validation_date="2026-09-16", max_requests=1
        ):
            pass


def test_small_validation_g2b_broad_date_is_blocked_before_quota_or_network(monkeypatch):
    monkeypatch.setattr(vnext_http, "_quota_take", lambda *a, **k: (_ for _ in ()).throw(AssertionError("quota must not be touched")))
    monkeypatch.setattr(vnext_http.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")))
    with _small_context(monkeypatch, max_requests=2):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="G2B_DATE_SCOPE_MISMATCH"):
            vnext_http.request(
                _g2b_url(start="20260901", end="20260916"),
                "shopping_delivery",
                retries=1,
            )
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 0
        assert context["permits_used"] == 0


def test_small_validation_goods_bid_endpoint_is_not_allowlisted(monkeypatch):
    params = {
        "serviceKey": "redacted",
        "pageNo": 1,
        "numOfRows": 999,
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDt": "202609160000",
        "inqryEndDt": "202609162359",
    }
    url = (
        "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/"
        "getBidPblancListInfoThng?" + urllib.parse.urlencode(params)
    )
    with _small_context(monkeypatch, max_requests=1):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="G2B_TARGET_INVALID"):
            vnext_source_guard.require_source_request_context(g2b_url=url)



def test_small_validation_g2b_unknown_target_is_blocked_before_budget_consumption(monkeypatch):
    with _small_context(monkeypatch, max_requests=2):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="G2B_TARGET_INVALID"):
            vnext_source_guard.require_source_request_context(
                g2b_url="https://apis.data.go.kr/1230000/other?serviceKey=redacted"
            )
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 0
        assert context["permits_used"] == 0


def test_small_validation_retained_shopping_operation_is_exactly_allowlisted(monkeypatch):
    with _small_context(monkeypatch, max_requests=1):
        assert vnext_source_guard.require_source_request_context(
            g2b_url=_g2b_url()
        ) == vnext_source_guard.SMALL_VALIDATION
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 1
        assert context["requests_used"] == 0


@pytest.mark.parametrize(
    "path",
    [
        "/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc",
        "/1230000/as/ScsbidInfoService/getOpengResultListInfoServc",
        "/1230000/as/ScsbidInfoService/getScsbidListSttusServc",
        "/1230000/ao/CntrctInfoService/getCntrctInfoListServc",
    ],
)
def test_small_validation_removed_no1_operations_are_not_allowlisted(
    monkeypatch, path
):
    params = {
        "serviceKey": "redacted",
        "pageNo": 1,
        "numOfRows": 999,
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDt": "202609160000",
        "inqryEndDt": "202609162359",
    }
    url = "https://apis.data.go.kr" + path + "?" + urllib.parse.urlencode(params)
    with _small_context(monkeypatch, max_requests=1):
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="G2B_TARGET_INVALID",
        ):
            vnext_source_guard.require_source_request_context(g2b_url=url)



def test_small_validation_lofin_snapshot_and_prefilter_are_scope_bound(monkeypatch):
    with _small_context(monkeypatch, max_requests=3):
        assert vnext_source_guard.require_source_request_context(
            lofin_params=_lofin_params()
        ) == vnext_source_guard.SMALL_VALIDATION
        wrong_day = _lofin_params("20260915")
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="LOFIN_DATE_SCOPE_MISMATCH"):
            vnext_source_guard.require_source_request_context(lofin_params=wrong_day)
        filtered = _lofin_params()
        filtered["dbiz_nm"] = "LED"
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="LOFIN_PREFILTER_FORBIDDEN"):
            vnext_source_guard.require_source_request_context(lofin_params=filtered)
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 1
        assert context["requests_used"] == 0


def test_small_validation_lofin_transport_broad_snapshot_is_blocked_before_quota_or_network(monkeypatch):
    monkeypatch.setattr(lofin_vnext_http, "_quota_take", lambda: (_ for _ in ()).throw(AssertionError("quota must not be touched")))
    monkeypatch.setattr(lofin_vnext_http.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network must not be touched")))
    with _small_context(monkeypatch, max_requests=2):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="LOFIN_DATE_SCOPE_MISMATCH"):
            lofin_vnext_http._request(_lofin_params("20260915"), retries=1)
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 0
        assert context["permits_used"] == 0


def test_operational_recent_starts_on_2026_01_01(monkeypatch):
    monkeypatch.setattr(
        vnext_source_guard, "_today_kst", lambda: dt.date(2026, 10, 2)
    )

    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="BEFORE_BOOTSTRAP",
    ):
        with vnext_source_guard.operational_recent_source_context(
            collection_date="2025-12-31",
            max_requests=1,
        ):
            pass

    with vnext_source_guard.operational_recent_source_context(
        collection_date="2026-01-01",
        max_requests=1,
    ):
        assert vnext_source_guard.require_source_request_context(
            g2b_url=_g2b_url(start="20260101", end="20260101")
        ) == vnext_source_guard.OPERATIONAL_RECENT


def test_operational_budget_allows_2026_jan1_history_within_365_days(monkeypatch):
    monkeypatch.setattr(
        vnext_source_guard,
        "_today_kst",
        lambda: dt.date(2026, 10, 2),
    )

    with vnext_source_guard.operational_budget_source_context(
        snapshot_date="2026-01-01",
        max_requests=1,
    ):
        params = {
            "__service_code": "QWGJK",
            "Key": "redacted",
            "Type": "json",
            "pIndex": 1,
            "pSize": 1000,
            "fyr": 2026,
            "exe_ymd": "20260101",
            "dbiz_nm": "",
        }
        vnext_source_guard._validate_operational_budget_lofin_params(
            params, "2026-01-01"
        )

    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="DATE_TOO_OLD",
    ):
        with vnext_source_guard.operational_budget_source_context(
            snapshot_date="2025-10-01",
            max_requests=1,
        ):
            pass


def test_match_backfill_shopping_is_2025_only(monkeypatch):
    monkeypatch.setattr(
        vnext_source_guard, "_today_kst", lambda: dt.date(2026, 10, 4)
    )

    with vnext_source_guard.match_backfill_shopping_source_context(
        collection_date="2025-06-15",
        max_requests=1,
    ):
        assert vnext_source_guard.require_source_request_context(
            g2b_url=_g2b_url(start="20250615", end="20250615")
        ) == vnext_source_guard.MATCH_BACKFILL_SHOPPING

    for disallowed in ("2024-12-31", "2026-01-01"):
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="YEAR_NOT_ALLOWED",
        ):
            with vnext_source_guard.match_backfill_shopping_source_context(
                collection_date=disallowed,
                max_requests=1,
            ):
                pass


def test_match_backfill_budget_allows_only_2025_qwgjk():
    params = {
        "__service_code": "QWGJK",
        "Key": "redacted",
        "Type": "json",
        "pIndex": 1,
        "pSize": 1000,
        "fyr": 2025,
        "exe_ymd": "20251231",
        "dbiz_nm": "",
    }
    with vnext_source_guard.match_backfill_budget_source_context(
        snapshot_date="2025-12-31",
        max_requests=1,
    ):
        assert vnext_source_guard.require_source_request_context(
            lofin_params=params
        ) == vnext_source_guard.MATCH_BACKFILL_BUDGET

    aidfa = dict(params, __service_code="AIDFA")
    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="QWGJK_ONLY",
    ):
        vnext_source_guard._validate_match_backfill_budget_lofin_params(
            aidfa, "2025-12-31"
        )

    wrong_year = dict(params, fyr=2026, exe_ymd="20261231")
    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="YEAR_NOT_ALLOWED",
    ):
        vnext_source_guard._validate_match_backfill_budget_lofin_params(
            wrong_year, "2025-12-31"
        )


def test_operational_budget_allows_next_year_aidfa_but_not_future_qwgjk():
    aidfa = {
        "__service_code": "AIDFA",
        "Key": "redacted",
        "Type": "json",
        "pIndex": 1,
        "pSize": 1000,
        "fyr": 2027,
    }
    vnext_source_guard._validate_operational_budget_lofin_params(
        aidfa, "2026-10-02"
    )

    too_far = dict(aidfa, fyr=2028)
    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="YEAR_MISMATCH",
    ):
        vnext_source_guard._validate_operational_budget_lofin_params(
            too_far, "2026-10-02"
        )

    qwgjk = {
        "__service_code": "QWGJK",
        "Key": "redacted",
        "Type": "json",
        "pIndex": 1,
        "pSize": 1000,
        "fyr": 2027,
        "exe_ymd": "20261002",
        "dbiz_nm": "",
    }
    with pytest.raises(
        vnext_source_guard.VNextSourceAccessError,
        match="YEAR_MISMATCH",
    ):
        vnext_source_guard._validate_operational_budget_lofin_params(
            qwgjk, "2026-10-02"
        )
