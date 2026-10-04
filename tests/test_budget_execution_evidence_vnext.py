from pathlib import Path

import db
import budget_execution_evidence_vnext as evidence
import vnext_source_guard


def _budget(
    name="솔빛도서관 LED 조명 개선사업",
    *,
    key="B1",
    org_code="4111000",
    org_name="수원시",
    category="LIGHTING",
    year=2026,
):
    return {
        "project_identity": f"DETAIL_EXECUTION|{year}|{org_code}|D1|{key}|A1",
        "raw_source_key": key,
        "source_layer": "DETAIL_EXECUTION",
        "fiscal_year": year,
        "org_code": org_code,
        "org_name": org_name,
        "project_code": key,
        "project_name": name,
        "primary_category": category,
        "snapshot_date": f"{year}-06-01",
    }


def _award(
    title="솔빛도서관 조명개선 실시설계용역",
    *,
    no="R26BK000001",
    order="000",
    cls="0",
    rebid="0",
    org_code="4111000",
    org_name="수원시",
    date="20260920",
    amount="12345678",
):
    return {
        "bidNtceNo": no,
        "bidNtceOrd": order,
        "bidClsfcNo": cls,
        "rbidNo": rebid,
        "opengDt": date,
        "dminsttCd": org_code,
        "dminsttNm": org_name,
        "bidNtceNm": title,
        "bidwinnrNm": "낙찰업체",
        "bidwinnrBizno": "123-45-67890",
        "sucsfbidAmt": amount,
        "sucsfbidRate": "87.745",
    }


def test_service_award_matches_only_with_distinctive_project_identity():
    fact = evidence.compact_award_fact(
        _award(),
        business_type="service",
    )
    row = evidence.match_budget_project(_budget(), fact)

    assert fact["valid"] is True
    assert fact["evidence_type"] == evidence.SERVICE_AWARD
    assert row is not None
    assert row["organization_basis"] == "EXACT_ORG_CODE"
    assert "솔빛도서관" in row["shared_identity"]
    assert row["match_confidence"] >= 0.96
    assert row["source_traffic"] is False


def test_generic_lighting_words_do_not_create_false_positive():
    fact = evidence.compact_award_fact(
        _award(title="LED 가로등 실시설계용역"),
        business_type="service",
    )
    budget = _budget(name="LED 가로등 교체사업")

    assert fact["valid"] is True
    assert evidence.match_budget_project(budget, fact) is None


def test_construction_award_is_typed_lighting_before_general_electrical():
    lighting = evidence.compact_award_fact(
        _award(title="솔빛도서관 LED 조명 전기공사"),
        business_type="construction",
    )
    electrical = evidence.compact_award_fact(
        _award(title="솔빛도서관 수배전 전기공사", no="R26BK000002"),
        business_type="construction",
    )

    assert lighting["valid"] is True
    assert lighting["evidence_type"] == evidence.LIGHTING_WORK_AWARD
    assert electrical["valid"] is True
    assert electrical["evidence_type"] == evidence.ELECTRICAL_WORK_AWARD


def test_unrelated_construction_award_is_not_compacted():
    fact = evidence.compact_award_fact(
        _award(title="솔빛도서관 옥상 방수공사"),
        business_type="construction",
    )

    assert fact["valid"] is False
    assert "OUT_OF_SCOPE_WORK_AWARD" in fact["problems"]


def test_unrelated_service_row_is_not_compacted():
    fact = evidence.compact_award_fact(
        _award(title="솔빛도서관 단순 물품구매"),
        business_type="service",
    )

    assert fact["valid"] is False
    assert "OUT_OF_SCOPE_SERVICE_AWARD" in fact["problems"]


def test_missing_execution_identity_fails_closed():
    row = _award()
    row["bidClsfcNo"] = ""

    fact = evidence.compact_award_fact(row, business_type="service")

    assert fact["valid"] is False
    assert "MISSING_OFFICIAL_EXECUTION_IDENTITY" in fact["problems"]


def test_missing_notice_order_is_not_guessed_as_zero():
    row = _award()
    row["bidNtceOrd"] = ""

    fact = evidence.compact_award_fact(row, business_type="service")

    assert fact["valid"] is False
    assert "MISSING_OFFICIAL_EXECUTION_IDENTITY" in fact["problems"]


def test_administrative_transition_requires_effective_date_and_locality_clue():
    budget = _budget(
        name="송림3동 LED 조명 개선사업",
        org_code="OLD-DONG",
        org_name="인천광역시 동구",
    )
    budget["snapshot_date"] = "2026-06-20"
    fact = evidence.compact_award_fact(
        _award(
            title="송림3동 조명개선 실시설계용역",
            org_code="NEW-JEMULPO",
            org_name="인천광역시 제물포구",
            date="20260720",
        ),
        business_type="service",
    )

    matched = evidence.match_budget_project(budget, fact)

    assert matched is not None
    assert matched["organization_basis"].startswith(
        "ADMIN_TRANSITION_ORG_MATCH"
    )
    assert "송림3동" in matched["shared_identity"]


