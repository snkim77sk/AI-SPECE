import budget_notice_links_vnext
import budget_projection_vnext
import budget_read_vnext
import budget_targets_vnext
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


def _save_education_budget(key, *, institution_code="", institution_name="",
                           project="학교 LED 조명 개선", office_code="J10",
                           office_name="경기도교육청"):
    payload = {
        "YMQ": "2026",
        "officeCode": office_code,
        "교육청명": office_name,
        "projectCode": "E1",
        "사업명": project,
        "itemCode": "I1",
        "itemName": "시설비",
        "예산액": "300000000",
        "집행액": "50000000",
    }
    if institution_code:
        payload["schoolCode"] = institution_code
    if institution_name:
        payload["schoolName"] = institution_name
    vnext_store.preserve_raw(
        "education_budget", key, payload,
        source_system="지방교육재정알리미(typeA)",
        source_operation="EDUINFO_FULL_RAW_V1:typeA",
        source_date="2026-09-18",
    )


def _prepare_education(*notice_datasets):
    budget_projection_vnext.refresh_budget_projection(
        datasets=["education_budget"]
    )
    classification_vnext.classify_dataset("education_budget")
    for dataset in notice_datasets:
        classification_vnext.classify_dataset(dataset)


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



def test_minimum_classification_confidence_applies_to_notice_side_too():
    _save_budget("P1", "가로등 LED 교체")
    _save_notice("bid_notice_goods", "N1|00", "가로등 LED 구매")
    _prepare("bid_notice_goods")

    with db.connect() as conn:
        conn.execute(
            """UPDATE classifications
               SET confidence=0.5
               WHERE entity_type='bid_notice_goods'
                 AND entity_key='N1|00'"""
        )

    assert budget_notice_links_vnext.budget_notice_candidates(
        fiscal_year=2026,
        minimum_classification_confidence=0.9,
    ) == []

    rows = budget_notice_links_vnext.budget_notice_candidates(
        fiscal_year=2026,
        minimum_classification_confidence=0.5,
    )
    assert len(rows) == 1
    assert rows[0]["notice_source_key"] == "N1|00"

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


def test_budget_read_model_exposes_same_read_only_procurement_candidate():
    _save_budget("P1", "가로등 LED 교체")
    _save_notice("bid_notice_goods", "N1|00", "가로등 LED 구매")
    _prepare()

    payload = budget_read_vnext.budget_read_model(fiscal_year=2026)

    assert len(payload["procurement_candidates"]) == 1
    row = payload["procurement_candidates"][0]
    assert row["budget_raw_source_key"] == "P1"
    assert row["notice_source_key"] == "N1|00"
    assert row["candidate_only"] is True
    assert row["source_traffic"] is False


def test_aidfa_appropriation_remains_target_context_but_is_not_direct_notice_candidate():
    vnext_store.preserve_raw(
        "budget_appropriation", "AIDFA-1",
        {
            "fyr": "2026",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "fld_cd": "F1",
            "fld_nm": "도로조명",
            "sect_cd": "S1",
            "sect_nm": "LED 가로등",
            "acnt_dv_cd": "A1",
            "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "500000000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    _save_notice(
        "bid_notice_goods", "N1|00", "도로조명 LED 가로등 구매",
        org_code="4111000", org_name="수원시",
    )
    budget_projection_vnext.refresh_budget_projection(
        datasets=["budget_appropriation"]
    )
    classification_vnext.classify_dataset("budget_appropriation")
    classification_vnext.classify_dataset("bid_notice_goods")

    targets = budget_targets_vnext.target_candidates(fiscal_year=2026)
    assert len(targets) == 1
    assert targets[0]["source_layer"] == "APPROPRIATION"
    assert targets[0]["primary_category"] == "LIGHTING"

    assert budget_notice_links_vnext.budget_notice_candidates(
        fiscal_year=2026
    ) == []


def test_institution_scoped_education_budget_does_not_link_on_education_office_only():
    _save_education_budget(
        "school-a", institution_code="S1", institution_name="가온초등학교"
    )
    _save_education_budget(
        "school-b", institution_code="S2", institution_name="나래초등학교"
    )
    _save_notice(
        "bid_notice_goods", "N1|00", "학교 LED 조명 개선 구매",
        org_code="J10", org_name="경기도교육청",
    )
    _prepare_education("bid_notice_goods")

    rows = budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026)

    assert rows == []


def test_education_notice_exact_institution_name_links_only_that_school():
    _save_education_budget(
        "school-a", institution_code="S1", institution_name="가온초등학교"
    )
    _save_education_budget(
        "school-b", institution_code="S2", institution_name="나래초등학교"
    )
    _save_notice(
        "bid_notice_goods", "N1|00", "학교 LED 조명 개선 구매",
        org_code="", org_name="가온초등학교",
    )
    _prepare_education("bid_notice_goods")

    rows = budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026)

    assert len(rows) == 1
    assert rows[0]["budget_raw_source_key"] == "school-a"
    assert rows[0]["budget_institution_name"] == "가온초등학교"
    assert rows[0]["institution_match"] == "EXACT_INSTITUTION_NAME"


def test_education_notice_title_can_supply_explicit_institution_evidence():
    _save_education_budget(
        "school-a", institution_code="S1", institution_name="가온초등학교"
    )
    _save_education_budget(
        "school-b", institution_code="S2", institution_name="나래초등학교"
    )
    _save_notice(
        "bid_notice_goods", "N1|00", "가온초등학교 LED 조명 개선 구매",
        org_code="J10", org_name="경기도교육청",
    )
    _prepare_education("bid_notice_goods")

    rows = budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026)

    assert len(rows) == 1
    assert rows[0]["budget_raw_source_key"] == "school-a"
    assert rows[0]["organization_match"] == "EXACT_ORG_CODE"
    assert rows[0]["institution_match"] == "INSTITUTION_NAME_IN_NOTICE"



def test_education_notice_title_only_school_match_requires_same_office():
    _save_education_budget(
        "school-a",
        institution_code="S1",
        institution_name="중앙초등학교",
        office_code="J10",
        office_name="경기도교육청",
    )
    _save_notice(
        "bid_notice_goods",
        "N-cross-office|00",
        "중앙초등학교 LED 조명 개선 구매",
        org_code="K10",
        org_name="부산광역시교육청",
    )
    _prepare_education("bid_notice_goods")

    assert budget_notice_links_vnext.budget_notice_candidates(
        fiscal_year=2026
    ) == []

def test_education_budget_without_institution_identity_can_still_use_office_match():
    _save_education_budget("office-level")
    _save_notice(
        "bid_notice_goods", "N1|00", "학교 LED 조명 개선 구매",
        org_code="J10", org_name="경기도교육청",
    )
    _prepare_education("bid_notice_goods")

    rows = budget_notice_links_vnext.budget_notice_candidates(fiscal_year=2026)

    assert len(rows) == 1
    assert rows[0]["budget_raw_source_key"] == "office-level"
    assert rows[0]["organization_match"] == "EXACT_ORG_CODE"
    assert rows[0]["institution_match"] == "NOT_APPLICABLE"
