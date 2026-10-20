import admin_geography_v41 as geo
import re


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


def test_region_search_terms_include_short_and_formal_province_names():
    for short, formal, stem in (
        ("충남", "충청남도", "충청남"),
        ("충북", "충청북도", "충청북"),
        ("경남", "경상남도", "경상남"),
        ("경북", "경상북도", "경상북"),
    ):
        terms = geo.region_search_terms(short)
        assert terms == geo.region_search_terms(formal)
        assert {short, formal, stem}.issubset(terms)
        assert len(terms) == len(set(terms))


def test_region_search_terms_preserve_renamed_and_merged_history():
    assert {"강원", "강원도", "강원특별자치도"}.issubset(
        geo.region_search_terms("강원특별자치도")
    )
    assert {"전북", "전라북도", "전북특별자치도"}.issubset(
        geo.region_search_terms("전북특별자치도")
    )
    assert {"광주", "광주광역시", "전남", "전라남도", "전남광주통합특별시"}.issubset(
        geo.region_search_terms("전남광주통합특별시")
    )
    assert "전남광주통합특별시" not in geo.region_search_terms("광주")
    assert "전남광주통합특별시" not in geo.region_search_terms("전남")
    assert geo.region_search_terms("알수없는지역") == ()
    assert geo.region_search_terms("") == ()


def test_compact_region_prefixes_in_institution_names():
    for value, canonical in (
        ("충남천안시", "충청남도"),
        ("충남천안시청", "충청남도"),
        ("충청남도천안시시설관리공단", "충청남도"),
        ("충북청주시청", "충청북도"),
        ("경남창원시의회", "경상남도"),
        ("경북포항시", "경상북도"),
        ("서울도봉구", "서울특별시"),
        ("서울도봉구청", "서울특별시"),
        ("서울시청", "서울특별시"),
        ("서울시립대학교", "서울특별시"),
        ("강원철원군", "강원특별자치도"),
        ("전라북도전주시청", "전북특별자치도"),
        ("충남교육청", "충청남도"),
        ("광주광산구청", "광주광역시"),
        ("광주광역시교육청", "광주광역시"),
        ("경기도광주시청", "경기도"),
        ("경기광주시청", "경기도"),
    ):
        assert geo.canonical_region(value) == canonical, value


def test_short_prefixes_do_not_infer_unrelated_or_ambiguous_institutions():
    for value in ("광주시", "광주시청", "광주시립도서관", "충남대학교", "서울대학교", "경기대학교"):
        assert geo.canonical_region(value) == "", value
    assert geo.canonical_region("광주") == "광주광역시"
    assert geo.canonical_region("광주 서구") == "광주광역시"


def test_public_prefix_rules_match_the_python_canonical_contract():
    samples = (
        "충남천안시", "충북 청주시", "경남창원시청", "경상북도포항시",
        "서울도봉구", "경기도광주시청", "광주시청", "광주광역시교육청",
        "강원도철원군", "전북특별자치도전주시", "전남광주통합특별시",
        "전남목포시", "알수없는지역", "", "  충남\t천안시  ",
        "\t충남천안시청", "\n충청북도청주시", "\r\n  경남창원시청",
        "\t\n경상북도 포항시", "  광주시청", "\n광주광역시교육청",
    )
    for sample in samples:
        from_rules = next((
            canonical for pattern, canonical in geo.region_prefix_rules()
            if re.match(pattern, sample)
        ), "")
        assert from_rules == geo.canonical_region(sample), sample
    for pattern, _ in geo.region_prefix_rules():
        assert "(?:" not in pattern
        assert "(?=" not in pattern


