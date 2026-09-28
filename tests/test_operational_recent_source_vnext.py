import urllib.parse

import pytest

import vnext_live_gate
import vnext_source_guard


def _shopping_url(day="20260929", page=1):
    params = {
        "serviceKey": "redacted",
        "pageNo": page,
        "numOfRows": 999,
        "type": "json",
        "inqryBgnDate": day,
        "inqryEndDate": day,
    }
    return (
        "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/"
        "getDlvrReqDtlInfoList?" + urllib.parse.urlencode(params)
    )


def test_operational_recent_allows_exact_shopping_day_without_runtime_sha(monkeypatch):
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: __import__("datetime").date(2026, 9, 29))
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "")
    with vnext_source_guard.operational_recent_source_context(
        collection_date="2026-09-29", max_requests=2
    ):
        context = vnext_source_guard.current_source_request_context()
        assert context["mode"] == vnext_source_guard.OPERATIONAL_RECENT
        assert context["source_commit_sha"].startswith("VERSION:")
        assert vnext_source_guard.require_source_request_context(
            g2b_url=_shopping_url()
        ) == vnext_source_guard.OPERATIONAL_RECENT


def test_operational_recent_rejects_wrong_day_and_nonshopping_endpoint(monkeypatch):
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: __import__("datetime").date(2026, 9, 29))
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "")
    with vnext_source_guard.operational_recent_source_context(
        collection_date="2026-09-29", max_requests=3
    ):
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="DATE_SCOPE_MISMATCH",
        ):
            vnext_source_guard.require_source_request_context(
                g2b_url=_shopping_url(day="20260928")
            )
        bad = (
            "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/"
            "getBidPblancListInfoServc?serviceKey=redacted"
        )
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="TARGET_INVALID",
        ):
            vnext_source_guard.require_source_request_context(g2b_url=bad)
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 0


def test_operational_recent_is_limited_to_recent_or_current_days(monkeypatch):
    import datetime as dt

    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 29))
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "")
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="FROM_FUTURE"):
        with vnext_source_guard.operational_recent_source_context(
            collection_date="2026-09-30"
        ):
            pass
    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="TOO_OLD"):
        with vnext_source_guard.operational_recent_source_context(
            collection_date="2026-08-28"
        ):
            pass
