import json

import db
import budget_appropriation_vnext
import budget_projection_vnext
import education_budget_vnext
import lofin_vnext_http
from vnext_store import preserve_raw


def test_lofin_parser_supports_aidfa_envelope():
    payload = {
        "AIDFA": [{
            "head": [
                {"list_total_count": 2},
                {"RESULT": {"CODE": "INFO-000", "MESSAGE": "OK"}},
            ],
            "row": [
                {"fyr": "2026", "wa_laf_cd": "1100000", "fld_cd": "01", "fld_nm": "일반공공행정"},
                {"fyr": "2026", "wa_laf_cd": "1100000", "fld_cd": "02", "fld_nm": "공공질서및안전"},
            ],
        }]
    }
    rows, total, code, _ = lofin_vnext_http.parse_response(
        json.dumps(payload).encode(), service_code="AIDFA"
    )
    assert len(rows) == 2
    assert total == 2
    assert code == "INFO-000"


def test_appropriation_collection_preserves_every_row_without_keyword_filter(monkeypatch):
    calls = []
    pages = {
        1: ([
            {"fyr": "2026", "wa_laf_cd": "1100000", "fld_cd": "01", "sect_cd": "1", "fld_nm": "일반행정", "biz_bdg_tott_amt": "100"},
            {"fyr": "2026", "wa_laf_cd": "1100000", "fld_cd": "02", "sect_cd": "1", "fld_nm": "도로조명", "biz_bdg_tott_amt": "200"},
        ], 3, "INFO-000", ""),
        2: ([
            {"fyr": "2026", "wa_laf_cd": "1100000", "fld_cd": "03", "sect_cd": "1", "fld_nm": "복지", "biz_bdg_tott_amt": "300"},
        ], 3, "INFO-000", ""),
    }

    def fake_fetch(year, region_code="", page=1, size=1000, **kwargs):
        calls.append((year, region_code, page, size))
        return pages[page]

    monkeypatch.setattr(budget_appropriation_vnext, "fetch_appropriation_page", fake_fetch)
    result = budget_appropriation_vnext.collect_full_appropriation(
        2026, region_code="1100000", page_size=2, resume=False
    )
    assert result["complete"] is True
    assert result["fetched"] == result["saved"] == 3
    assert [call[1] for call in calls] == ["1100000", "1100000"]
    with db.connect() as conn:
        payloads = [json.loads(row["payload_json"]) for row in conn.execute(
            "SELECT payload_json FROM raw_records WHERE dataset='budget_appropriation' ORDER BY id"
        )]
    assert [row["fld_nm"] for row in payloads] == ["일반행정", "도로조명", "복지"]


def test_education_collection_preserves_nonlighting_rows_before_classification(monkeypatch):
    pages = {
        1: ([
            {"YMQ": "2026", "officeCode": "B10", "projectCode": "P1", "사업명": "교실 냉난방 개선", "예산액": "1000"},
            {"YMQ": "2026", "officeCode": "B10", "projectCode": "P2", "사업명": "학교 LED 조명 개선", "예산액": "2000"},
        ], 2),
    }
    monkeypatch.setattr(education_budget_vnext.legacy, "get_request_type", lambda: "opclTotal")
    monkeypatch.setattr(
        education_budget_vnext,
        "fetch_page",
        lambda year, page=1, size=1000: pages[page],
    )
    result = education_budget_vnext.collect_full_education_budget(
        2026, page_size=2, resume=False, allow_live=True
    )
    assert result["complete"] is True
    assert result["saved"] == 2
    with db.connect() as conn:
        payloads = [json.loads(row["payload_json"]) for row in conn.execute(
            "SELECT payload_json FROM raw_records WHERE dataset='education_budget' ORDER BY id"
        )]
    assert [row["projectCode"] for row in payloads] == ["P1", "P2"]


