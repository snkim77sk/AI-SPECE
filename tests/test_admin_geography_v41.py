import admin_geography_v41 as geo


def _shared_forms(left, right):
    left_strong, left_weak = geo.locality_forms(left)
    right_strong, right_weak = geo.locality_forms(right)
    strong = set(left_strong) & set(right_strong)
    weak = (
        (set(left_strong) | set(left_weak))
        & (set(right_strong) | set(right_weak))
    ) - strong
    return strong, weak


def test_current_and_legacy_region_names_are_recognized():
    assert geo.canonical_region("전남광주통합특별시") == "전남광주통합특별시"
    assert geo.canonical_region("강원도 춘천시") == "강원특별자치도"
    assert geo.canonical_region("전라북도 전주시") == "전북특별자치도"
    assert geo.canonical_region("광주광역시 광산구") == "광주광역시"
    assert geo.canonical_region("전라남도 목포시") == "전라남도"


def test_admin_suffix_omission_is_strong_identity():
    strong, weak = _shared_forms("북도면", "북도")
    assert "loc:북도" in strong
    assert weak == set()


def test_numbered_dong_to_unnumbered_dong_is_weak_only():
    strong, weak = _shared_forms("간석3동", "간석동")
    assert strong == set()
    assert "loc:간석" in weak


def test_one_to_one_admin_rename_is_strong_identity():
    strong, weak = _shared_forms("안양8동", "명학동")
    assert "admin:anyang:myeonghak" in strong
    assert weak == set()

    strong, weak = _shared_forms("구지면", "구지읍")
    assert "admin:dalseong:guji" in strong
    assert "loc:구지" in strong


def test_compound_construction_area_ignores_spacing_and_hyphen_format():
    left = geo.compound_location_forms("송도11-1공구 가로등")
    right = geo.compound_location_forms("송도 11 - 1공구 보안등")
    assert set(left) & set(right)


def test_incheon_seogu_to_geomdan_requires_matching_successor_locality():
    basis, evidence = geo.organization_transition_basis(
        budget_org="인천광역시 서구",
        shopping_org="인천광역시 검단구",
        budget_text="아라1동 보안등 LED 교체사업",
        shopping_text="아라1동 보안등 관급자재",
        budget_date="2026-03-01",
        shopping_date="2026-08-01",
    )
    assert basis == "ADMIN_TRANSITION_ORG_MATCH"
    assert any("서구>검단구:아라1" in item for item in evidence)

    basis, evidence = geo.organization_transition_basis(
        budget_org="인천광역시 서구",
        shopping_org="인천광역시 검단구",
        budget_text="청라1동 보안등 LED 교체사업",
        shopping_text="청라1동 보안등 관급자재",
        budget_date="2026-03-01",
        shopping_date="2026-08-01",
    )
    assert basis == ""
    assert evidence == []


def test_incheon_junggu_to_yeongjong_requires_yeongjong_locality():
    basis, evidence = geo.organization_transition_basis(
        budget_org="인천광역시 중구",
        shopping_org="인천광역시 영종구",
        budget_text="운서1동 보안등 정비",
        shopping_text="운서1동 LED 보안등 구매",
        budget_date="2026-06-15",
        shopping_date="2026-08-10",
    )
    assert basis == "ADMIN_TRANSITION_ORG_MATCH"
    assert any("중구>영종구:운서1" in item for item in evidence)


def test_hwaseong_parent_to_new_ward_requires_shared_ward_locality():
    basis, evidence = geo.organization_transition_basis(
        budget_org="경기도 화성시",
        shopping_org="경기도 화성시 동탄구",
        budget_text="동탄 보안등 LED 개선",
        shopping_text="동탄 보안등 관급자재",
        budget_date="2026-01-15",
        shopping_date="2026-03-15",
    )
    assert basis == "ADMIN_TRANSITION_ORG_MATCH"
    assert any("화성시>동탄구:동탄" in item for item in evidence)

    basis, evidence = geo.organization_transition_basis(
        budget_org="경기도 화성시",
        shopping_org="경기도 화성시 동탄구",
        budget_text="서부권 보안등 LED 개선",
        shopping_text="보안등 관급자재",
        budget_date="2026-01-15",
        shopping_date="2026-03-15",
    )
    assert basis == ""
    assert evidence == []


def test_transition_does_not_apply_when_dates_do_not_cross_effective_day():
    basis, evidence = geo.organization_transition_basis(
        budget_org="인천광역시 서구",
        shopping_org="인천광역시 검단구",
        budget_text="아라1동 보안등 LED 교체사업",
        shopping_text="아라1동 보안등 관급자재",
        budget_date="2026-08-01",
        shopping_date="2026-09-01",
    )
    assert basis == ""
    assert evidence == []
