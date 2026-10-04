import budget_shopping_match_vnext as matcher


def _budget(**overrides):
    row = {
        "raw_source_key": "B1",
        "project_identity": "DETAIL_EXECUTION|2026|2812000|D1|P1|A1",
        "source_layer": "DETAIL_EXECUTION",
        "fiscal_year": 2026,
        "source_date": "2026-03-01",
        "region_name": "인천광역시",
        "org_name": "인천옹진군",
        "dept_name": "도로과",
        "project_code": "P1",
        "project_name": "보안등 LED 교체사업",
        "field_name": "교통및물류",
        "section_name": "도로",
        "account_name": "일반회계",
        "primary_category": "LIGHTING",
        "budget_amount": 300000000,
    }
    row.update(overrides)
    return row


def _shopping(**overrides):
    row = {
        "source_key": "S1",
        "source_date": "2026-06-15",
        "demand_region": "인천광역시",
        "demand_org": "인천광역시 옹진군",
        "primary_category": "LIGHTING",
        "delivery_req_name": "보안등 교체 관급자재",
        "detail_item_name": "LED보안등기구",
        "item_name": "LED 보안등기구 50W",
        "model_name": "TEST-50",
        "vendor_name": "테스트조명",
        "amount": 200000000,
    }
    row.update(overrides)
    return row


def test_score_pair_marks_strong_same_org_led_evidence():
    result = matcher.score_pair(_budget(), _shopping())

    assert result is not None
    assert result["level"] == "HIGH"
    assert result["score"] >= matcher.MIN_HIGH_SCORE
    assert result["organization_basis"] in {"EXACT_ORG_NAME", "ORG_ALIAS_MATCH"}
    assert "LED" in result["shared_signals"]
    assert "SECURITY_LIGHT" in result["shared_signals"]
    assert result["lag_days"] == 106


def test_score_pair_rejects_different_organization():
    result = matcher.score_pair(
        _budget(),
        _shopping(demand_region="서울특별시", demand_org="서울특별시 강남구"),
    )
    assert result is None


def test_score_pair_rejects_different_org_in_same_region():
    result = matcher.score_pair(
        _budget(
            region_name="인천광역시",
            org_name="인천옹진군",
        ),
        _shopping(
            demand_region="인천광역시",
            demand_org="인천광역시 강화군",
        ),
    )
    assert result is None


def test_score_pair_rejects_parent_region_vs_subordinate_org():
    result = matcher.score_pair(
        _budget(
            region_name="인천광역시",
            org_name="인천광역시",
        ),
        _shopping(
            demand_region="인천광역시",
            demand_org="인천광역시 서구",
        ),
    )
    assert result is None


def test_org_aliases_keep_same_org_regional_spelling_variants():
    budget_basis, shared = matcher._organization_basis(
        _budget(
            region_name="인천광역시",
            org_name="인천옹진군",
        ),
        _shopping(
            demand_region="인천광역시",
            demand_org="인천광역시 옹진군",
        ),
    )
    assert budget_basis == "ORG_ALIAS_MATCH"
    assert "옹진군" in shared
    assert "인천" not in shared
    assert "인천광역시" not in shared


def test_top_level_region_short_name_matches_only_top_level_region():
    basis, shared = matcher._organization_basis(
        _budget(
            region_name="서울특별시",
            org_name="서울특별시",
        ),
        _shopping(
            demand_region="서울특별시",
            demand_org="서울시",
        ),
    )
    assert basis == "ORG_ALIAS_MATCH"
    assert "서울" in shared


def test_score_pair_rejects_unrelated_nonlighting_project_text():
    result = matcher.score_pair(
        _budget(project_name="청사 냉난방기 교체", primary_category="LIGHTING"),
        _shopping(),
    )
    assert result is None


def test_cross_lighting_pole_category_can_match_with_shared_streetlight_signal():
    result = matcher.score_pair(
        _budget(
            project_name="가로등 신규 설치 및 유지보수",
            primary_category="LIGHTING",
        ),
        _shopping(
            primary_category="POLE",
            delivery_req_name="가로등주 구매",
            detail_item_name="가로등주",
            item_name="스테인리스 가로등주",
            amount=80000000,
        ),
    )

    assert result is not None
    assert result["level"] == "CANDIDATE"
    assert result["score"] >= matcher.MIN_CANDIDATE_SCORE
    assert result["score"] < matcher.MIN_HIGH_SCORE
    assert "CATEGORY_RELATED" in result["evidence"]
    assert "STREET_LIGHT" in result["shared_signals"]


def test_2025_matching_reads_historical_qwgjk_revisions(monkeypatch):
    history_rows = [_budget(
        source_date="2025-12-31",
        fiscal_year=2025,
        project_identity="DETAIL_EXECUTION|2025|2812000|D1|P1|A1",
    )]
    shopping_rows = [_shopping(
        source_date="2025-06-15",
    )]

    monkeypatch.setattr(
        matcher.budget_read_vnext,
        "qwgjk_history_rows",
        lambda **kwargs: list(history_rows),
    )
    monkeypatch.setattr(
        matcher.budget_read_vnext,
        "screen_budget_rows",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("2025 must read revision history, not current state")
        ),
    )
    monkeypatch.setattr(
        matcher.procurement_read_vnext,
        "shopping_rows",
        lambda **kwargs: list(shopping_rows),
    )

    payload = matcher.historical_match_rows(
        fiscal_year=2025,
        region="인천광역시",
    )

    assert payload["fiscal_year"] == 2025
    assert payload["budget_projects_scanned"] == 1
    assert payload["shopping_rows_scanned"] == 1
    assert len(payload["budget_projects"]) == 1
    assert payload["budget_projects"][0]["budget_org"] == "인천옹진군"
    assert payload["budget_projects"][0]["budget_project_name"] == "보안등 LED 교체사업"
    assert payload["matches"]


