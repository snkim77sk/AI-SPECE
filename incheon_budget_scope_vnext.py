"""Incheon budget institution presets for the G2B budget screen.

These scopes are read filters only. They do not change source collection, stored
organization names, or administrative-history evidence.

Current district names follow the 2026-07-01 Incheon administrative system already
used by admin_geography_v41.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetScope:
    code: str
    label: str
    group: str
    exact_names: tuple[str, ...] = ()
    contains_terms: tuple[str, ...] = ()


_SCOPE_ROWS = (
    BudgetScope("INCHEON_ALL", "인천광역시 전체", "전체"),
    BudgetScope(
        "INCHEON_CITY",
        "인천광역시 본청",
        "인천광역시 주요기관",
        exact_names=("인천광역시", "인천광역시청"),
    ),
    BudgetScope(
        "INCHEON_GENERAL_CONSTRUCTION",
        "인천광역시 종합건설본부",
        "인천광역시 주요기관",
        contains_terms=("종합건설본부",),
    ),
    BudgetScope(
        "INCHEON_IFEZ",
        "인천경제자유구역청",
        "인천광역시 주요기관",
        contains_terms=("경제자유구역청", "인천경제자유구역청"),
    ),
    BudgetScope(
        "INCHEON_WATERWORKS",
        "인천광역시 상수도사업본부",
        "인천광역시 주요기관",
        contains_terms=("상수도사업본부",),
    ),
    BudgetScope(
        "INCHEON_URBAN_RAIL",
        "인천광역시 도시철도건설본부",
        "인천광역시 주요기관",
        contains_terms=("도시철도건설본부",),
    ),
    BudgetScope("INCHEON_GANGHWA", "강화군", "군·구", contains_terms=("강화군",)),
    BudgetScope("INCHEON_ONGJIN", "옹진군", "군·구", contains_terms=("옹진군",)),
    BudgetScope("INCHEON_JEMULPO", "제물포구", "군·구", contains_terms=("제물포구",)),
    BudgetScope("INCHEON_YEONGJONG", "영종구", "군·구", contains_terms=("영종구",)),
    BudgetScope("INCHEON_MICHEOHOL", "미추홀구", "군·구", contains_terms=("미추홀구",)),
    BudgetScope("INCHEON_YEONSU", "연수구", "군·구", contains_terms=("연수구",)),
    BudgetScope("INCHEON_NAMDONG", "남동구", "군·구", contains_terms=("남동구",)),
    BudgetScope("INCHEON_BUPYEONG", "부평구", "군·구", contains_terms=("부평구",)),
    BudgetScope("INCHEON_GYEYANG", "계양구", "군·구", contains_terms=("계양구",)),
    BudgetScope("INCHEON_SEOHAE", "서해구", "군·구", contains_terms=("서해구",)),
    BudgetScope("INCHEON_GEOMDAN", "검단구", "군·구", contains_terms=("검단구",)),
)

SCOPES = {row.code: row for row in _SCOPE_ROWS}
DEFAULT_SCOPE = "INCHEON_ALL"


def normalize_scope(value):
    code = str(value or "").strip().upper()
    return code if code in SCOPES else DEFAULT_SCOPE


def scope_row(value):
    return SCOPES[normalize_scope(value)]


def scope_filter(value):
    row = scope_row(value)
    return {
        "code": row.code,
        "label": row.label,
        "exact_names": row.exact_names,
        "contains_terms": row.contains_terms,
        "all_incheon": row.code == DEFAULT_SCOPE,
    }


def grouped_options():
    groups = []
    seen = {}
    for row in _SCOPE_ROWS:
        bucket = seen.get(row.group)
        if bucket is None:
            bucket = {"label": row.group, "options": []}
            groups.append(bucket)
            seen[row.group] = bucket
        bucket["options"].append({"code": row.code, "label": row.label})
    return groups


def _norm(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def matches_row(row, scope):
    spec = scope_filter(scope)
    if spec["all_incheon"]:
        return True

    values = [
        str((row or {}).get("org_name") or ""),
        str((row or {}).get("institution_name") or ""),
        str((row or {}).get("dept_name") or ""),
    ]
    normalized = [_norm(value) for value in values if str(value).strip()]

    exact = {_norm(value) for value in spec["exact_names"]}
    if exact and any(value in exact for value in normalized):
        return True

    contains = tuple(
        _norm(value) for value in spec["contains_terms"] if str(value).strip()
    )
    if contains and any(
        term in value
        for value in normalized
        for term in contains
        if term
    ):
        return True
    return False
