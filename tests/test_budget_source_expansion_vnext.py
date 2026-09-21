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


def test_aidfa_region_partition_plan_deduplicates_regions_and_stops_on_incomplete(monkeypatch):
    calls = []

    def fake_collect(year, *, region_code="", **kwargs):
        calls.append(region_code)
        return {
            "scope": f"{year}:{region_code}",
            "complete": region_code != "2600000",
            "fetched": 10,
            "saved": 10,
        }

    monkeypatch.setattr(
        budget_appropriation_vnext,
        "collect_full_appropriation",
        fake_collect,
    )
    result = budget_appropriation_vnext.collect_appropriation_region_partitions(
        2026,
        ["4100000", "2600000", "1100000", "4100000"],
    )

    assert calls == ["4100000", "2600000"]
    assert result["region_codes"] == ["4100000", "2600000", "1100000"]
    assert result["complete_for_planned_regions"] is False
    assert result["partition_scope"] == "EXPLICIT_REGION_LIST"
    assert result["source_collection_completeness_verified"] is False


def test_aidfa_region_partition_plan_marks_only_supplied_plan_complete(monkeypatch):
    calls = []

    def fake_collect(year, *, region_code="", **kwargs):
        calls.append(region_code)
        return {"scope": f"{year}:{region_code}", "complete": True, "fetched": 5, "saved": 5}

    monkeypatch.setattr(
        budget_appropriation_vnext,
        "collect_full_appropriation",
        fake_collect,
    )
    result = budget_appropriation_vnext.collect_appropriation_region_partitions(
        2026,
        ["4100000", "1100000"],
    )

    assert calls == ["4100000", "1100000"]
    assert result["complete_for_planned_regions"] is True
    assert result["source_collection_completeness_verified"] is False


