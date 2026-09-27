import datetime as dt
import json

import pytest

import budget_appropriation_vnext
import budget_vnext
import lofin_vnext_http
import vnext_live_gate
import vnext_source_guard


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def _lofin_payload(service_code, row):
    return json.dumps({
        service_code: [{
            "head": [
                {"list_total_count": 1},
                {"RESULT": {"CODE": "INFO-000", "MESSAGE": "OK"}},
            ],
            "row": [row],
        }]
    }).encode("utf-8")


def _prepare_bounded(monkeypatch, service_code, row, sha_char):
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: sha_char * 40)
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "synthetic-key")
    monkeypatch.setattr(lofin_vnext_http, "_quota_take", lambda: 1)
    calls = []

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        return _Response(_lofin_payload(service_code, row))

    monkeypatch.setattr(lofin_vnext_http.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_qwgjk_wrapper_records_exactly_one_attested_transport_success(monkeypatch):
    row = {
        "fyr": "2026",
        "exe_ymd": "20260924",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "D1",
        "dbiz_cd": "P1",
        "acnt_dv_cd": "A1",
    }
    calls = _prepare_bounded(
        monkeypatch, lofin_vnext_http.SERVICE_CODE, row, "q"
    )

    with vnext_source_guard.bounded_canary_source_context(max_requests=2):
        before = vnext_source_guard.current_source_request_context()
        result = budget_vnext.fetch_page(
            2026, "2026-09-24", page=1, size=10, region_code="4100000"
        )
        bound = vnext_source_guard.require_attested_transport_result(
            before,
            result,
            error_code="QWGJK_TEST_TRANSPORT_NOT_ATTESTED",
        )
        context = vnext_source_guard.current_source_request_context()

        assert bound == ([row], 1)
        assert context["requests_used"] == 1
        assert context["permits_used"] == 1
        assert context["transport_successes_used"] == 1

    assert len(calls) == 1


def test_aidfa_low_level_transport_records_exactly_one_attested_success(monkeypatch):
    row = {
        "fyr": "2026",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "fld_cd": "F1",
        "sect_cd": "S1",
        "acnt_dv_cd": "A1",
        "biz_bdg_tott_amt": "5000",
    }
    calls = _prepare_bounded(
        monkeypatch, lofin_vnext_http.APPROPRIATION_SERVICE_CODE, row, "a"
    )

    with vnext_source_guard.bounded_canary_source_context(max_requests=2):
        before = vnext_source_guard.current_source_request_context()
        result = budget_appropriation_vnext.fetch_page(
            2026, region_code="4100000", page=1, size=10
        )
        bound = vnext_source_guard.require_attested_transport_result(
            before,
            result,
            error_code="AIDFA_TEST_TRANSPORT_NOT_ATTESTED",
        )
        context = vnext_source_guard.current_source_request_context()

        assert bound == ([row], 1)
        assert context["requests_used"] == 1
        assert context["permits_used"] == 1
        assert context["transport_successes_used"] == 1

    assert len(calls) == 1


def test_qwgjk_small_validation_context_cannot_be_reused_for_aidfa(monkeypatch):
    source_sha = "s" * 40
    monkeypatch.setattr(vnext_live_gate, "runtime_source_sha", lambda: source_sha)
    monkeypatch.setattr(
        vnext_live_gate,
        "require_canary_approval",
        lambda value: {"source_commit_sha": source_sha},
    )
    monkeypatch.setattr(
        vnext_source_guard, "_today_kst", lambda: dt.date(2026, 9, 25)
    )
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "synthetic-key")
    network = []
    monkeypatch.setattr(
        lofin_vnext_http.urllib.request,
        "urlopen",
        lambda *a, **k: network.append("NETWORK"),
    )

    with vnext_source_guard.small_validation_source_context(
        "synthetic-canary.json",
        validation_date="2026-09-24",
        max_requests=2,
    ):
        with pytest.raises(
            vnext_source_guard.VNextSourceAccessError,
            match="VNEXT_SMALL_VALIDATION_LOFIN_QUERY_INVALID",
        ):
            budget_appropriation_vnext.fetch_page(
                2026, region_code="4100000", page=1, size=10
            )
        context = vnext_source_guard.current_source_request_context()
        assert context["requests_used"] == 0
        assert context["permits_used"] == 0
        assert context["transport_successes_used"] == 0

    assert network == []
