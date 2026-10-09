import budget_read_vnext


def test_budget_institution_names_uses_selected_region_without_source_io(monkeypatch):
    calls = {}

    def fake_names(datasets, **kwargs):
        calls["datasets"] = tuple(datasets)
        calls.update(kwargs)
        return ["서울특별시", "서울특별시 강남구"]

    monkeypatch.setattr(
        budget_read_vnext.budget_storage,
        "current_institution_names",
        fake_names,
    )

    result = budget_read_vnext.budget_institution_names(
        fiscal_year=2026,
        region="서울특별시",
    )

    assert result == ["서울특별시", "서울특별시 강남구"]
    assert calls["fiscal_year"] == 2026
    assert calls["source_layers"] == ("DETAIL_EXECUTION",)
    assert "서울특별시" in calls["region_terms"]


def test_budget_department_names_follow_selected_institution_without_source_io(monkeypatch):
    calls = {}

    def fake_departments(datasets, **kwargs):
        calls["datasets"] = tuple(datasets)
        calls.update(kwargs)
        return ["도로관리과", "시설과"]

    monkeypatch.setattr(
        budget_read_vnext.budget_storage,
        "current_department_names",
        fake_departments,
    )

    result = budget_read_vnext.budget_department_names(
        fiscal_year=2026,
        region="서울특별시",
        institution_name="서울특별시 강남구",
    )

    assert result == ["도로관리과", "시설과"]
    assert calls["source_layers"] == ("DETAIL_EXECUTION",)
    assert calls["organization_exact_names"] == ("서울특별시 강남구",)
    assert "서울특별시" in calls["region_terms"]


def test_incheon_curated_scope_remains_available_alongside_nationwide_filter():
    spec = budget_read_vnext._institution_scope_spec(
        "인천광역시",
        institution_scope="INCHEON_GENERAL_CONSTRUCTION",
    )
    assert "종합건설본부" in spec["contains_terms"]

    exact = budget_read_vnext._institution_scope_spec(
        "인천광역시",
        institution_name="인천광역시 계양구",
        institution_scope="INCHEON_CITY",
    )
    assert exact["exact_names"] == ("인천광역시 계양구",)
    assert exact["contains_terms"] == ()


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