def test_grouped_region_rules_preserve_every_registered_alias_and_prefix_boundary():
    individual = tuple(
        (re.compile(pattern), canonical)
        for pattern, canonical in geo._individual_region_prefix_rules()
    )
    grouped = tuple(
        (re.compile(pattern), canonical)
        for pattern, canonical in geo.region_prefix_rules()
    )
    assert len(individual) == 46
    assert len(grouped) == len(geo._KNOWN_REGIONS) == 18
    assert len({canonical for _, canonical in grouped}) == len(grouped)

    aliases = set(geo._KNOWN_REGIONS)
    aliases.update(geo._REGION_EXACT_ALIASES)
    aliases.update(geo._REGION_SHORT_ALIASES)
    aliases.update(geo._region_stem(region) for region in geo._KNOWN_REGIONS)
    # Include suffixes that exercise both accepted institution structures and
    # rejected ambiguities; these are read-parser samples, not a new registry.
    suffixes = (
        "", " ", "\t", "\n", " 천안시", "\t천안시", "천안시", "천안시청",
        "천안시시설관리공단", "도봉구", "광주시", "광주시청", "광산구청",
        "시", "시청", "시립도서관", "도", "도청", "도립", "시의회", "도의회",
        "교육청", "교육지원청", "도교육청", "시교육청", "시설공단", "도시공사",
        "문화재단", "대학교", "광주", "광주통합특별시", "광주광산구청", "통합특별시",
    )
    for alias in aliases:
        for suffix in suffixes:
            for leading in ("", "  ", "\t\n"):
                sample = leading + alias + suffix
                before = next((
                    canonical for pattern, canonical in individual if pattern.match(sample)
                ), "")
                after = next((
                    canonical for pattern, canonical in grouped if pattern.match(sample)
                ), "")
                assert before == after, sample

    order = [canonical for _, canonical in grouped]
    spellings = {region: region for region in geo._KNOWN_REGIONS}
    spellings.update((geo._region_stem(region), region) for region in geo._KNOWN_REGIONS)
    spellings.update(geo._REGION_EXACT_ALIASES)
    spellings.update(geo._REGION_SHORT_ALIASES)
    # Literal prefixes are the only way two rules can both match. Assert every
    # cross-canonical prefix overlap retains the longer name's priority.
    for shorter, short_region in spellings.items():
        for longer, long_region in spellings.items():
            if short_region != long_region and longer.startswith(shorter):
                assert order.index(long_region) < order.index(short_region)
    assert order.index("전남광주통합특별시") < order.index("전라남도")
    for sample in ("전남광주통합특별시", "전남광주광산구청", "전남광주"):
        assert geo.canonical_region(sample) == "전남광주통합특별시"
    assert geo.canonical_region("전남목포시") == "전라남도"


def test_membership_run_compression_matches_independent_canonical_oracle():
    # The oracle uses the original 46 rules, independently of both subsequent
    # optimizations (canonical grouping and boolean run compression).
    original = tuple(
        (re.compile(pattern), canonical)
        for pattern, canonical in geo._individual_region_prefix_rules()
    )
    aliases = set(geo._KNOWN_REGIONS)
    aliases.update(geo._REGION_EXACT_ALIASES)
    aliases.update(geo._REGION_SHORT_ALIASES)
    aliases.update(geo._region_stem(region) for region in geo._KNOWN_REGIONS)
    samples = ["", "unknown", "광주시", "광주시청", "충남대학교"]
    samples.extend(
        leading + alias + suffix
        for alias in aliases
        for suffix in (
            "", " 천안시", "\t천안시", "천안시청", "도봉구", "광주시청",
            "광산구청", "시청", "도청", "교육청", "대학교",
            "광주통합특별시", "광주광산구청",
        )
        for leading in ("", "  ", "\t\n")
    )
    oracle = tuple((sample, next((
        canonical for pattern, canonical in original if pattern.match(sample)
    ), "")) for sample in samples)
    selections = [geo.region_history_members(region) for region in geo._KNOWN_REGIONS]
    selections.extend(((), geo._KNOWN_REGIONS, geo._KNOWN_REGIONS[::2]))
    for members in selections:
        compressed = tuple(
            (re.compile(pattern), included)
            for pattern, included in geo.region_membership_rules(members)
        )
        if members in selections[:len(geo._KNOWN_REGIONS)]:
            assert len(compressed) <= (6 if len(members) > 1 else 3)
        for sample, canonical in oracle:
            actual = next((
                included for pattern, included in compressed if pattern.match(sample)
            ), None)
            expected = canonical in members if canonical else None
            assert actual is expected, (members, sample)

            # Candidate narrowing may overmatch, but must retain every exact
            # selected result, including historical aliases and raw whitespace.
            if expected is True:
                terms = tuple(dict.fromkeys(
                    term for member in members for term in geo.region_search_terms(member)
                ))
                candidate = r"^\s*(" + "|".join(re.escape(term) for term in terms) + ")"
                assert re.match(candidate, sample), (members, sample)


