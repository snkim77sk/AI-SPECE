"""G2B 4.x shopping storage scope: 2026-01-01 forward, lighting and poles only."""
from __future__ import annotations

import datetime as dt

START_DATE = dt.date(2026, 1, 1)
MATCH_BACKFILL_START_DATE = dt.date(2025, 1, 1)
MATCH_BACKFILL_END_DATE = dt.date(2025, 12, 31)
# Keep the deployed checkpoint contract identifier stable. The filter/code contract
# did not change; only the approved bootstrap boundary moved back to 2026-01-01.
# Renaming this value would invalidate resumable 2026-10+ checkpoints unnecessarily.
SCOPE_VERSION = "shopping-lighting-pole-v2-20261001"

# Preserve both the original AI-SPECE lighting codes and the newer exact LED/smart-LED
# detailed-item codes already used by NO1.  Storage eligibility is code-based only.
LIGHTING_DETAIL_ITEM_NOS = frozenset({
    "3910161601",
    "3911151501", "3911151502",
    "3911160301", "3911160302", "3911160304",
    "3911160501",
    "3911160801", "3911160802",
    "3911161101", "3911161102",
    "3911210201", "3911210301",
    "3911980201", "3911980202",
    "3911980301", "3911980302", "3911980303", "3911980304",
})
POLE_DETAIL_ITEM_NOS = frozenset({
    "3911152601", "3911152602", "3911152607",
})
TARGET_DETAIL_ITEM_NOS = LIGHTING_DETAIL_ITEM_NOS | POLE_DETAIL_ITEM_NOS


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def detail_item_no(row):
    for key in (
        "dtilPrdctClsfcNo", "detailPrdctClsfcNo", "detailItemNo",
        "dtlPrdctClsfcNo", "prdctClsfcNo",
    ):
        code = _digits((row or {}).get(key))
        if code:
            return code
    return ""


def target_group(row):
    code = detail_item_no(row)
    if code in POLE_DETAIL_ITEM_NOS:
        return "POLE"
    if code in LIGHTING_DETAIL_ITEM_NOS:
        return "LIGHTING"
    return ""


def should_store(row):
    return bool(target_group(row))


def validate_match_backfill_date(value):
    date = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    if not (MATCH_BACKFILL_START_DATE <= date <= MATCH_BACKFILL_END_DATE):
        raise ValueError(
            "shopping match backfill is restricted to "
            f"{MATCH_BACKFILL_START_DATE.isoformat()}..{MATCH_BACKFILL_END_DATE.isoformat()}"
        )
    return date


def validate_start_date(value):
    date = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
    if date < START_DATE:
        raise ValueError(f"shopping collection starts at {START_DATE.isoformat()}")
    return date
