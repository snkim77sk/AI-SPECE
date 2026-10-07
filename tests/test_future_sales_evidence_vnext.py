import future_sales_evidence_vnext as future


def _pattern(**overrides):
    row = {
        "org_name": "인천옹진군",
        "evidence_years": [2025, 2026],
        "population_complete_years": [2025, 2026],
        "evidence_only_years": [],
        "population_complete": True,
        "historical_budget_projects": 10,
        "high_matched_budget_projects": 6,
        "matched_budget_projects": 8,
        "high_match_project_rate": 0.6,
        "matched_project_rate": 0.8,
        "high_matched_shopping_rows": 8,
        "matched_budget_amount": 900000000,
        "actual_shopping_amount": 600000000,
        "shopping_to_budget_amount_ratio": 0.6667,
        "average_nonnegative_lag_days": 110.0,
        "signal_counts": {"LED": 6, "SECURITY_LIGHT": 5},
        "budget_categories": ["LIGHTING"],
        "shopping_categories": ["LIGHTING"],
        "pattern_basis": "PERSISTED_BUDGET_POPULATION_AND_HIGH_MATCH_EVIDENCE",
    }
    row.update(overrides)
    return row


def _future_row(**overrides):
    row = {
        "fiscal_year": 2027,
        "source_layer": "APPROPRIATION",
        "region_name": "인천광역시",
        "org_name": "인천옹진군",
        "project_name": "",
        "field_name": "교통및물류",
        "section_name": "도로조명",
        "account_name": "일반회계",
        "primary_category": "LIGHTING",
        "budget_amount": 500000000,
        "appropriation_amount": 500000000,
    }
    row.update(overrides)
    return row


def test_aidfa_future_evidence_is_strong_but_structurally_capped():
    result = future.score_future_budget_evidence(
        _future_row(),
        _pattern(),
    )

    assert result["historical_evidence_level"] == "STRONG_HISTORY"
    assert result["historical_evidence_score"] == 75
    assert "AIDFA_STRUCTURAL_CAP" in result["historical_evidence_reasons"]
    assert result["historical_high_projects"] == 6
    assert result["historical_total_budget_projects"] == 10
    assert result["historical_high_match_project_rate"] == 0.6
    assert result["historical_matched_project_rate"] == 0.8
    assert result["historical_population_complete"] is True
    assert result["historical_actual_shopping_amount"] == 600000000
    assert result["historical_shopping_to_budget_amount_ratio"] == 0.6667
    assert result["historical_evidence_years"] == [2025, 2026]
    assert result["historical_population_complete_years"] == [2025, 2026]
    assert result["historical_evidence_only_years"] == []
    assert "MULTI_YEAR_VERIFIED_POPULATION" in result[
        "historical_evidence_reasons"
    ]


def test_project_level_qwgjk_can_exceed_aidfa_cap_with_repeated_signals():
    result = future.score_future_budget_evidence(
        _future_row(
            source_layer="DETAIL_EXECUTION",
            project_name="보안등 LED 교체사업",
            section_name="도로",
        ),
        _pattern(),
    )

    assert result["historical_evidence_level"] == "STRONG_HISTORY"
    assert result["historical_evidence_score"] > 75
    assert "AIDFA_STRUCTURAL_CAP" not in result["historical_evidence_reasons"]
    assert "LED" in result["historical_shared_signals"]
    assert "SECURITY_LIGHT" in result["historical_shared_signals"]


def test_future_budget_without_persisted_history_stays_zero_evidence():
    result = future.score_future_budget_evidence(_future_row(), None)

    assert result["historical_evidence_score"] == 0
    assert result["historical_evidence_level"] == "NO_HISTORY"
    assert result["historical_high_projects"] == 0
    assert result["historical_total_budget_projects"] == 0
    assert result["historical_high_match_project_rate"] is None
    assert result["historical_population_complete"] is False


def test_non_led_pole_budget_is_not_forced_into_historical_model():
    result = future.score_future_budget_evidence(
        _future_row(primary_category="ELECTRICAL"),
        _pattern(),
    )

    assert result["historical_evidence_score"] == 0
    assert result["historical_evidence_level"] == "OUTSIDE_LED_POLE"


