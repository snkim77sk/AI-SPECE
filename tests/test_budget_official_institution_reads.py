"""Official selector membership is independent of stored budget availability."""
import copy
import socket
import urllib.request

import pytest

import budget_institution_registry_vnext as registry
import budget_read_vnext as reader
import budget_storage
import vnext_store


ALL_LAYERS = ("DETAIL_EXECUTION", "APPROPRIATION", "EDUCATION")
OPTION_KEYS = {
    "name", "label", "kind", "official", "source_url", "website_url",
    "has_stored", "source_layers",
}


def _stored(monkeypatch, evidence):
    calls = []

    def read(datasets, **kwargs):
        calls.append({"datasets": datasets, **kwargs})
        return copy.deepcopy(evidence)

    monkeypatch.setattr(budget_storage, "current_institution_layers", read)
    return calls


def _by_name(options):
    assert len({item["name"] for item in options}) == len(options)
    return {item["name"]: item for item in options}


@pytest.mark.parametrize("region", ["충남", "충북", "경남", "경북", "인천", "제주"])
def test_official_uncollected_options_retain_identity_and_false_availability(
    monkeypatch, region,
):
    calls = _stored(monkeypatch, [])
    options = reader.budget_institution_options(fiscal_year="2026", region=region)
    actual = _by_name(options)
    expected = registry.official_institutions(region)
    assert expected
    assert len(options) == len(expected)
    for official in expected:
        item = actual[official["name"]]
        assert set(item) == OPTION_KEYS
        assert {key: item[key] for key in official} == official
        assert item["has_stored"] is False
        assert item["source_layers"] == ()
    assert calls == [{
        "datasets": reader.BUDGET_DATASETS,
        "fiscal_year": 2026,
        "source_layers": ALL_LAYERS,
        "region_terms": reader._region_search_terms(region),
    }]


def test_short_full_compact_aliases_merge_availability_and_exact_layer_union(monkeypatch):
    evidence = [
        {"name": "충남천안시", "source_layers": ("DETAIL_EXECUTION",)},
        {"name": "충청남도 천안시", "source_layers": ("APPROPRIATION",)},
        {"name": "천안시", "source_layers": ("EDUCATION", "DETAIL_EXECUTION")},
        {"name": "충남", "source_layers": ("APPROPRIATION",)},
        {"name": "충남도", "source_layers": ("DETAIL_EXECUTION",)},
        {"name": "충청남도", "source_layers": ("DETAIL_EXECUTION",)},
    ]
    original = copy.deepcopy(evidence)
    _stored(monkeypatch, evidence)
    options = reader.budget_institution_options(fiscal_year=2026, region="충청남도")
    actual = _by_name(options)
    assert len(options) == len(registry.official_institutions("충남"))
    assert actual["천안시"]["official"] is True
    assert actual["천안시"]["kind"] == "LOCAL_GOVERNMENT"
    assert actual["천안시"]["has_stored"] is True
    assert actual["천안시"]["source_layers"] == tuple(sorted(ALL_LAYERS))
    assert actual["충청남도"]["kind"] == "PROVINCE"
    assert actual["충청남도"]["source_layers"] == ("APPROPRIATION", "DETAIL_EXECUTION")
    assert actual["공주시"]["has_stored"] is False
    assert evidence == original


def test_aidfa_only_evidence_is_stored_without_claiming_detail_or_education(monkeypatch):
    calls = _stored(monkeypatch, [
        {"name": "충남공주시", "source_layers": ("APPROPRIATION",)},
    ])
    actual = _by_name(reader.budget_institution_options(
        fiscal_year=2027, region="충남", source_layers=("APPROPRIATION",),
    ))
    assert actual["공주시"]["official"] is True
    assert actual["공주시"]["has_stored"] is True
    assert actual["공주시"]["source_layers"] == ("APPROPRIATION",)
    assert actual["천안시"]["has_stored"] is False
    assert calls[0]["fiscal_year"] == 2027
    assert calls[0]["source_layers"] == ("APPROPRIATION",)


