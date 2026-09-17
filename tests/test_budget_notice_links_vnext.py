import budget_notice_links_vnext
import budget_projection_vnext
import classification_vnext
import db
import vnext_store


def _save_budget(key, name, *, org_code="4111000", org_name="수원시", year="2026"):
    vnext_store.preserve_raw(
        "budget", key,
        {
            "fyr": year, "exe_ymd": year + "0918",
            "wa_laf_cd": "4100000", "wa_laf_hg_nm": "경기",
            "laf_cd": org_code, "laf_hg_nm": org_name,
            "dbiz_cd": key, "dbiz_nm": name, "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "100000000", "ep_amt": "10000000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date=year + "-09-18",
    )


def _save_notice(dataset, key, name, *, org_code="4111000", org_name="수원시",
                 date="2026-09-18"):
    notice_no, notice_ord = key.split("|", 1)
    vnext_store.preserve_raw(
        dataset, key,
        {
            "bidNtceNo": notice_no, "bidNtceOrd": notice_ord,
            "bidNtceNm": name, "bidNtceDt": date,
            "dminsttCd": org_code, "dminsttNm": org_name,
        },
        source_system="G2B", source_operation="TEST", source_date=date,
    )


def _prepare():
    budget_projection_vnext.refresh_budget_projection(datasets=["budget"])
    classification_vnext.classify_dataset("budget")
    classification_vnext.classify_dataset("bid_notice_goods")
    classification_vnext.classify_dataset("bid_notice_service")


def _counts():
    with db.connect() as conn:
        return {
            "raw": conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0],
            "classification": conn.execute("SELECT COUNT(*) FROM classifications").fetchone()[0],
            "links": conn.execute("SELECT COUNT(*) FROM lifecycle_links").fetchone()[0],
        }


def test_exact_year_org_category_and_project_token_returns_candidate():
    _save_budget("P1", "노후 가로등 LED 교체")
    _save_notice("bid_notice_goods", "N1|00", "가로등 LED 등기구 교체 구매")
    _prepare()

    rows = budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026)

    assert len(rows) == 1
    row = rows[0]
    assert row["budget_raw_source_key"] == "P1"
    assert row["notice_source_key"] == "N1|00"
    assert row["primary_category"] == "LIGHTING"
    assert row["organization_match"] == "EXACT_ORG_CODE"
    assert set(row["shared_project_tokens"]) >= {"가로등", "led"}
    assert row["candidate_only"] is True
    assert row["persisted_link"] is False


def test_wrong_year_wrong_org_or_missing_name_evidence_are_not_linked():
    _save_budget("P1", "가로등 LED 교체")
    _save_notice("bid_notice_goods", "N1|00", "가로등 LED 구매", date="2025-09-18")
    _save_notice("bid_notice_goods", "N2|00", "가로등 LED 구매", org_code="9999999", org_name="다른기관")
    _save_notice("bid_notice_goods", "N3|00", "실내 LED 평판등 구매")
    _prepare()

    assert budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026) == []


def test_same_org_and_name_but_different_post_raw_category_are_not_linked():
    _save_budget("P1", "청사 전기설비 개선")
    _save_notice("bid_notice_service", "N1|00", "청사 LED 조명 개선 설계")
    _prepare()

    assert budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026) == []


def test_exact_org_name_can_match_when_source_code_is_missing():
    _save_budget("P1", "보안등 LED 교체", org_code="")
    _save_notice("bid_notice_goods", "N1|00", "보안등 LED 구매", org_code="")
    _prepare()

    rows = budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026)
    assert len(rows) == 1
    assert rows[0]["organization_match"] == "EXACT_ORG_NAME"


def test_candidate_query_is_read_only_and_creates_no_lifecycle_link():
    _save_budget("P1", "가로등 LED 교체")
    _save_notice("bid_notice_goods", "N1|00", "가로등 LED 구매")
    _prepare()
    before = _counts()

    rows = budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026)
    summary = budget_notice_links_vnext.budget_notice_link_summary(fiscal_year=2026)

    after = _counts()
    assert rows
    assert summary["candidate_links"] == 1
    assert summary["persisted_links"] == 0
    assert summary["source_traffic"] is False
    assert after == before
