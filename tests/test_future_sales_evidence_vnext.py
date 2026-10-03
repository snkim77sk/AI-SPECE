import future_sales_evidence_vnext as future


def _pattern(**overrides):
    row = {
        "org_name": "인천옹진군",
        "evidence_years": [2025, 2026],
        "high_matched_budget_projects": 6,
        "high_matched_shopping_rows": 8,
        "matched_budget_amount": 900000000,
        "actual_shopping_amount": 600000000,
        "shopping_to_budget_amount_ratio": 0.6667,
        "average_nonnegative_lag_days": 110.0,
        "signal_counts": {"LED": 6, "SECURITY_LIGHT": 5},
        "budget_categories": ["LIGHTING"],
        "shopping_categories": ["LIGHTING"],
        "pattern_basis": "PERSISTED_HIGH_MATCH_EVIDENCE",
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
    assert result["historical_actual_shopping_amount"] == 600000000
    assert result["historical_evidence_years"] == [2025, 2026]


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


def test_non_led_pole_budget_is_not_forced_into_historical_model():
    result = future.score_future_budget_evidence(
        _future_row(primary_category="ELECTRICAL"),
        _pattern(),
    )

    assert result["historical_evidence_score"] == 0
    assert result["historical_evidence_level"] == "OUTSIDE_LED_POLE"


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


def test_future_budget_rows_uses_bounded_budget_and_persisted_patterns(monkeypatch):
    calls = {}

    def fake_budget(**kwargs):
        calls["budget"] = kwargs
        return [_future_row()]

    def fake_patterns(**kwargs):
        calls["patterns"] = kwargs
        return [_pattern()]

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

    rows = future.future_budget_rows(
        fiscal_year=2027,
        region="인천광역시",
        categories=("LIGHTING", "POLE"),
        limit=200,
    )

    assert len(rows) == 1
    assert rows[0]["historical_evidence_score"] == 75
    assert calls["budget"]["fiscal_year"] == 2027
    assert calls["budget"]["region"] == "인천광역시"
    assert calls["budget"]["limit"] == 200
    assert calls["patterns"]["fiscal_years"] == (2025, 2026)
    assert calls["patterns"]["region"] == "인천광역시"