def test_complete_population_rate_modulates_project_level_evidence():
    strong = future.score_future_budget_evidence(
        _future_row(
            source_layer="DETAIL_EXECUTION",
            project_name="보안등 LED 교체사업",
            section_name="도로",
        ),
        _pattern(
            historical_budget_projects=10,
            high_match_project_rate=0.6,
        ),
    )
    weak = future.score_future_budget_evidence(
        _future_row(
            source_layer="DETAIL_EXECUTION",
            project_name="보안등 LED 교체사업",
            section_name="도로",
        ),
        _pattern(
            historical_budget_projects=100,
            high_match_project_rate=0.06,
        ),
    )

    assert "HISTORICAL_HIGH_MATCH_RATE_50_PLUS" in strong[
        "historical_evidence_reasons"
    ]
    assert "HISTORICAL_HIGH_MATCH_RATE_LOW" in weak[
        "historical_evidence_reasons"
    ]
    assert strong["historical_evidence_score"] > weak[
        "historical_evidence_score"
    ]


def test_partial_second_year_does_not_get_verified_multi_year_bonus():
    verified = future.score_future_budget_evidence(
        _future_row(
            source_layer="DETAIL_EXECUTION",
            project_name="보안등 LED 교체사업",
            section_name="도로",
        ),
        _pattern(
            evidence_years=[2025, 2026],
            population_complete_years=[2025, 2026],
            evidence_only_years=[],
        ),
    )
    partial = future.score_future_budget_evidence(
        _future_row(
            source_layer="DETAIL_EXECUTION",
            project_name="보안등 LED 교체사업",
            section_name="도로",
        ),
        _pattern(
            evidence_years=[2026, 2027],
            population_complete_years=[2026],
            evidence_only_years=[2027],
        ),
    )

    assert "MULTI_YEAR_VERIFIED_POPULATION" in verified[
        "historical_evidence_reasons"
    ]
    assert "MULTI_YEAR_VERIFIED_POPULATION" not in partial[
        "historical_evidence_reasons"
    ]
    assert "PARTIAL_YEAR_EVIDENCE_NO_MULTI_YEAR_BONUS" in partial[
        "historical_evidence_reasons"
    ]
    assert partial["historical_population_complete_years"] == [2026]
    assert partial["historical_evidence_only_years"] == [2027]
    assert verified["historical_evidence_score"] > partial[
        "historical_evidence_score"
    ]


def test_enrich_rows_matches_org_alias_and_sorts_stronger_evidence_first():
    rows = [
        _future_row(org_name="인천광역시 옹진군", budget_amount=100000000),
        _future_row(
            org_name="인천광역시 강화군",
            budget_amount=900000000,
        ),
    ]

    enriched = future.enrich_rows(rows, patterns=[_pattern()])

    assert enriched[0]["org_name"] == "인천광역시 옹진군"
    assert enriched[0]["historical_evidence_score"] > 0
    assert enriched[1]["historical_evidence_score"] == 0


def test_future_stale_incheon_seogu_maps_to_geomdan_lineage_with_project_clue():
    patterns = [
        _pattern(
            org_name="인천광역시 검단구",
            historical_org_names=["인천광역시 서구", "인천광역시 검단구"],
            organization_lineage_applied=True,
        )
    ]
    row = _future_row(
        source_layer="DETAIL_EXECUTION",
        org_name="인천광역시 서구",
        project_name="아라1동 보안등 LED 교체사업",
        section_name="도로",
        source_date="2026-06-01",
    )

    result = future.enrich_rows([row], patterns=patterns)[0]

    assert result["historical_evidence_score"] > 0
    assert result["historical_pattern_org"] == "인천광역시 검단구"
    assert result["historical_pattern_match_basis"].startswith(
        "LINEAGE:INCHON_20260701_서구_TO_검단구:아라1"
    )
    assert "HISTORICAL_ORG_LINEAGE_MATCH" in result[
        "historical_evidence_reasons"
    ]
    assert result["historical_organization_lineage_applied"] is True
    assert result["historical_pattern_historical_org_names"] == [
        "인천광역시 서구",
        "인천광역시 검단구",
    ]