def test_full_population_mode_stays_sample_only_until_source_coverage_complete(monkeypatch):
    monkeypatch.setattr(
        matcher,
        "source_population_coverage",
        lambda year: {
            "fiscal_year": int(year),
            "shopping_complete": False,
            "budget_complete": True,
            "source_complete": False,
            "basis": "TEST",
        },
    )
    monkeypatch.setattr(
        matcher,
        "_full_budget_population_for_year",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full budget scan must not run before source coverage")
        ),
    )
    monkeypatch.setattr(
        matcher,
        "_full_shopping_population_for_year",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full shopping scan must not run before source coverage")
        ),
    )
    monkeypatch.setattr(
        matcher,
        "_budget_rows_for_year",
        lambda *args, **kwargs: [_budget()],
    )
    monkeypatch.setattr(
        matcher.procurement_read_vnext,
        "shopping_rows",
        lambda **kwargs: [_shopping()],
    )

    payload = matcher.historical_match_rows(
        fiscal_year=2026,
        full_population=True,
    )

    assert payload["source_population_coverage"]["source_complete"] is False
    assert payload["match_population_complete"] is False
    assert payload["budget_population_scan_complete"] is False
    assert payload["shopping_population_scan_complete"] is False


def test_full_population_mode_marks_complete_only_after_both_full_scans(monkeypatch):
    monkeypatch.setattr(
        matcher,
        "source_population_coverage",
        lambda year: {
            "fiscal_year": int(year),
            "shopping_complete": True,
            "budget_complete": True,
            "source_complete": True,
            "basis": "TEST",
        },
    )
    monkeypatch.setattr(
        matcher,
        "_full_budget_population_for_year",
        lambda *args, **kwargs: ([_budget()], True, 1),
    )
    monkeypatch.setattr(
        matcher,
        "_full_shopping_population_for_year",
        lambda *args, **kwargs: ([_shopping()], True),
    )

    payload = matcher.historical_match_rows(
        fiscal_year=2026,
        full_population=True,
    )

    assert payload["budget_projects_scanned"] == 1
    assert payload["shopping_rows_scanned"] == 1
    assert payload["budget_population_scan_complete"] is True
    assert payload["shopping_population_scan_complete"] is True
    assert payload["match_population_complete"] is True


def test_historical_summary_recommends_2025_when_evidence_sample_is_small(monkeypatch):
    budgets = [_budget()]
    shopping = [_shopping()]

    monkeypatch.setattr(
        matcher.budget_read_vnext,
        "screen_budget_rows",
        lambda **kwargs: list(budgets),
    )
    monkeypatch.setattr(
        matcher.procurement_read_vnext,
        "shopping_rows",
        lambda **kwargs: list(shopping),
    )

    summary = matcher.historical_match_summary(
        fiscal_year=2026,
        region="인천광역시",
    )

    assert summary["budget_projects_scanned"] == 1
    assert summary["shopping_rows_scanned"] == 1
    assert len(summary["budget_projects"]) == 1
    assert summary["budget_projects"][0]["budget_project_identity"]
    assert summary["high_matches"] == 1
    assert summary["high_matched_budget_projects"] == 1
    assert summary["evidence_sufficient_for_pattern_learning"] is False
    assert summary["expand_2025_recommended"] is True
    assert "BUDGET_PROJECT_SAMPLE_SMALL" in summary["expansion_reasons"]


def test_historical_summary_can_be_sufficient_with_broad_high_match_sample(monkeypatch):
    budgets = []
    shopping = []
    for index in range(matcher.MIN_PROJECT_SAMPLE):
        budgets.append(_budget(
            raw_source_key=f"B{index}",
            project_identity=f"DETAIL_EXECUTION|2026|2812000|D1|P{index}|A1",
            project_code=f"P{index}",
            project_name=f"보안등 LED 교체사업 {index}",
            budget_amount=300000000,
        ))
        if index < matcher.MIN_HIGH_MATCHED_PROJECTS:
            shopping.append(_shopping(
                source_key=f"S{index}",
                delivery_req_name=f"보안등 교체 관급자재 {index}",
                amount=100000000,
            ))

    monkeypatch.setattr(
        matcher.budget_read_vnext,
        "screen_budget_rows",
        lambda **kwargs: list(budgets),
    )
    monkeypatch.setattr(
        matcher.procurement_read_vnext,
        "shopping_rows",
        lambda **kwargs: list(shopping),
    )

    summary = matcher.historical_match_summary(
        fiscal_year=2026,
        region="인천광역시",
    )

    assert summary["budget_projects_scanned"] == matcher.MIN_PROJECT_SAMPLE
    assert (
        summary["high_matched_budget_projects"]
        >= matcher.MIN_HIGH_MATCHED_PROJECTS
    )
    assert summary["evidence_sufficient_for_pattern_learning"] is True
    assert summary["expand_2025_recommended"] is False
