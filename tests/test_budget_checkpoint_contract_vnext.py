import json

import pytest

import budget_appropriation_vnext
import budget_vnext
import education_budget_vnext
import vnext_store


DAY = "2026-09-15"


def _qwgjk_row(code):
    return {
        "fyr": "2026",
        "exe_ymd": "20260915",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "D1",
        "dbiz_cd": code,
        "acnt_dv_cd": "A1",
    }


def test_qwgjk_resume_rejects_identity_contract_change_before_source_call(monkeypatch):
    calls = []

    def fetch(year, snapshot, keyword, page=1, size=1000, **kwargs):
        calls.append(page)
        rows = {1: [_qwgjk_row("P1")], 2: [_qwgjk_row("P2")]}
        return list(rows.get(page, [])), 2, "INFO-000", ""

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", fetch)

    first = budget_vnext.collect_full_budget(
        2026, DAY, page_size=1, max_pages=1, resume=False
    )
    assert first["status"] == "RUNNING"
    assert first["complete"] is False
    assert first["source_total"] == 2
    assert first["fetched"] == first["saved"] == 1
    assert calls == [1]

    before = vnext_store.get_checkpoint("budget", f"2026:{DAY}")
    before_meta = json.loads(before["cursor_value"])
    assert before_meta["checkpoint_contract"] == budget_vnext.CHECKPOINT_CONTRACT

    monkeypatch.setattr(
        budget_vnext, "CHECKPOINT_CONTRACT", "QWGJK_SOURCE_IDENTITY_V2"
    )
    with pytest.raises(ValueError, match="collection contract changed"):
        budget_vnext.collect_full_budget(
            2026, DAY, page_size=1, max_pages=1, resume=True
        )
    assert calls == [1]
    unchanged = vnext_store.get_checkpoint("budget", f"2026:{DAY}")
    assert unchanged["cursor_value"] == before["cursor_value"]
    assert unchanged["page_no"] == 2
    assert unchanged["fetched_count"] == 1

    replay = budget_vnext.collect_full_budget(
        2026, DAY, page_size=1, max_pages=1, resume=False
    )
    after = vnext_store.get_checkpoint("budget", f"2026:{DAY}")
    after_meta = json.loads(after["cursor_value"])
    assert replay["status"] == "RUNNING"
    assert calls == [1, 1]
    assert after_meta["checkpoint_contract"] == "QWGJK_SOURCE_IDENTITY_V2"
    assert after_meta["generation"] != before_meta["generation"]


def test_aidfa_checkpoint_records_identity_contract(monkeypatch):
    row = {
        "fyr": "2026",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "fld_cd": "F1",
        "sect_cd": "S1",
        "acnt_dv_cd": "A1",
    }
    monkeypatch.setattr(
        budget_appropriation_vnext,
        "fetch_appropriation_page",
        lambda *args, **kwargs: ([row], 1, "INFO-000", ""),
    )

    result = budget_appropriation_vnext.collect_full_appropriation(
        2026, region_code="4100000", resume=False
    )
    assert result["complete"] is True
    cp = vnext_store.get_checkpoint("budget_appropriation", "2026:4100000")
    assert json.loads(cp["cursor_value"])["checkpoint_contract"] == (
        budget_appropriation_vnext.CHECKPOINT_CONTRACT
    )


def test_education_checkpoint_records_identity_contract(monkeypatch):
    row = {
        "YMQ": "2026",
        "officeCode": "J10",
        "schoolCode": "S1",
        "projectCode": "P1",
        "itemCode": "I1",
    }
    monkeypatch.setattr(
        education_budget_vnext,
        "fetch_page",
        lambda year, page=1, size=1000, *, request_type="": ([row], 1),
    )

    result = education_budget_vnext.collect_full_education_budget(
        2026,
        request_type="typeA",
        resume=False,
        allow_live=True,
    )
    assert result["complete"] is True
    cp = vnext_store.get_checkpoint("education_budget", "2026:typeA")
    assert json.loads(cp["cursor_value"])["checkpoint_contract"] == (
        education_budget_vnext.CHECKPOINT_CONTRACT
    )