def test_wrong_year_or_wrong_organization_does_not_match():
    fact = evidence.compact_award_fact(_award(), business_type="service")

    assert evidence.match_budget_project(
        _budget(year=2025), fact
    ) is None
    assert evidence.match_budget_project(
        _budget(org_code="9999999", org_name="다른기관"), fact
    ) is None


def test_one_execution_is_dropped_when_two_budget_projects_tie():
    fact = evidence.compact_award_fact(_award(), business_type="service")
    budgets = [
        _budget(name="솔빛도서관 LED 조명 개선", key="B1"),
        _budget(name="솔빛도서관 보안등 개선", key="B2"),
    ]

    result = evidence.match_award_facts(budgets, [fact])

    assert result["matches"] == []
    assert len(result["ambiguous"]) == 1
    assert result["ambiguous"][0]["reason"] == (
        "AMBIGUOUS_BUDGET_PROJECT_ASSIGNMENT"
    )


def test_stronger_distinctive_identity_wins_one_execution_assignment():
    fact = evidence.compact_award_fact(
        _award(title="솔빛도서관 별빛마당 LED 조명 실시설계용역"),
        business_type="service",
    )
    budgets = [
        _budget(
            name="솔빛도서관 별빛마당 LED 조명 개선",
            key="STRONG",
        ),
        _budget(
            name="솔빛도서관 주차장 LED 조명 개선",
            key="WEAK",
        ),
    ]

    result = evidence.match_award_facts(budgets, [fact])

    assert len(result["matches"]) == 1
    assert result["matches"][0]["budget_raw_source_key"] == "STRONG"
    assert len(result["matches"][0]["shared_identity"]) >= 2


def test_notice_metadata_can_supply_title_and_institution_without_raw_copy():
    row = _award()
    row.pop("bidNtceNm")
    row.pop("dminsttCd")
    row.pop("dminsttNm")
    notice = {
        "bidNtceNo": row["bidNtceNo"],
        "bidNtceOrd": row["bidNtceOrd"],
        "bidNtceNm": "솔빛도서관 조명개선 감리용역",
        "dminsttCd": "4111000",
        "dminsttNm": "수원시",
    }

    fact = evidence.compact_award_fact(
        row,
        business_type="service",
        notice=notice,
    )

    assert fact["valid"] is True
    assert fact["award_title"] == "솔빛도서관 조명개선 감리용역"
    assert fact["award_org_code"] == "4111000"


def test_store_rejects_same_execution_assigned_to_two_budget_projects():
    fact = evidence.compact_award_fact(_award(), business_type="service")
    first = evidence.match_budget_project(
        _budget(name="솔빛도서관 별빛마당 LED 조명 개선", key="B1"),
        fact,
    )
    second = evidence.match_budget_project(
        _budget(name="솔빛도서관 별빛마당 경관조명 개선", key="B2"),
        fact,
    )
    assert first is not None
    assert second is not None

    saved = evidence.save_compact_evidence([first, second])

    assert saved["saved"] == 0
    assert saved["ambiguous_source_rows_rejected"] == 1
    assert evidence.evidence_rows(fiscal_year=2026) == []


def test_compact_store_persists_only_selected_fields_and_is_idempotent():
    fact = evidence.compact_award_fact(_award(), business_type="service")
    match = evidence.match_budget_project(_budget(), fact)
    assert match is not None

    first = evidence.save_compact_evidence([match])
    second = evidence.save_compact_evidence([match])
    rows = evidence.evidence_rows(fiscal_year=2026)

    assert first["saved"] == 1
    assert second["saved"] == 1
    assert first["raw_payload_saved"] is False
    assert len(rows) == 1
    assert rows[0]["evidence_type"] == evidence.SERVICE_AWARD
    assert rows[0]["award_amount"] == 12345678
    assert rows[0]["vendor_name"] == "낙찰업체"

    with db.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(budget_execution_evidence)"
            ).fetchall()
        }
    assert "payload_json" not in columns
    assert "bidder_list_json" not in columns
    assert "preliminary_price_json" not in columns


def test_build_compact_evidence_persists_only_unambiguous_matches():
    rows = [
        _award(),
        _award(
            title="솔빛도서관 옥상 방수공사",
            no="R26BK000003",
        ),
    ]

    result = evidence.build_compact_evidence(
        rows,
        business_type="service",
        budgets=[_budget()],
        persist=True,
    )

    # A service award may be any service subtype, but it still requires a
    # distinctive budget-project identity before persistence.
    assert result["facts"] == 2
    assert len(result["matches"]) == 1
    assert result["saved"] == 1
    assert result["raw_payload_saved"] is False
    assert result["source_traffic"] is False


def test_module_cannot_call_g2b_sources_and_source_guard_stays_closed():
    source = Path("budget_execution_evidence_vnext.py").read_text(
        encoding="utf-8"
    )

    assert "vnext_http" not in source
    assert "get_service_key" not in source
    assert "award_vnext" not in source
    assert all(
        "ScsbidInfoService" not in path
        for path in vnext_source_guard._G2B_SMALL_VALIDATION_PATHS
    )
