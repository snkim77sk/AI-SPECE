"""Administrative geography history used by G2B matching/read models.

This registry is intentionally conservative. One-to-one renames may share a strong
identity. Split/merge transitions never become equivalent from the parent name
alone; matching requires a successor-locality clue and an effective-date crossing.

Official snapshot: 2026-10-04.
"""
from __future__ import annotations

import datetime as dt
import re

REGIONS = (
    "서울특별시", "부산광역시", "대구광역시", "인천광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "경기도",
    "강원특별자치도", "충청북도", "충청남도", "전북특별자치도",
    "경상북도", "경상남도", "제주특별자치도", "전남광주통합특별시",
)

_LEGACY_TOP_LEVEL_REGIONS = (
    "광주광역시",
    "전라남도",
)

_KNOWN_REGIONS = REGIONS + _LEGACY_TOP_LEVEL_REGIONS

_REGION_EXACT_ALIASES = {
    "강원도": "강원특별자치도",
    "전라북도": "전북특별자치도",
    "전북": "전북특별자치도",
    "전남광주": "전남광주통합특별시",
}

_ONE_TO_ONE_LOCALITY_RENAMES = {
    "안양8동": ("admin:anyang:myeonghak", "명학동"),
    "명학동": ("admin:anyang:myeonghak", "명학동"),
    "안양9동": ("admin:anyang:byeongmokan", "병목안동"),
    "병목안동": ("admin:anyang:byeongmokan", "병목안동"),
    "구지면": ("admin:dalseong:guji", "구지"),
    "구지읍": ("admin:dalseong:guji", "구지"),
}

_INCHEON_EFFECTIVE = dt.date(2026, 7, 1)
_HWASEONG_EFFECTIVE = dt.date(2026, 2, 1)
_JEONNAM_GWANGJU_EFFECTIVE = dt.date(2026, 7, 1)

_INCHEON_SUCCESSOR_LOCALITIES = {
    ("중구", "제물포구"): {
        "신포", "연안", "신흥", "도원", "율목", "동인천", "개항",
    },
    ("동구", "제물포구"): {
        "만석", "화수1", "화평", "화수2", "송현1", "송현2", "송현3",
        "송림1", "송림2", "송림3", "송림4", "송림5", "송림6", "금창",
    },
    ("중구", "영종구"): {
        "영종", "영종1", "영종2", "운서", "운서1", "운서2", "용유",
    },
    ("서구", "서해구"): {
        "검암경서", "연희", "청라1", "청라2", "청라3",
        "가정1", "가정2", "가정3", "신현원창",
        "석남1", "석남2", "석남3", "가좌1", "가좌2", "가좌3", "가좌4",
    },
    ("서구", "검단구"): {
        "검단", "불로대곡", "원당", "당하", "오류왕길", "마전", "아라1", "아라2",
    },
}

_HWASEONG_WARDS = {
    "만세구": {"만세"},
    "효행구": {"효행"},
    "병점구": {"병점"},
    "동탄구": {"동탄"},
}