def test_education_office_and_school_do_not_become_municipal_government(monkeypatch):
    _stored(monkeypatch, [
        {"name": "충남교육청", "source_layers": ("EDUCATION",)},
        {"name": "충청남도교육청", "source_layers": ("EDUCATION",)},
        {"name": "충남천안공업고등학교", "source_layers": ("EDUCATION",)},
    ])
    options = reader.budget_institution_options(fiscal_year=2026, region="충남")
    actual = _by_name(options)
    assert len(options) == len(registry.official_institutions("충남")) + 1
    assert actual["충청남도교육청"]["kind"] == "EDUCATION_OFFICE"
    assert actual["충청남도교육청"]["official"] is True
    assert actual["충청남도교육청"]["has_stored"] is True
    assert actual["충청남도교육청"]["source_layers"] == ("EDUCATION",)
    assert actual["충청남도"]["kind"] == "PROVINCE"
    assert actual["충청남도"]["has_stored"] is False
    assert actual["천안시"]["kind"] == "LOCAL_GOVERNMENT"
    assert actual["천안시"]["has_stored"] is False
    school = actual["충남천안공업고등학교"]
    assert school["kind"] == "STORED"
    assert school["official"] is False
    assert school["has_stored"] is True
    assert school["source_layers"] == ("EDUCATION",)
    assert school["source_url"] == school["website_url"] == ""


def test_historical_governments_remain_distinct_from_current_successor(monkeypatch):
    _stored(monkeypatch, [
        {"name": "광주광역시", "source_layers": ("DETAIL_EXECUTION",)},
        {"name": "전라남도", "source_layers": ("APPROPRIATION",)},
    ])
    current = "전남광주통합특별시"
    actual = _by_name(reader.budget_institution_options(fiscal_year=2026, region=current))
    assert actual[current]["kind"] == "PROVINCE"
    assert actual[current]["official"] is True
    assert actual[current]["has_stored"] is False
    for name, layer in (("광주광역시", "DETAIL_EXECUTION"), ("전라남도", "APPROPRIATION")):
        assert actual[name]["kind"] == "STORED"
        assert actual[name]["official"] is False
        assert actual[name]["has_stored"] is True
        assert actual[name]["source_layers"] == (layer,)


def test_all_existing_presets_remain_selectable_and_names_helper_compatible(monkeypatch):
    _stored(monkeypatch, [])
    monkeypatch.setattr(budget_storage, "current_institution_names", lambda *a, **kw: [])
    for region, names in reader._REGION_BASE_INSTITUTIONS.items():
        options = reader.budget_institution_options(fiscal_year=2026, region=region)
        option_names = [item["name"] for item in options]
        compatibility_names = reader.budget_institution_names(fiscal_year=2026, region=region)
        assert set(names).issubset(option_names)
        assert compatibility_names == list(names)
        assert all(isinstance(name, str) for name in compatibility_names)
        assert all(reader.institution_name_allowed(region, name, option_names) for name in names)


def test_names_helper_preserves_raw_names_while_typed_options_merge_aliases(monkeypatch):
    evidence = [
        {"name": "충남천안시", "source_layers": ("DETAIL_EXECUTION",)},
        {"name": "충남교육청", "source_layers": ("EDUCATION",)},
        {"name": "충남천안공업고등학교", "source_layers": ("EDUCATION",)},
    ]
    _stored(monkeypatch, evidence)
    monkeypatch.setattr(
        budget_storage, "current_institution_names",
        lambda *args, **kwargs: [item["name"] for item in evidence],
    )
    options = reader.budget_institution_options(fiscal_year=2026, region="충남")
    names = reader.budget_institution_names(fiscal_year=2026, region="충남")
    assert names == [item["name"] for item in evidence]
    option_names = [item["name"] for item in options]
    assert "천안시" in option_names
    assert "충청남도교육청" in option_names
    assert "충남천안공업고등학교" in option_names
    assert all(reader.institution_name_allowed("충남", name, option_names) for name in names)