def test_future_stale_incheon_seogu_without_locality_is_fail_closed():
    patterns = [
        _pattern(org_name="인천광역시 서해구"),
        _pattern(org_name="인천광역시 검단구"),
        _pattern(org_name="인천광역시 서구"),
    ]
    row = _future_row(
        source_layer="APPROPRIATION",
        org_name="인천광역시 서구",
        project_name="",
        source_date="2026-06-01",
        field_name="교통및물류",
        section_name="도로조명",
    )

    result = future.enrich_rows([row], patterns=patterns)[0]

    assert result["historical_evidence_score"] == 0
    assert result["historical_evidence_level"] == "NO_HISTORY"
    assert result["historical_pattern_org"] == ""
    assert result["historical_pattern_match_basis"] == (
        "AMBIGUOUS_RETIRED_INCHEON_서구"
    )


def test_future_stale_incheon_seogu_cheongna_maps_to_seohae_lineage():
    patterns = [_pattern(org_name="인천광역시 서해구")]
    row = _future_row(
        source_layer="DETAIL_EXECUTION",
        org_name="인천광역시 서구",
        project_name="청라1동 보안등 LED 교체사업",
        section_name="도로",
    )

    result = future.enrich_rows([row], patterns=patterns)[0]

    assert result["historical_evidence_score"] > 0
    assert result["historical_pattern_org"] == "인천광역시 서해구"
    assert "LINEAGE:INCHON_20260701_서구_TO_서해구:청라1" == result[
        "historical_pattern_match_basis"
    ]


def test_future_stale_gwangju_top_level_maps_to_integrated_special_city():
    patterns = [
        _pattern(
            org_name="전남광주통합특별시",
            historical_org_names=["광주광역시", "전라남도"],
            organization_lineage_applied=True,
        )
    ]
    row = _future_row(
        region_name="전남광주통합특별시",
        org_name="광주광역시",
        source_layer="APPROPRIATION",
        source_date="2026-06-01",
    )

    result = future.enrich_rows([row], patterns=patterns)[0]

    assert result["historical_evidence_score"] == 75
    assert result["historical_pattern_org"] == "전남광주통합특별시"
    assert result["historical_pattern_match_basis"] == (
        "LINEAGE:JEONNAM_GWANGJU_20260701_TOP_LEVEL_MERGE"
    )


def test_future_stale_hwaseong_parent_needs_ward_clue():
    patterns = [_pattern(org_name="경기도 화성시 동탄구")]
    matched = _future_row(
        region_name="경기도",
        org_name="경기도 화성시",
        source_layer="DETAIL_EXECUTION",
        project_name="동탄 보안등 LED 개선사업",
        section_name="도로",
    )
    ambiguous = _future_row(
        region_name="경기도",
        org_name="경기도 화성시",
        source_layer="APPROPRIATION",
        project_name="",
        section_name="도로조명",
    )

    rows = future.enrich_rows([matched, ambiguous], patterns=patterns)
    by_project = {str(row.get("project_name") or ""): row for row in rows}

    assert by_project["동탄 보안등 LED 개선사업"][
        "historical_pattern_org"
    ] == "경기도 화성시 동탄구"
    assert by_project["동탄 보안등 LED 개선사업"][
        "historical_evidence_score"
    ] > 0

    assert by_project[""]["historical_evidence_score"] == 0
    assert by_project[""]["historical_pattern_match_basis"] == (
        "AMBIGUOUS_RETIRED_HWASEONG_PARENT"
    )


def test_current_org_future_match_remains_direct_alias():
    patterns = [_pattern(org_name="인천광역시 검단구")]
    row = _future_row(
        org_name="인천광역시 검단구",
        source_layer="DETAIL_EXECUTION",
        project_name="아라1동 보안등 LED 개선사업",
    )

    result = future.enrich_rows([row], patterns=patterns)[0]

    assert result["historical_evidence_score"] > 0
    assert result["historical_pattern_match_basis"] == "DIRECT_ORG_ALIAS"


