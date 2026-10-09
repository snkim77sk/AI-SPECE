import budget_read_vnext


def test_budget_institution_names_uses_selected_region_without_source_io(monkeypatch):
    calls = {}

    def fake_names(datasets, **kwargs):
        calls["datasets"] = tuple(datasets)
        calls.update(kwargs)
        return ["서울특별시", "서울특별시 강남구"]

    monkeypatch.setattr(
        budget_read_vnext.budget_storage,
        "current_organization_names",
        fake_names,
    )

    result = budget_read_vnext.budget_institution_names(
        fiscal_year=2026,
        region="서울특별시",
    )

    assert result == ["서울특별시", "서울특별시 강남구"]
    assert calls["fiscal_year"] == 2026
    assert calls["source_layers"] == ("DETAIL_EXECUTION", "EDUCATION")
    assert "서울특별시" in calls["region_terms"]


def test_nationwide_institution_exact_filter_takes_priority_over_legacy_scope():
    spec = budget_read_vnext._institution_scope_spec(
        "인천광역시",
        institution_name="인천광역시 계양구",
        institution_scope="INCHEON_CITY",
    )
    assert spec["exact_names"] == ("인천광역시 계양구",)
    assert spec["contains_terms"] == ()


def test_budget_project_detail_uses_stored_current_record(monkeypatch):
    monkeypatch.setattr(
        budget_read_vnext.budget_storage,
        "current_normalized_record",
        lambda dataset, key, classifier_version="": {
            "dataset": dataset,
            "record_key": key,
            "source_layer": "DETAIL_EXECUTION",
            "fiscal_year": 2026,
            "region_name": "인천광역시",
            "org_name": "인천광역시",
            "dept_name": "도로과",
            "project_code": "P1",
            "project_name": "드림로~원당대로간 도로개설",
            "field_name": "교통및물류",
            "section_name": "도로",
            "account_name": "일반회계",
            "budget_amount": 100,
            "executed_amount": 25,
            "remaining_amount": 75,
            "primary_category": "OTHER",
            "subcategory": "",
            "classification_confidence": 0.55,
            "classification_reason": "stored",
        },
    )

    row = budget_read_vnext.budget_project_detail("budget", "row-1")
    assert row["project_name"] == "드림로~원당대로간 도로개설"
    assert row["dept_name"] == "도로과"
    assert row["raw_source_key"] == "row-1"
    assert row["primary_category"] == "OTHER"