def test_budget_projection_preserves_account_codes_from_qwgjk_and_education():
    preserve_raw(
        "budget", "q-account",
        {
            "fyr": "2026", "exe_ymd": "20260919",
            "laf_cd": "4111000", "dbiz_cd": "P1", "dbiz_nm": "도로조명",
            "acnt_dv_cd": "A1", "acnt_dv_nm": "일반회계", "bdg_cash_amt": "1000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )
    preserve_raw(
        "education_budget", "e-account",
        {
            "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
            "projectCode": "E1", "사업명": "학교 LED 조명 개선",
            "itemCode": "I7", "itemName": "시설비", "예산액": "2000",
        },
        source_system="지방교육재정알리미(typeA)",
        source_operation="EDUINFO_FULL_RAW_V1:typeA",
        source_date="2026-09-19",
    )

    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget", "education_budget"]
    )

    with db.connect() as conn:
        rows = {
            row["raw_dataset"]: dict(row)
            for row in conn.execute(
                """SELECT raw_dataset,account_code,account_name
                   FROM vnext_budget_projection
                   WHERE raw_source_key IN ('q-account','e-account')"""
            )
        }
    assert rows["budget"]["account_code"] == "A1"
    assert rows["budget"]["account_name"] == "일반회계"
    assert rows["education_budget"]["account_code"] == "I7"
    assert rows["education_budget"]["account_name"] == "시설비"


def test_education_raw_identity_separates_institution_names_when_codes_are_missing():
    base = {
        "YMQ": "2026",
        "officeCode": "J10",
        "projectCode": "E1",
        "itemCode": "I1",
        "사업명": "학교 LED 조명 개선",
    }
    one = dict(base, institutionName="가온초등학교")
    two = dict(base, institutionName="나래초등학교")

    assert education_budget_vnext._source_key(
        one, 2026, "typeA"
    ) != education_budget_vnext._source_key(
        two, 2026, "typeA"
    )


def test_education_raw_identity_uses_institution_code_before_mutable_name():
    one = {
        "YMQ": "2026",
        "officeCode": "J10",
        "schoolCode": "S1",
        "schoolName": "가온초등학교",
        "projectCode": "E1",
        "itemCode": "I1",
    }
    renamed = dict(one, schoolName="가온초")

    assert education_budget_vnext._source_key(
        one, 2026, "typeA"
    ) == education_budget_vnext._source_key(
        renamed, 2026, "typeA"
    )


def test_budget_projection_preserves_department_and_education_institution_fields():
    preserve_raw(
        "budget", "q-subunit",
        {
            "fyr": "2026", "exe_ymd": "20260919",
            "laf_cd": "4111000", "laf_hg_nm": "수원시",
            "dept_cd": "D7", "dept_nm": "도로관리과",
            "dbiz_cd": "P1", "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1", "bdg_cash_amt": "1000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )
    preserve_raw(
        "education_budget", "e-subunit",
        {
            "YMQ": "2026",
            "officeCode": "J10",
            "교육청명": "경기도교육청",
            "schoolCode": "S7",
            "schoolName": "가온초등학교",
            "departmentCode": "ED1",
            "departmentName": "교육시설과",
            "projectCode": "E1",
            "사업명": "학교 LED 조명 개선",
            "itemCode": "I1",
            "예산액": "2000",
        },
        source_system="지방교육재정알리미(typeA)",
        source_operation="EDUINFO_FULL_RAW_V1:typeA",
        source_date="2026-09-19",
    )

    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget", "education_budget"]
    )

    with db.connect() as conn:
        q = conn.execute(
            """SELECT dept_code,dept_name,institution_code,institution_name
               FROM vnext_budget_projection
               WHERE raw_dataset='budget' AND raw_source_key='q-subunit'"""
        ).fetchone()
        e = conn.execute(
            """SELECT dept_code,dept_name,institution_code,institution_name
               FROM vnext_budget_projection
               WHERE raw_dataset='education_budget' AND raw_source_key='e-subunit'"""
        ).fetchone()

    assert q["dept_code"] == "D7"
    assert q["dept_name"] == "도로관리과"
    assert q["institution_code"] == ""
    assert q["institution_name"] == ""
    assert e["dept_code"] == "ED1"
    assert e["dept_name"] == "교육시설과"
    assert e["institution_code"] == "S7"
    assert e["institution_name"] == "가온초등학교"


def test_aidfa_all_then_region_partition_reuses_same_raw_identity(monkeypatch):
    row = {
        "fyr": "2026",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "fld_cd": "F1",
        "sect_cd": "S1",
        "acnt_dv_nm": "일반회계",
        "biz_bdg_tott_amt": "5000",
    }
    calls = []

    def fake_fetch(year, region_code="", page=1, size=1000, **kwargs):
        calls.append(region_code)
        return [dict(row)], 1, "INFO-000", ""

    monkeypatch.setattr(
        budget_appropriation_vnext,
        "fetch_appropriation_page",
        fake_fetch,
    )

    all_rows = budget_appropriation_vnext.collect_full_appropriation(
        2026, region_code="", resume=False
    )
    regional = budget_appropriation_vnext.collect_full_appropriation(
        2026, region_code="4100000", resume=False
    )

    assert all_rows["complete"] is True
    assert regional["complete"] is True
    assert calls == ["", "4100000"]
    with db.connect() as conn:
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM raw_records WHERE dataset='budget_appropriation'"
        ).fetchone()[0]
        revision_count = conn.execute(
            """SELECT COUNT(*) FROM raw_record_revisions
               WHERE dataset='budget_appropriation'"""
        ).fetchone()[0]
    assert raw_count == 1
    assert revision_count == 1


def test_education_raw_identity_separates_departments_with_same_project_and_item():
    base = {
        "YMQ": "2026",
        "officeCode": "J10",
        "schoolCode": "S1",
        "projectCode": "E1",
        "itemCode": "I1",
    }
    one = dict(base, departmentCode="D1", departmentName="시설과")
    two = dict(base, departmentCode="D2", departmentName="예산과")

    assert education_budget_vnext._source_key(
        one, 2026, "typeA"
    ) != education_budget_vnext._source_key(
        two, 2026, "typeA"
    )


def test_education_raw_identity_uses_department_code_before_mutable_name():
    one = {
        "YMQ": "2026",
        "officeCode": "J10",
        "schoolCode": "S1",
        "departmentCode": "D1",
        "departmentName": "시설과",
        "projectCode": "E1",
        "itemCode": "I1",
    }
    renamed = dict(one, departmentName="교육시설과")

    assert education_budget_vnext._source_key(
        one, 2026, "typeA"
    ) == education_budget_vnext._source_key(
        renamed, 2026, "typeA"
    )


def test_education_raw_identity_uses_department_name_when_code_missing():
    base = {
        "YMQ": "2026",
        "officeCode": "J10",
        "schoolCode": "S1",
        "departmentCode": "",
        "projectCode": "E1",
        "itemCode": "I1",
    }
    one = dict(base, departmentName="시설과")
    two = dict(base, departmentName="예산과")

    assert education_budget_vnext._source_key(
        one, 2026, "typeA"
    ) != education_budget_vnext._source_key(
        two, 2026, "typeA"
    )


def test_qwgjk_projection_maps_official_ane_part_cd_to_section_code():
    preserve_raw(
        "budget", "q-ane-part",
        {
            "fyr": "2026",
            "exe_ymd": "20260919",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "fld_cd": "F1",
            "fld_nm": "교통및물류",
            "ane_part_cd": "S1",
            "part_nm": "도로",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "1000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )

    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])

    with db.connect() as conn:
        row = conn.execute(
            """SELECT section_code,section_name
               FROM vnext_budget_projection
               WHERE raw_dataset='budget' AND raw_source_key='q-ane-part'"""
        ).fetchone()
    assert row["section_code"] == "S1"
    assert row["section_name"] == "도로"


def test_qwgjk_projection_keeps_appropriation_current_budget_execution_and_remaining_distinct():
    preserve_raw(
        "budget", "q-amount-semantics",
        {
            "fyr": "2026",
            "exe_ymd": "20260919",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1",
            "cpl_amt": "1000",
            "bdg_cash_amt": "1500",
            "ep_amt": "300",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )

    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])

    with db.connect() as conn:
        row = conn.execute(
            """SELECT appropriation_amount,budget_amount,executed_amount,remaining_amount
               FROM vnext_budget_projection
               WHERE raw_dataset='budget' AND raw_source_key='q-amount-semantics'"""
        ).fetchone()
    assert row["appropriation_amount"] == 1000
    assert row["budget_amount"] == 1500
    assert row["executed_amount"] == 300
    assert row["remaining_amount"] == 1200


