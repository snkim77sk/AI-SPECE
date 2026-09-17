import json

import budget_appropriation_vnext
import budget_projection_vnext
import db
import lofin_vnext_http
from vnext_store import preserve_raw


def _aidfa_row(**overrides):
    row = {
        "fyr": "2025",
        "wa_laf_cd": "4100000",
        "wa_laf_hg_nm": "경기",
        "laf_cd": "4111000",
        "laf_hg_nm": "수원시",
        "fld_cd": "120",
        "fld_nm": "교통및물류",
        "sect_cd": "126",
        "sect_nm": "도로",
        "biz_bdg_tott_amt": "10000",
        "fin_acv_tott_amt": "2000",
        "padm_oper_exps_tott_amt": "1000",
        "biz_bdg_prsm_amt": "9000",
        "fin_acv_prsm_amt": "1500",
        "padm_oper_prsm_exps": "800",
        "acnt_dv_nm": "일반회계",
    }
    row.update(overrides)
    return row


def test_aidfa_projection_uses_documented_policy_budget_total():
    fact = budget_projection_vnext.project_payload(
        "budget_appropriation", _aidfa_row(), source_date="2025"
    )
    assert fact["source_layer"] == "APPROPRIATION"
    assert fact["fiscal_year"] == 2025
    assert fact["region_code"] == "4100000"
    assert fact["org_code"] == "4111000"
    assert fact["field_name"] == "교통및물류"
    assert fact["section_name"] == "도로"
    assert fact["account_name"] == "일반회계"
    assert fact["project_name"] == "교통및물류 > 도로"
    assert fact["budget_amount"] == 10000
    assert fact["appropriation_amount"] == 10000
    assert fact["remaining_amount"] == 10000
    assert fact["executed_amount"] == 0


def test_aidfa_projection_falls_back_to_policy_budget_net_only_if_total_missing():
    fact = budget_projection_vnext.project_payload(
        "budget_appropriation",
        _aidfa_row(biz_bdg_tott_amt="", biz_bdg_prsm_amt="7777"),
        source_date="2025",
    )
    assert fact["budget_amount"] == 7777
    assert fact["appropriation_amount"] == 7777


def test_aidfa_source_identity_distinguishes_account_division():
    normal = budget_appropriation_vnext._source_key(_aidfa_row(acnt_dv_nm="일반회계"), 2025, "4100000")
    special = budget_appropriation_vnext._source_key(_aidfa_row(acnt_dv_nm="특별회계"), 2025, "4100000")
    assert normal != special


def test_aidfa_region_partition_uses_documented_wa_laf_cd(monkeypatch):
    captured = {}
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "TEST_KEY")

    def fake_request(params, retries=3, timeout=45, *, service_code=None):
        captured["params"] = dict(params)
        captured["service_code"] = service_code
        return [], 0, "INFO-000", "OK"

    monkeypatch.setattr(lofin_vnext_http, "_request", fake_request)
    lofin_vnext_http.fetch_appropriation_page(
        2025, region_code="4100000", page=3, size=500
    )
    assert captured["service_code"] == "AIDFA"
    assert captured["params"]["fyr"] == 2025
    assert captured["params"]["wa_laf_cd"] == "4100000"
    assert captured["params"]["pIndex"] == 3
    assert captured["params"]["pSize"] == 500


def test_aidfa_all_documented_amount_columns_remain_in_amounts_json():
    preserve_raw(
        "budget_appropriation", "aidfa-1", _aidfa_row(),
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2025",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["budget_appropriation"])
    with db.connect() as conn:
        row = conn.execute(
            "SELECT amounts_json,budget_amount FROM vnext_budget_projection "
            "WHERE raw_dataset='budget_appropriation' AND raw_source_key='aidfa-1'"
        ).fetchone()
    amounts = json.loads(row["amounts_json"])
    assert row["budget_amount"] == 10000
    assert amounts["biz_bdg_tott_amt"] == "10000"
    assert amounts["fin_acv_tott_amt"] == "2000"
    assert amounts["padm_oper_exps_tott_amt"] == "1000"
    assert amounts["biz_bdg_prsm_amt"] == "9000"
    assert amounts["fin_acv_prsm_amt"] == "1500"
    assert amounts["padm_oper_prsm_exps"] == "800"