def test_unmatched_legacy_preset_and_unknown_stored_name_are_not_discarded(monkeypatch):
    _stored(monkeypatch, [{"name": "TEST_STORED_UNKNOWN", "source_layers": ()}])
    monkeypatch.setattr(reader, "_REGION_BASE_INSTITUTIONS", {"충청남도": ("TEST_LEGACY_PRESET",)})
    actual = _by_name(reader.budget_institution_options(fiscal_year=2026, region="충남"))
    assert actual["TEST_LEGACY_PRESET"]["kind"] == "LEGACY"
    assert actual["TEST_LEGACY_PRESET"]["official"] is False
    assert actual["TEST_LEGACY_PRESET"]["has_stored"] is False
    assert actual["TEST_STORED_UNKNOWN"]["kind"] == "STORED"
    assert actual["TEST_STORED_UNKNOWN"]["has_stored"] is True
    assert actual["TEST_STORED_UNKNOWN"]["source_layers"] == ()


@pytest.mark.parametrize("region", ["", "  "])
def test_nationwide_selector_contains_only_stored_institutions(monkeypatch, region):
    evidence = [
        {"name": "충남천안시", "source_layers": ("DETAIL_EXECUTION",)},
        {"name": "충북청주시", "source_layers": ("APPROPRIATION",)},
    ]
    calls = _stored(monkeypatch, evidence)
    actual = _by_name(reader.budget_institution_options(fiscal_year=2026, region=region))
    assert set(actual) == {item["name"] for item in evidence}
    assert all(item["kind"] == "STORED" and item["has_stored"] for item in actual.values())
    assert calls[0]["region_terms"] == ()


def test_option_read_performs_one_name_query_and_no_source_or_project_io(monkeypatch):
    calls = _stored(monkeypatch, [{"name": "충남천안시", "source_layers": ("DETAIL_EXECUTION",)}])
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append((args, kwargs))
        raise AssertionError("institution options must not collect or materialize projects")

    monkeypatch.setattr(budget_storage, "current_institution_names", forbidden)
    monkeypatch.setattr(budget_storage, "current_normalized_rows", forbidden)
    monkeypatch.setattr(budget_storage, "current_raw_rows", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    options = reader.budget_institution_options(fiscal_year=2026, region="충남")
    assert _by_name(options)["천안시"]["has_stored"] is True
    assert len(calls) == 1
    assert forbidden_calls == []


def test_real_disposable_storage_keeps_appropriation_and_education_evidence_separate():
    vnext_store.preserve_raw(
        "budget_appropriation", "official-read-aidfa",
        {"fyr": "2026", "wa_laf_hg_nm": "충남", "laf_hg_nm": "충남공주시",
         "sect_nm": "도로", "biz_bdg_tott_amt": "100"},
        source_system="REGRESSION_FIXTURE", source_operation="AIDFA", source_date="2026-10-20",
    )
    vnext_store.preserve_raw(
        "education_budget", "official-read-education",
        {"fiscalYear": "2026", "officeName": "충남교육청", "schoolName": "충남천안공업고등학교",
         "projectName": "학교 LED 교체", "budget_amount": "200"},
        source_system="REGRESSION_FIXTURE", source_operation="EDUCATION", source_date="2026-10-20",
    )
    actual = _by_name(reader.budget_institution_options(fiscal_year=2026, region="충남"))
    assert actual["공주시"]["has_stored"] is True
    assert actual["공주시"]["source_layers"] == ("APPROPRIATION",)
    assert actual["천안시"]["has_stored"] is False
    assert actual["충청남도교육청"]["source_layers"] == ("EDUCATION",)
    assert actual["충남천안공업고등학교"]["kind"] == "STORED"
    assert actual["충남천안공업고등학교"]["source_layers"] == ("EDUCATION",)
    detail_only = _by_name(reader.budget_institution_options(
        fiscal_year=2026, region="충남", source_layers=("DETAIL_EXECUTION",),
    ))
    assert all(item["has_stored"] is False for item in detail_only.values())
    assert "충남천안공업고등학교" not in detail_only
