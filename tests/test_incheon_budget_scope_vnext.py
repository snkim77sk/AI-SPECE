import incheon_budget_scope_vnext as scopes


def test_default_scope_is_incheon_all_and_current_districts_are_present():
    assert scopes.DEFAULT_SCOPE == "INCHEON_ALL"
    labels = [
        option["label"]
        for group in scopes.grouped_options()
        for option in group["options"]
    ]

    assert labels[0] == "인천광역시 전체"
    for name in (
        "강화군", "옹진군", "제물포구", "영종구", "미추홀구",
        "연수구", "남동구", "부평구", "계양구", "서해구", "검단구",
    ):
        assert name in labels


def test_major_incheon_city_agencies_are_selectable():
    labels = {
        option["label"]
        for group in scopes.grouped_options()
        for option in group["options"]
    }

    assert "인천광역시 본청" in labels
    assert "인천광역시 종합건설본부" in labels
    assert "인천경제자유구역청" in labels
    assert "인천광역시 상수도사업본부" in labels
    assert "인천광역시 도시철도건설본부" in labels


def test_scope_matching_uses_org_institution_or_department():
    yeonsu = {
        "org_name": "인천광역시 연수구",
        "dept_name": "도로관리과",
    }
    construction = {
        "org_name": "인천광역시",
        "dept_name": "종합건설본부",
    }
    ifez = {
        "org_name": "인천광역시",
        "institution_name": "인천경제자유구역청",
        "dept_name": "도시건축과",
    }

    assert scopes.matches_row(yeonsu, "INCHEON_YEONSU") is True
    assert scopes.matches_row(yeonsu, "INCHEON_ONGJIN") is False
    assert scopes.matches_row(
        construction, "INCHEON_GENERAL_CONSTRUCTION"
    ) is True
    assert scopes.matches_row(ifez, "INCHEON_IFEZ") is True


def test_main_city_scope_is_exact_not_every_incheon_child_name():
    assert scopes.matches_row(
        {"org_name": "인천광역시"}, "INCHEON_CITY"
    ) is True
    assert scopes.matches_row(
        {"org_name": "인천광역시 연수구"}, "INCHEON_CITY"
    ) is False