def test_qwgjk_explicit_zero_current_budget_does_not_fall_back_to_appropriation():
    preserve_raw(
        "budget", "q-zero-current",
        {
            "fyr": "2026",
            "exe_ymd": "20260921",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1",
            "cpl_amt": "1000",
            "bdg_cash_amt": "0",
            "ep_amt": "0",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-21",
    )

    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])

    with db.connect() as conn:
        row = conn.execute(
            """SELECT appropriation_amount,budget_amount,executed_amount,remaining_amount
               FROM vnext_budget_projection
               WHERE raw_dataset='budget' AND raw_source_key='q-zero-current'"""
        ).fetchone()

    assert row["appropriation_amount"] == 1000
    assert row["budget_amount"] == 0
    assert row["executed_amount"] == 0
    assert row["remaining_amount"] == 0


def test_qwgjk_missing_current_budget_can_fall_back_to_appropriation_for_remaining():
    preserve_raw(
        "budget", "q-missing-current",
        {
            "fyr": "2026",
            "exe_ymd": "20260921",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1",
            "cpl_amt": "1000",
            "ep_amt": "300",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-21",
    )

    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])

    with db.connect() as conn:
        row = conn.execute(
            """SELECT appropriation_amount,budget_amount,executed_amount,remaining_amount
               FROM vnext_budget_projection
               WHERE raw_dataset='budget' AND raw_source_key='q-missing-current'"""
        ).fetchone()

    assert row["appropriation_amount"] == 1000
    assert row["budget_amount"] == 0
    assert row["executed_amount"] == 300
    assert row["remaining_amount"] == 700
