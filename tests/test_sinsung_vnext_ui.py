import pathlib

import budget_projection_vnext
import classification_vnext
import db
import sinsung_vnext_ui
import vnext_store


def _seed_budget():
    vnext_store.preserve_raw(
        "budget",
        "ui-budget-1",
        {
            "fyr": "2026",
            "exe_ymd": "20260924",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체사업",
            "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "100000000",
            "ep_amt": "10000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-24",
    )
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    classification_vnext.classify_dataset("budget")


def _state_counts():
    with db.connect() as conn:
        return {
            "raw": conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0],
            "revisions": conn.execute("SELECT COUNT(*) FROM raw_record_revisions").fetchone()[0],
            "classifications": conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
            "links": conn.execute("SELECT COUNT(*) FROM lifecycle_links").fetchone()[0],
        }


def test_vnext_runtime_page_reads_stored_pipeline_without_collection_side_effects():
    _seed_budget()
    before = _state_counts()

    page = sinsung_vnext_ui.vnext_dashboard_html(
        {"year": ["2026"], "category": ["LIGHTING"], "limit": ["50"]}
    )

    after = _state_counts()
    assert "G2B vNext 예산 · 조달 영업 파이프라인" in page
    assert "LED 가로등 교체사업" in page
    assert "공고 전 영업후보" in page
    assert "90,000,000 원" in page
    assert "신규 API 호출·자동수집·bulk historical" in page
    assert after == before


def test_vnext_runtime_json_adapter_is_read_only():
    _seed_budget()
    before = _state_counts()

    payload = sinsung_vnext_ui.vnext_api_payload(
        {"year": ["2026"], "category": ["LIGHTING"], "limit": ["25"]}
    )

    after = _state_counts()
    assert payload["runtime_adapter"] == {
        "view": "G2B_VNEXT_READ_ONLY",
        "source_traffic": False,
        "collection_side_effects": False,
    }
    assert payload["status"]["read_only"] is True
    assert payload["status"]["source_traffic"] is False
    assert payload["status"]["source_collection_completeness_verified"] is False
    assert payload["prebid_rows"][0]["project_name"] == "LED 가로등 교체사업"
    assert after == before


def test_runtime_wiring_occurs_before_signup_role_wrapper():
    text = pathlib.Path("main.py").read_text(encoding="utf-8")
    vnext_pos = text.index("apply_vnext_ui()")
    signup_pos = text.index("apply_signup_approval()")
    education_pos = text.index("apply_education_budget()")

    assert vnext_pos < signup_pos < education_pos
    assert "apply_vnext_ui" in text