def _norm(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _date(value):
    text = str(value or "")[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def canonical_region(value):
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if text in _REGION_EXACT_ALIASES:
        return _REGION_EXACT_ALIASES[text]
    for alias, canonical in _REGION_EXACT_ALIASES.items():
        if text.startswith(alias):
            return canonical
    for region in sorted(_KNOWN_REGIONS, key=len, reverse=True):
        if text.startswith(region):
            return region
    # Conservative common short forms. Do not turn old 광주/전남 into the merged
    # current region automatically; historical data must retain its source identity.
    short_aliases = {
        "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
        "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
        "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
        "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
        "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
        "제주": "제주특별자치도",
    }
    for alias, canonical in sorted(short_aliases.items(), key=lambda x: len(x[0]), reverse=True):
        if text == alias or text.startswith(alias + " "):
            return canonical
    return ""


def locality_forms(token):
    """Return strong/weak canonical identity forms for one textual token."""
    raw = str(token or "").strip()
    normalized = _norm(raw)
    if len(normalized) < 2:
        return {}, {}

    strong = {}
    weak = {}

    rename = _ONE_TO_ONE_LOCALITY_RENAMES.get(normalized)
    if rename:
        strong[rename[0]] = rename[1]

    suffix = re.search(r"(읍|면|동|리|구)$", normalized)
    if suffix:
        stem = normalized[: -len(suffix.group(1))]
        if len(stem) >= 2:
            strong[f"loc:{stem}"] = stem
            base = re.sub(r"\d+$", "", stem)
            if base != stem and len(base) >= 2:
                weak[f"loc:{base}"] = base
    else:
        strong[f"loc:{normalized}"] = raw

    return strong, weak


def compound_location_forms(value):
    """Extract spacing/punctuation-insensitive compound locality phrases."""
    text = str(value or "")
    strong = {}
    # e.g. 송도11-1공구 == 송도 11-1공구
    for match in re.finditer(
        r"([가-힣]{2,}\s*\d+(?:\s*[-·]\s*\d+)?\s*공구)",
        text,
    ):
        normalized = _norm(match.group(1))
        if normalized:
            strong[f"compound:{normalized}"] = normalized
    return strong


def region_history_members(region):
    """Return current region plus predecessor regions needed for historical reads."""
    selected = canonical_region(region)
    if not selected:
        return ()
    if selected == "전남광주통합특별시":
        return (
            "전남광주통합특별시",
            "광주광역시",
            "전라남도",
        )
    return (selected,)


def _incheon_successor_for_text(old_suffix, text):
    normalized = _norm(text)
    matches = []
    for (source_suffix, successor_suffix), localities in _INCHEON_SUCCESSOR_LOCALITIES.items():
        if source_suffix != old_suffix:
            continue
        for locality in sorted(localities, key=len, reverse=True):
            key = _norm(locality)
            if key and key in normalized:
                matches.append((successor_suffix, locality))
                break
    unique = {item[0] for item in matches}
    if len(unique) != 1:
        return "", ""
    successor = next(iter(unique))
    locality = next(item[1] for item in matches if item[0] == successor)
    return successor, locality


def organization_lineage(
    *,
    org,
    project_text="",
    source_date="",
    region="",
):
    """Map one historical organization row to a safe current-lineage group.

    Split predecessors require a locality clue; otherwise they remain separate.
    Exact top-level predecessor governments may flow into their legally merged
    successor after the effective-date relation is known.
    """
    raw_org = str(org or "").strip()
    normalized = _norm(raw_org)
    day = _date(source_date)
    basis = "SOURCE_ORG"

    if not raw_org:
        return {
            "group_key": "",
            "org_name": "",
            "source_org": "",
            "basis": basis,
        }

    # Current organizations retain themselves.
    current_suffixes = (
        "제물포구", "영종구", "서해구", "검단구",
        "만세구", "효행구", "병점구", "동탄구",
    )
    if any(normalized.endswith(_norm(suffix)) for suffix in current_suffixes):
        return {
            "group_key": _norm(raw_org),
            "org_name": raw_org,
            "source_org": raw_org,
            "basis": "CURRENT_ORG",
        }

    # Old Incheon organizations before 2026-07-01.
    if day is not None and day < _INCHEON_EFFECTIVE and "인천" in normalized:
        if normalized.endswith(_norm("동구")):
            current = "인천광역시 제물포구"
            return {
                "group_key": _norm(current),
                "org_name": current,
                "source_org": raw_org,
                "basis": "INCHON_20260701_DONGGU_TO_JEMULPO",
            }

        for old_suffix in ("중구", "서구"):
            if normalized.endswith(_norm(old_suffix)):
                successor, locality = _incheon_successor_for_text(
                    old_suffix,
                    project_text,
                )
                if successor:
                    current = f"인천광역시 {successor}"
                    return {
                        "group_key": _norm(current),
                        "org_name": current,
                        "source_org": raw_org,
                        "basis": (
                            f"INCHON_20260701_{old_suffix}_TO_{successor}:{locality}"
                        ),
                    }

    # Old Hwaseong parent rows can only move into a new ward with an explicit ward clue.
    if (
        day is not None
        and day < _HWASEONG_EFFECTIVE
        and normalized.endswith(_norm("화성시"))
    ):
        text = _norm(project_text)
        matches = [
            ward
            for ward, localities in _HWASEONG_WARDS.items()
            if any(_norm(locality) in text for locality in localities)
        ]
        if len(matches) == 1:
            current = f"경기도 화성시 {matches[0]}"
            return {
                "group_key": _norm(current),
                "org_name": current,
                "source_org": raw_org,
                "basis": f"HWASEONG_20260201_TO_{matches[0]}",
            }

    # Top-level merged government: exact predecessor governments only.
    if (
        day is not None
        and day < _JEONNAM_GWANGJU_EFFECTIVE
        and normalized in {_norm("광주광역시"), _norm("전라남도")}
    ):
        current = "전남광주통합특별시"
        return {
            "group_key": _norm(current),
            "org_name": current,
            "source_org": raw_org,
            "basis": "JEONNAM_GWANGJU_20260701_TOP_LEVEL_MERGE",
        }

    return {
        "group_key": _norm(raw_org),
        "org_name": raw_org,
        "source_org": raw_org,
        "basis": basis,
    }


def organization_index_keys(name):
    """Broad candidate-family keys; final score still applies transition guards."""
    text = _norm(name)
    keys = set()
    if not text:
        return keys

    if "인천" in text:
        if any(part in text for part in ("중구", "영종구", "제물포구")):
            keys.add("admin-family:incheon:junggu")
        if any(part in text for part in ("동구", "제물포구")):
            keys.add("admin-family:incheon:donggu")
        if any(part in text for part in ("서구", "서해구", "검단구")):
            keys.add("admin-family:incheon:seogu")
    if "화성시" in text:
        if text.endswith("화성시") or any(ward in text for ward in _HWASEONG_WARDS):
            keys.add("admin-family:hwaseong:wards")
    return keys


def _org_suffix(name, suffix):
    return _norm(name).endswith(_norm(suffix))


def _straddles(left_date, right_date, effective):
    left = _date(left_date)
    right = _date(right_date)
    if left is None or right is None:
        return False
    return (left < effective <= right) or (right < effective <= left)


def _shared_locality(text_a, text_b, localities):
    a = _norm(text_a)
    b = _norm(text_b)
    for locality in sorted(localities, key=len, reverse=True):
        key = _norm(locality)
        if key and key in a and key in b:
            return locality
    return ""


def organization_transition_basis(
    *,
    budget_org,
    shopping_org,
    budget_text,
    shopping_text,
    budget_date,
    shopping_date,
):
    """Return a safe administrative-transition org match basis or empty values."""
    pairs = (
        (budget_org, shopping_org, budget_text, shopping_text, budget_date, shopping_date),
        (shopping_org, budget_org, shopping_text, budget_text, shopping_date, budget_date),
    )

    for old_org, new_org, old_text, new_text, old_date, new_date in pairs:
        if _straddles(old_date, new_date, _INCHEON_EFFECTIVE):
            for (old_suffix, new_suffix), localities in _INCHEON_SUCCESSOR_LOCALITIES.items():
                if _org_suffix(old_org, old_suffix) and _org_suffix(new_org, new_suffix):
                    locality = _shared_locality(old_text, new_text, localities)
                    if locality:
                        return (
                            "ADMIN_TRANSITION_ORG_MATCH",
                            [f"INCHON_20260701:{old_suffix}>{new_suffix}:{locality}"],
                        )

        if _straddles(old_date, new_date, _HWASEONG_EFFECTIVE):
            if _org_suffix(old_org, "화성시"):
                for ward, localities in _HWASEONG_WARDS.items():
                    if _org_suffix(new_org, f"화성시{ward}") or _org_suffix(new_org, ward):
                        locality = _shared_locality(old_text, new_text, localities)
                        if locality:
                            return (
                                "ADMIN_TRANSITION_ORG_MATCH",
                                [f"HWASEONG_20260201:화성시>{ward}:{locality}"],
                            )

    return "", []