def test_budget_projection_organizes_three_source_layers_without_dropping_rows():
    preserve_raw(
        "budget", "q1",
        {
            "fyr": "2026", "exe_ymd": "20260917", "wa_laf_cd": "4100000",
            "laf_cd": "4111000", "laf_hg_nm": "수원시", "dept_cd": "D1",
            "dbiz_cd": "Q1", "dbiz_nm": "도로시설 유지관리", "bdg_cash_amt": "1000",
            "cpl_amt": "1000", "ep_amt": "250", "bdg_ntep": "100", "capep": "200", "sggep": "700",
        },
        source_system="지방재정365 QWGJK", source_operation="QWGJK_FULL_V2_SNAPSHOT", source_date="2026-09-17",
    )
    preserve_raw(
        "budget_appropriation", "a1",
        {
            "fyr": "2026", "wa_laf_cd": "4100000", "laf_cd": "4111000",
            "fld_cd": "F1", "fld_nm": "교통및물류", "sect_cd": "S1", "sect_nm": "도로",
            "biz_bdg_tott_amt": "5000", "acnt_dv_nm": "일반회계",
        },
        source_system="지방재정365 AIDFA", source_operation="AIDFA_FULL_V1", source_date="2026",
    )
    preserve_raw(
        "education_budget", "e1",
        {
            "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
            "projectCode": "E1", "사업명": "학교시설 환경개선", "예산액": "3000", "집행액": "500",
        },
        source_system="지방교육재정알리미(opclTotal)", source_operation="EDUINFO_FULL_RAW_V1:opclTotal", source_date="2026",
    )

    result = budget_projection_vnext.refresh_budget_projection()
    assert result["projected"] == 3
    assert result["by_dataset"] == {
        "budget": 1, "budget_appropriation": 1, "education_budget": 1,
    }
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM vnext_budget_projection ORDER BY source_layer"
        )]
    by_layer = {row["source_layer"]: row for row in rows}
    assert set(by_layer) == {"APPROPRIATION", "DETAIL_EXECUTION", "EDUCATION"}
    assert by_layer["DETAIL_EXECUTION"]["project_name"] == "도로시설 유지관리"
    assert by_layer["DETAIL_EXECUTION"]["remaining_amount"] == 750
    assert by_layer["APPROPRIATION"]["project_name"] == "교통및물류 > 도로"
    assert by_layer["APPROPRIATION"]["appropriation_amount"] == 5000
    assert by_layer["EDUCATION"]["project_name"] == "학교시설 환경개선"
    assert by_layer["EDUCATION"]["remaining_amount"] == 2500


def test_projection_keeps_raw_amount_fields_for_later_source_specific_mapping():
    preserve_raw(
        "budget_appropriation", "a2",
        {"fyr": "2026", "fld_cd": "F2", "mystery_budget_amt": "12345", "fld_nm": "환경"},
        source_system="지방재정365 AIDFA", source_operation="AIDFA_FULL_V1", source_date="2026",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["budget_appropriation"])
    with db.connect() as conn:
        row = conn.execute(
            "SELECT amounts_json FROM vnext_budget_projection WHERE raw_dataset='budget_appropriation' AND raw_source_key='a2'"
        ).fetchone()
    amounts = json.loads(row["amounts_json"])
    assert amounts["mystery_budget_amt"] == "12345"


def test_education_collection_can_use_explicit_request_type_without_mutating_global_setting(monkeypatch):
    calls = []

    def fake_fetch(year, page=1, size=1000, *, request_type=""):
        calls.append((year, page, size, request_type))
        return [
            {
                "YMQ": "2026", "officeCode": "B10", "projectCode": "P1",
                "사업명": "학교시설 환경개선", "예산액": "1000",
            }
        ], 1

    monkeypatch.setattr(education_budget_vnext, "fetch_page", fake_fetch)
    monkeypatch.setattr(education_budget_vnext.legacy, "get_request_type", lambda: "legacyDefault")

    result = education_budget_vnext.collect_full_education_budget(
        2026,
        request_type="explicitBudgetType",
        page_size=100,
        resume=False,
        allow_live=True,
    )

    assert result["complete"] is True
    assert result["scope"] == "2026:explicitBudgetType"
    assert calls == [(2026, 1, 100, "explicitBudgetType")]
    with db.connect() as conn:
        row = conn.execute(
            """SELECT source_operation FROM raw_records
               WHERE dataset='education_budget'"""
        ).fetchone()
    assert row["source_operation"] == "EDUINFO_FULL_RAW_V1:explicitBudgetType"


def test_education_request_type_plan_deduplicates_and_does_not_claim_source_completeness(monkeypatch):
    calls = []

    def fake_collect(year, *, request_type=None, **kwargs):
        calls.append(request_type)
        return {
            "scope": f"{year}:{request_type}",
            "complete": True,
            "fetched": 5,
            "saved": 5,
        }

    monkeypatch.setattr(
        education_budget_vnext,
        "collect_full_education_budget",
        fake_collect,
    )
    result = education_budget_vnext.collect_education_request_types(
        2026,
        ["typeA", "typeB", "typeA"],
        allow_live=True,
    )

    assert calls == ["typeA", "typeB"]
    assert result["request_types"] == ["typeA", "typeB"]
    assert result["complete_for_planned_request_types"] is True
    assert result["partition_scope"] == "EXPLICIT_REQUEST_TYPE_LIST"
    assert result["source_collection_completeness_verified"] is False
