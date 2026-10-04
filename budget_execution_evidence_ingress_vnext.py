"""Validated source-free ingress for compact budget execution evidence.

The import boundary is intentionally narrower than NO1 or the official G2B result
payload. It accepts only the fields required to prove a realized service/work award.
Prediction inputs (participants, scheduled/preliminary prices, recommendations, model
scores) are not part of the contract and are rejected as unknown fields.

No database from another project is read here. Callers must provide a one-way export
document. No network/source request is performed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

import budget_execution_evidence_vnext as execution_evidence

INGRESS_SCHEMA_VERSION = "g2b-execution-evidence-import-v1"
SOURCE_SYSTEMS = ("NO1_EXPORT", "OFFICIAL_G2B_EXPORT")
MAX_IMPORT_ROWS = 5000

_DOCUMENT_KEYS = frozenset({
    "schema_version",
    "source_system",
    "exported_at",
    "rows",
})
_ROW_KEYS = frozenset({
    "business_type",
    "notice_no",
    "notice_order",
    "classification",
    "rebid_no",
    "award_date",
    "organization_code",
    "organization_name",
    "title",
    "winner_id",
    "winner_name",
    "winning_amount",
    "award_rate",
    "source_row_digest",
})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value):
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _text(value, *, limit, code, required=False):
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(code)
    if len(text) > int(limit):
        raise ValueError(code)
    return text


def _integer(value, *, code):
    if value in (None, ""):
        return 0
    try:
        number = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError(code) from None
    if number < 0 or number > 9_000_000_000_000_000:
        raise ValueError(code)
    return number


def _rate(value):
    if value in (None, ""):
        return 0.0
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise ValueError("IMPORT_AWARD_RATE_INVALID") from None
    if number < 0 or number > 1000:
        raise ValueError("IMPORT_AWARD_RATE_INVALID")
    return number


def _aware_iso(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("IMPORT_EXPORTED_AT_REQUIRED")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("IMPORT_EXPORTED_AT_INVALID") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("IMPORT_EXPORTED_AT_TIMEZONE_REQUIRED")
    return parsed.astimezone(dt.timezone.utc).isoformat()


def _award_date(value):
    text = str(value or "").strip()
    try:
        day = dt.date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        raise ValueError("IMPORT_AWARD_DATE_INVALID") from None
    return day.isoformat()


def _business_type(value):
    text = str(value or "").strip().lower()
    aliases = {
        "service": "service",
        "servc": "service",
        "용역": "service",
        "construction": "construction",
        "cnstwk": "construction",
        "work": "construction",
        "공사": "construction",
    }
    result = aliases.get(text, "")
    if not result:
        raise ValueError("IMPORT_BUSINESS_TYPE_INVALID")
    return result


def _source_system(value):
    text = str(value or "").strip().upper()
    if text not in SOURCE_SYSTEMS:
        raise ValueError("IMPORT_SOURCE_SYSTEM_INVALID")
    return text


def _validate_row(raw, *, source_system):
    if not isinstance(raw, dict):
        raise ValueError("IMPORT_ROW_OBJECT_REQUIRED")
    unknown = set(raw) - _ROW_KEYS
    if unknown:
        raise ValueError(
            "IMPORT_ROW_FIELD_NOT_ALLOWED:" + ",".join(sorted(unknown))
        )

    business_type = _business_type(raw.get("business_type"))
    notice_no = _text(
        raw.get("notice_no"),
        limit=80,
        code="IMPORT_NOTICE_NO_INVALID",
        required=True,
    )
    notice_order = _text(
        raw.get("notice_order"),
        limit=20,
        code="IMPORT_NOTICE_ORDER_INVALID",
        required=True,
    )
    classification = _text(
        raw.get("classification"),
        limit=30,
        code="IMPORT_CLASSIFICATION_INVALID",
        required=True,
    )
    rebid_no = _text(
        raw.get("rebid_no"),
        limit=30,
        code="IMPORT_REBID_NO_INVALID",
        required=True,
    )
    award_date = _award_date(raw.get("award_date"))
    organization_code = _text(
        raw.get("organization_code"),
        limit=80,
        code="IMPORT_ORGANIZATION_CODE_INVALID",
    )
    organization_name = _text(
        raw.get("organization_name"),
        limit=240,
        code="IMPORT_ORGANIZATION_NAME_INVALID",
    )
    if not (organization_code or organization_name):
        raise ValueError("IMPORT_ORGANIZATION_REQUIRED")
    title = _text(
        raw.get("title"),
        limit=1000,
        code="IMPORT_TITLE_INVALID",
        required=True,
    )
    winner_id = "".join(
        ch for ch in str(raw.get("winner_id") or "") if ch.isdigit()
    )
    if len(winner_id) > 20:
        raise ValueError("IMPORT_WINNER_ID_INVALID")
    winner_name = _text(
        raw.get("winner_name"),
        limit=240,
        code="IMPORT_WINNER_NAME_INVALID",
    )
    winning_amount = _integer(
        raw.get("winning_amount"),
        code="IMPORT_WINNING_AMOUNT_INVALID",
    )
    award_rate = _rate(raw.get("award_rate"))
    if not (winner_id or winner_name or winning_amount):
        raise ValueError("IMPORT_FINAL_AWARD_FACT_REQUIRED")

    supplied_digest = _text(
        raw.get("source_row_digest"),
        limit=64,
        code="IMPORT_SOURCE_ROW_DIGEST_INVALID",
    ).lower()
    if supplied_digest and _HEX64.fullmatch(supplied_digest) is None:
        raise ValueError("IMPORT_SOURCE_ROW_DIGEST_INVALID")

    core = {
        "business_type": business_type,
        "notice_no": notice_no,
        "notice_order": notice_order,
        "classification": classification,
        "rebid_no": rebid_no,
        "award_date": award_date,
        "organization_code": organization_code,
        "organization_name": organization_name,
        "title": title,
        "winner_id": winner_id,
        "winner_name": winner_name,
        "winning_amount": winning_amount,
        "award_rate": award_rate,
    }
    computed_digest = _sha256(core)
    if supplied_digest and supplied_digest != computed_digest:
        raise ValueError("IMPORT_SOURCE_ROW_DIGEST_MISMATCH")

    source_reference = (
        business_type,
        notice_no,
        notice_order,
        classification,
        rebid_no,
    )
    return {
        **core,
        "source_system": source_system,
        "source_row_digest": computed_digest,
        "source_reference_tuple": source_reference,
    }


def validate_import_document(document):
    """Return canonical validated rows or fail closed before any DB write."""
    if not isinstance(document, dict):
        raise ValueError("IMPORT_DOCUMENT_OBJECT_REQUIRED")
    unknown = set(document) - _DOCUMENT_KEYS
    if unknown:
        raise ValueError(
            "IMPORT_DOCUMENT_FIELD_NOT_ALLOWED:" + ",".join(sorted(unknown))
        )
    if str(document.get("schema_version") or "") != INGRESS_SCHEMA_VERSION:
        raise ValueError("IMPORT_SCHEMA_VERSION_INVALID")

    source_system = _source_system(document.get("source_system"))
    exported_at = _aware_iso(document.get("exported_at"))
    raw_rows = document.get("rows")
    if not isinstance(raw_rows, list):
        raise ValueError("IMPORT_ROWS_LIST_REQUIRED")
    if not raw_rows:
        raise ValueError("IMPORT_ROWS_EMPTY")
    if len(raw_rows) > MAX_IMPORT_ROWS:
        raise ValueError("IMPORT_ROWS_LIMIT_EXCEEDED")

    by_execution = {}
    exact_duplicates = 0
    for raw in raw_rows:
        row = _validate_row(raw, source_system=source_system)
        key = tuple(row["source_reference_tuple"])
        current = by_execution.get(key)
        if current is None:
            by_execution[key] = row
            continue
        if current["source_row_digest"] != row["source_row_digest"]:
            raise ValueError("IMPORT_CONFLICTING_EXECUTION_DUPLICATE")
        exact_duplicates += 1

    rows = []
    for row in by_execution.values():
        item = dict(row)
        item.pop("source_reference_tuple", None)
        rows.append(item)
    rows.sort(
        key=lambda row: (
            row["award_date"],
            row["business_type"],
            row["notice_no"],
            row["notice_order"],
            row["classification"],
            row["rebid_no"],
        )
    )
    digest_payload = {
        "schema_version": INGRESS_SCHEMA_VERSION,
        "source_system": source_system,
        "exported_at": exported_at,
        "rows": rows,
    }
    return {
        **digest_payload,
        "document_digest": _sha256(digest_payload),
        "input_rows": len(raw_rows),
        "validated_rows": len(rows),
        "exact_duplicates_removed": exact_duplicates,
        "source_traffic": False,
        "external_db_connected": False,
    }


def _as_award_row(row):
    """Map the compact import contract to the existing official-fact normalizer."""
    return {
        "bidNtceNo": row["notice_no"],
        "bidNtceOrd": row["notice_order"],
        "bidClsfcNo": row["classification"],
        "rbidNo": row["rebid_no"],
        "award_date": row["award_date"],
        "award_org_code": row["organization_code"],
        "award_org_name": row["organization_name"],
        "notice_name": row["title"],
        "vendor_bizno": row["winner_id"],
        "vendor_name": row["winner_name"],
        "award_amount": row["winning_amount"],
        "award_rate": row["award_rate"],
    }


def import_compact_document(
    document,
    *,
    budgets=None,
    minimum_confidence=execution_evidence.MIN_MATCH_CONFIDENCE,
    persist=False,
):
    """Validate one-way export and build/persist only compact matched evidence."""
    validated = validate_import_document(document)
    budget_rows = list(budgets) if budgets is not None else None

    all_matches = []
    all_invalid = []
    all_ambiguous = []
    facts = 0
    for business_type in execution_evidence.BUSINESS_TYPES:
        group = [
            row for row in validated["rows"]
            if row["business_type"] == business_type
        ]
        if not group:
            continue
        result = execution_evidence.build_compact_evidence(
            [_as_award_row(row) for row in group],
            business_type=business_type,
            budgets=budget_rows,
            minimum_confidence=minimum_confidence,
            persist=False,
        )
        facts += int(result.get("facts") or 0)
        digests = {
            (
                row["notice_no"],
                row["notice_order"],
                row["classification"],
                row["rebid_no"],
            ): row["source_row_digest"]
            for row in group
        }
        for match in result.get("matches") or []:
            item = dict(match)
            key = (
                str(item.get("notice_no") or ""),
                str(item.get("notice_order") or ""),
                str(item.get("bid_clsfc_no") or ""),
                str(item.get("rebid_no") or ""),
            )
            item["ingress_source"] = validated["source_system"]
            item["ingress_row_digest"] = str(digests.get(key) or "")
            all_matches.append(item)
        all_invalid.extend(result.get("invalid") or [])
        all_ambiguous.extend(result.get("ambiguous") or [])

    saved = {
        "saved": 0,
        "ambiguous_source_rows_rejected": 0,
    }
    if persist:
        saved = execution_evidence.save_compact_evidence(all_matches)

    return {
        "schema_version": validated["schema_version"],
        "source_system": validated["source_system"],
        "exported_at": validated["exported_at"],
        "document_digest": validated["document_digest"],
        "input_rows": validated["input_rows"],
        "validated_rows": validated["validated_rows"],
        "exact_duplicates_removed": validated["exact_duplicates_removed"],
        "facts": facts,
        "matches": all_matches,
        "invalid": all_invalid,
        "ambiguous": all_ambiguous,
        "saved": int(saved.get("saved") or 0),
        "storage_ambiguities_rejected": int(
            saved.get("ambiguous_source_rows_rejected") or 0
        ),
        "compact_only": True,
        "raw_payload_saved": False,
        "prediction_fields_accepted": False,
        "source_traffic": False,
        "external_db_connected": False,
    }


def no1_export_contract():
    """Machine-readable contract for a future one-way NO1 exporter."""
    return {
        "schema_version": INGRESS_SCHEMA_VERSION,
        "source_system": "NO1_EXPORT",
        "max_rows": MAX_IMPORT_ROWS,
        "required_document_fields": [
            "schema_version",
            "source_system",
            "exported_at",
            "rows",
        ],
        "row_fields": sorted(_ROW_KEYS),
        "required_row_fields": [
            "business_type",
            "notice_no",
            "notice_order",
            "classification",
            "rebid_no",
            "award_date",
            "title",
        ],
        "organization_requirement": (
            "organization_code OR organization_name"
        ),
        "final_award_requirement": (
            "winner_id OR winner_name OR winning_amount"
        ),
        "forbidden_by_exact_allowlist": [
            "participants",
            "participant_count",
            "scheduled_price",
            "preliminary_prices",
            "recommendations",
            "prediction",
            "model",
            "public_snapshot",
            "payload",
        ],
        "source_traffic": False,
        "external_db_connected": False,
    }
