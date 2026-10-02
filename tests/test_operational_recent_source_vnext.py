import datetime as dt
import urllib.parse

import pytest

import vnext_live_gate
import vnext_source_guard


def _shopping_url(day="20261002", page=1, inqry_div="1"):
    params = {
        "serviceKey": "redacted",
        "pageNo": page,
        "numOfRows": 999,
        "type": "json",
        "inqryDiv": inqry_div,
        "inqryBgnDate": day,
        "inqryEndDate": day,
    }
    return (
        "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService/"
        "getDlvrReqDtlInfoList?" + urllib.parse.urlencode(params)
    )


def test_operational_recent_allows_completed_shopping_day_without_runtime_sha(monkeypatch):
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 10, 3))
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "")
    with vnext_source_guard.operational_recent_source_context(
        collection_date="2026-10-02", max_requests=2
    ):
        context = vnext_source_guard.current_source_request_context()
        assert context["mode"] == vnext_source_guard.OPERATIONAL_RECENT
        assert context["source_commit_sha"].startswith("VERSION:")
        assert vnext_source_guard.require_source_request_context(
            g2b_url=_shopping_url()
        ) == vnext_source_guard.OPERATIONAL_RECENT


def test_operational_recent_rejects_wrong_day_wrong_mode_and_nonshopping_endpoint(monkeypatch):
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 10, 3))
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "")
    with vnext_source_guard.operational_recent_source_context(
        collection_date="2026-10-02", max_requests=3
    ):
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="DATE_SCOPE_MISMATCH"):
            vnext_source_guard.require_source_request_context(
                g2b_url=_shopping_url(day="20261001")
            )
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="QUERY_INVALID"):
            vnext_source_guard.require_source_request_context(
                g2b_url=_shopping_url(inqry_div="2")
            )
        bad = (
            "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/"
            "getBidPblancListInfoServc?serviceKey=redacted"
        )
        with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="TARGET_INVALID"):
            vnext_source_guard.require_source_request_context(g2b_url=bad)
        context = vnext_source_guard.current_source_request_context()
        assert context["permits_used"] == 0


def test_operational_recent_is_fixed_to_oct1_through_d_minus_one(monkeypatch):
    monkeypatch.setattr(vnext_source_guard, "_today_kst", lambda: dt.date(2026, 12, 1))
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: "")

    # Oct 1 remains authorized even after it is older than a rolling recent window.
    with vnext_source_guard.operational_recent_source_context(
        collection_date="2026-10-01", max_requests=1
    ):
        assert vnext_source_guard.current_source_request_context()["validation_date_kst"] == "2026-10-01"

    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="NOT_COMPLETED"):
        with vnext_source_guard.operational_recent_source_context(collection_date="2026-12-01"):
            pass

    with pytest.raises(vnext_source_guard.VNextSourceAccessError, match="BEFORE_BOOTSTRAP"):
        with vnext_source_guard.operational_recent_source_context(collection_date="2026-09-30"):
            pass
