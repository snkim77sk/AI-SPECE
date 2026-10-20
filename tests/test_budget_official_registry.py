"""Checked official defaults remain separate from stored budget availability."""
from collections import Counter
import builtins
import importlib
import json
import os
from pathlib import Path
import socket

import pytest

import admin_geography_v41 as geography
import budget_institution_registry_vnext as registry


_CHILD_COUNTS = {
    "서울특별시": 25,
    "부산광역시": 16,
    "대구광역시": 9,
    "인천광역시": 11,
    "대전광역시": 5,
    "울산광역시": 5,
    "세종특별자치시": 0,
    "경기도": 31,
    "강원특별자치도": 18,
    "충청북도": 11,
    "충청남도": 15,
    "전북특별자치도": 14,
    "경상북도": 22,
    "경상남도": 18,
    "제주특별자치도": 2,
    "전남광주통합특별시": 27,
}


def _evidence(name):
    path = Path(__file__).resolve().parents[1] / "docs" / "sources" / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("region,children", _CHILD_COUNTS.items())
def test_exact_official_counts_and_parent_labels(region, children):
    options = registry.official_institutions(region)
    assert len(options) == children + 2  # one province and one education office
    assert len({item["name"] for item in options}) == len(options)
    assert options[0]["name"] == region
    assert options[0]["label"] == region + " 본청"
    assert options[0]["kind"] == "PROVINCE"
    assert options[-1]["kind"] == "EDUCATION_OFFICE"
    assert all(item["label"].startswith(region) for item in options)
    for item in options:
        assert set(item) == {
            "name", "label", "kind", "source_url", "website_url", "official",
        }
        assert item["official"] is True
        assert item["website_url"].startswith(("https://", "http://"))


def test_national_totals_and_distinct_institution_levels():
    assert set(_CHILD_COUNTS) == set(geography.REGIONS)
    options = [
        item
        for region in geography.REGIONS
        for item in registry.official_institutions(region)
    ]
    assert len(options) == 261
    assert Counter(item["kind"] for item in options) == {
        "PROVINCE": 16,
        "LOCAL_GOVERNMENT": 227,
        "ADMINISTRATIVE_CITY": 2,
        "EDUCATION_OFFICE": 16,
    }
    assert len({item["label"] for item in options}) == len(options)
    assert not any("교육지원청" in item["name"] for item in options)
    assert not any("학교" in item["name"] for item in options)


@pytest.mark.parametrize("name,left,right", [
    ("중구", "서울특별시", "부산광역시"),
    ("동구", "부산광역시", "대전광역시"),
    ("고성군", "강원특별자치도", "경상남도"),
])
def test_equal_child_names_keep_distinct_parents(name, left, right):
    left_item = next(item for item in registry.official_institutions(left)
                     if item["name"] == name)
    right_item = next(item for item in registry.official_institutions(right)
                      if item["name"] == name)
    assert left_item["label"] == left + " " + name
    assert right_item["label"] == right + " " + name
    assert left_item["label"] != right_item["label"]
    assert left_item["website_url"] != right_item["website_url"]


def test_jeju_cities_are_administrative_not_local_governments():
    options = registry.official_institutions("제주")
    cities = {item["name"]: item for item in options
              if item["kind"] == "ADMINISTRATIVE_CITY"}
    assert set(cities) == {"제주시", "서귀포시"}
    assert not any(item["kind"] == "LOCAL_GOVERNMENT" for item in options)
    assert cities["제주시"]["label"] == "제주특별자치도 제주시"
    assert cities["서귀포시"]["label"] == "제주특별자치도 서귀포시"


def test_mois_names_kinds_and_exact_websites_match_checked_evidence():
    evidence = _evidence("mois_local_governments_2026-10-20.json")
    assert registry.MOIS_SOURCE_URL == evidence["source_url"]
    assert registry.MOIS_RETRIEVED_DATE == evidence["retrieved_at"] == "2026-10-20"
    for region in evidence["regions"]:
        actual = [item for item in registry.official_institutions(region["label"])
                  if item["kind"] != "EDUCATION_OFFICE"]
        assert actual[0]["website_url"] == region["url"]
        assert actual[0]["source_url"] == evidence["source_url"]
        assert [item["name"] for item in actual[1:]] == [
            child["label"] for child in region["children"]
        ]
        for item, child in zip(actual[1:], region["children"]):
            expected_kind = ("ADMINISTRATIVE_CITY"
                             if child["kind"] == "administrative_city"
                             else "LOCAL_GOVERNMENT")
            assert item["kind"] == expected_kind
            assert item["website_url"] == child["url"]
            assert item["source_url"] == evidence["source_url"]


def test_moe_sixteen_offices_and_exact_websites_match_checked_evidence():
    evidence = _evidence("education_offices_moe_20261020.json")
    assert registry.MOE_SOURCE_URL == evidence["source_url"]
    assert registry.MOE_RETRIEVED_DATE == evidence["retrieved_date"] == "2026-10-20"
    expected = {
        item["region"]: item for item in evidence["institutions"]
    }
    assert len(expected) == 16
    for region in geography.REGIONS:
        offices = [item for item in registry.official_institutions(region)
                   if item["kind"] == "EDUCATION_OFFICE"]
        assert len(offices) == 1
        office = offices[0]
        assert office["name"] == office["label"] == expected[region]["name"]
        assert office["website_url"] == expected[region]["website_url"]
        assert office["source_url"] == expected[region]["source_url"]
    assert expected["전남광주통합특별시"]["name"] == "전남광주통합특별시교육청"


@pytest.mark.parametrize("value", (None, "", "  ", "전국", "알수없는지역"))
def test_blank_or_invalid_region_has_no_defaults(value):
    assert registry.official_institutions(value) == []


@pytest.mark.parametrize("legacy,current", [
    ("충남", "충청남도"),
    ("충북", "충청북도"),
    ("경남", "경상남도"),
    ("경북", "경상북도"),
    ("강원도", "강원특별자치도"),
    ("전라북도", "전북특별자치도"),
])
def test_shared_region_normalization_handles_aliases_and_one_to_one_renames(legacy, current):
    assert registry.official_institutions(legacy) == registry.official_institutions(current)


@pytest.mark.parametrize("historical", ("광주", "광주광역시", "전남", "전라남도"))
def test_historical_predecessors_are_not_rewritten_into_successor_defaults(historical):
    canonical_before = geography.canonical_region(historical)
    assert canonical_before in {"광주광역시", "전라남도"}
    assert registry.official_institutions(historical) == []
    assert geography.canonical_region(historical) == canonical_before


def test_options_are_fresh_and_cannot_mutate_the_snapshot():
    original = registry.official_institutions("충남")
    mutated = registry.official_institutions("충남")
    mutated[0]["name"] = "modified"
    mutated[-1]["website_url"] = "https://invalid.example/"
    mutated.clear()
    assert registry.official_institutions("충남") == original


def test_import_and_all_directory_reads_need_no_files_or_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("official registry must not perform runtime I/O")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(os, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(socket, "socket", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        importlib.reload(registry)
        assert sum(len(registry.official_institutions(region))
                   for region in geography.REGIONS) == 261