def test_membership_tristate_preserves_first_recognized_field_including_false():
    members = geo.region_history_members("충청남도")
    compressed = tuple(
        (re.compile(pattern), included)
        for pattern, included in geo.region_membership_rules(members)
    )
    fields = (None, "", "unknown", "광주시청", "충남천안시청", "  충청남도", "충북청주시청")

    def membership(value):
        return next((
            included for pattern, included in compressed if pattern.match(value or "")
        ), None)

    for region_name in fields:
        for org_name in fields:
            for institution_name in fields:
                values = (region_name, org_name, institution_name)
                canonical = next((
                    geo.canonical_region(value) for value in values if geo.canonical_region(value)
                ), "")
                expected = canonical in members if canonical else None
                actual = next((
                    value for value in map(membership, values) if value is not None
                ), None)
                assert actual is expected, values
    assert membership("충북청주시청") is False
    assert membership("광주시청") is None


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


def test_current_merged_region_exposes_legacy_history_members():
    assert geo.region_history_members("전남광주통합특별시") == (
        "전남광주통합특별시",
        "광주광역시",
        "전라남도",
    )


def test_pattern_lineage_splits_old_incheon_seogu_by_locality():
    west = geo.organization_lineage(
        org="인천광역시 서구",
        project_text="청라1동 보안등 LED 교체사업",
        source_date="2025-03-01",
        region="인천광역시",
    )
    geomdan = geo.organization_lineage(
        org="인천광역시 서구",
        project_text="아라1동 보안등 LED 교체사업",
        source_date="2025-03-01",
        region="인천광역시",
    )
    unresolved = geo.organization_lineage(
        org="인천광역시 서구",
        project_text="보안등 LED 교체사업",
        source_date="2025-03-01",
        region="인천광역시",
    )

    assert west["org_name"] == "인천광역시 서해구"
    assert geomdan["org_name"] == "인천광역시 검단구"
    assert unresolved["org_name"] == "인천광역시 서구"


def test_pattern_lineage_merges_exact_top_level_predecessor_governments_only():
    gwangju = geo.organization_lineage(
        org="광주광역시",
        project_text="시청 LED 조명 개선",
        source_date="2025-03-01",
        region="광주광역시",
    )
    jeonnam = geo.organization_lineage(
        org="전라남도",
        project_text="도청 LED 조명 개선",
        source_date="2025-03-01",
        region="전라남도",
    )
    child = geo.organization_lineage(
        org="전라남도 목포시",
        project_text="LED 조명 개선",
        source_date="2025-03-01",
        region="전라남도",
    )

    assert gwangju["org_name"] == "전남광주통합특별시"
    assert jeonnam["org_name"] == "전남광주통합특별시"
    assert child["org_name"] == "전라남도 목포시"


def test_future_as_of_resolves_stale_retired_orgs_fail_closed():
    geomdan = geo.organization_lineage(
        org="인천광역시 서구",
        project_text="아라1동 LED 보안등 교체",
        source_date="2026-06-01",
        region="인천광역시",
        as_of_date="2027-12-31",
    )
    assert geomdan["org_name"] == "인천광역시 검단구"

    ambiguous = geo.organization_lineage(
        org="인천광역시 서구",
        project_text="LED 보안등 교체",
        source_date="2026-06-01",
        region="인천광역시",
        as_of_date="2027-12-31",
    )
    assert ambiguous["group_key"] == ""
    assert ambiguous["basis"] == "AMBIGUOUS_RETIRED_INCHEON_서구"

    integrated = geo.organization_lineage(
        org="광주광역시",
        project_text="LED 조명 개선",
        source_date="2026-06-01",
        region="광주광역시",
        as_of_date="2027-12-31",
    )
    assert integrated["org_name"] == "전남광주통합특별시"


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