def test_enrich_rows_bounded_top_k_preserves_ranking():
    rows = [
        _future_row(
            org_name=f"기관-{index}",
            project_name=f"사업-{index:03d}",
            budget_amount=index * 1000,
        )
        for index in range(300)
    ]

    result = future.enrich_rows(rows, patterns=[], limit=25)

    assert len(result) == 25
    assert [row["budget_amount"] for row in result] == [
        index * 1000 for index in range(299, 274, -1)
    ]


def test_future_budget_rows_with_no_available_history_is_fail_closed(monkeypatch):
    calls = {"patterns": 0}

    monkeypatch.setattr(
        __import__("budget_read_vnext"),
        "screen_budget_rows",
        lambda **kwargs: [_future_row(fiscal_year=2030)],
    )
    monkeypatch.setattr(
        future.budget_shopping_match_store,
        "pattern_history_years",
        lambda **kwargs: {
            "target_fiscal_year": kwargs["target_fiscal_year"],
            "years": [],
            "population_years": [],
            "evidence_only_years": [],
            "basis": "NO_STORED_PRE_TARGET_HISTORY",
        },
    )

    def forbidden_patterns(**kwargs):
        calls["patterns"] += 1
        raise AssertionError(
            "empty history window must not expand to unbounded organization patterns"
        )

    monkeypatch.setattr(
        future.budget_shopping_match_store,
        "organization_patterns",
        forbidden_patterns,
    )

    rows = future.future_budget_rows(
        fiscal_year=2030,
        region="인천광역시",
    )

    assert calls["patterns"] == 0
    assert len(rows) == 1
    assert rows[0]["historical_evidence_score"] == 0
    assert rows[0]["historical_evidence_level"] == "NO_HISTORY"
    assert rows[0]["historical_requested_years"] == []
    assert rows[0]["historical_year_selection_basis"] == (
        "NO_STORED_PRE_TARGET_HISTORY"
    )


def test_future_budget_rows_uses_bounded_budget_and_persisted_patterns(monkeypatch):
    calls = {}

    def fake_budget(**kwargs):
        calls["budget"] = kwargs
        return [_future_row()]

    def fake_patterns(**kwargs):
        calls["patterns"] = kwargs
        return [_pattern()]

    def fake_history_years(**kwargs):
        calls["history_years"] = kwargs
        return {
            "target_fiscal_year": kwargs["target_fiscal_year"],
            "years": [2025, 2026],
            "population_years": [2025, 2026],
            "evidence_only_years": [],
            "basis": "LATEST_AVAILABLE_PRE_TARGET_YEARS",
        }

    monkeypatch.setattr(
        __import__("budget_read_vnext"),
        "screen_budget_rows",
        fake_budget,
    )
    monkeypatch.setattr(
        future.budget_shopping_match_store,
        "organization_patterns",
        fake_patterns,
    )
    monkeypatch.setattr(
        future.budget_shopping_match_store,
        "pattern_history_years",
        fake_history_years,
    )

    rows = future.future_budget_rows(
        fiscal_year=2027,
        region="인천광역시",
        categories=("LIGHTING", "POLE"),
        institution_scope="INCHEON_ONGJIN",
        limit=200,
        result_limit=120,
    )

    assert len(rows) == 1
    assert rows[0]["historical_evidence_score"] == 75
    assert calls["budget"]["fiscal_year"] == 2027
    assert calls["budget"]["region"] == "인천광역시"
    assert calls["budget"]["institution_scope"] == "INCHEON_ONGJIN"
    assert calls["budget"]["limit"] == 200
    assert calls["history_years"]["target_fiscal_year"] == 2027
    assert calls["history_years"]["region"] == "인천광역시"
    assert calls["history_years"]["window"] == 2
    assert calls["patterns"]["fiscal_years"] == (2025, 2026)
    assert calls["patterns"]["region"] == "인천광역시"
    assert rows[0]["historical_requested_years"] == [2025, 2026]
    assert rows[0]["historical_requested_population_years"] == [2025, 2026]
    assert rows[0]["historical_requested_evidence_only_years"] == []
    assert rows[0]["historical_year_selection_basis"] == (
        "LATEST_AVAILABLE_PRE_TARGET_YEARS"
    )
