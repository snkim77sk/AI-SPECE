import datetime as dt
from pathlib import Path

import budget_execution_evidence_ingress_vnext as ingress
import budget_execution_evidence_vnext as evidence
import db
import vnext_source_guard


def _budget(
    name="솔빛도서관 별빛마당 LED 조명 개선사업",
    *,
    key="B1",
    category="LIGHTING",
):
    return {
        "project_identity": f"DETAIL_EXECUTION|2026|4111000|D1|{key}|A1",
        "raw_source_key": key,
        "source_layer": "DETAIL_EXECUTION",
        "fiscal_year": 2026,
        "org_code": "4111000",
        "org_name": "수원시",
        "project_code": key,
        "project_name": name,
        "primary_category": category,
        "snapshot_date": "2026-06-01",
    }


def _row(**overrides):
    row = {
        "business_type": "service",
        "notice_no": "R26BK000001",
        "notice_order": "000",
        "classification": "0",
        "rebid_no": "0",
        "award_date": "2026-09-20",
        "organization_code": "4111000",
        "organization_name": "수원시",
        "title": "솔빛도서관 별빛마당 조명개선 실시설계용역",
        "winner_id": "1234567890",
        "winner_name": "낙찰업체",
        "winning_amount": 12345678,
        "award_rate": 87.745,
        "source_row_digest": "",
    }
    row.update(overrides)
    return row


def _document(rows=None, **overrides):
    document = {
        "schema_version": ingress.INGRESS_SCHEMA_VERSION,
        "source_system": "NO1_EXPORT",
        "exported_at": "2026-10-04T23:30:00+09:00",
        "rows": list(rows if rows is not None else [_row()]),
    }
    document.update(overrides)
    return document


def test_no1_export_contract_is_minimal_and_prediction_fields_forbidden():
    contract = ingress.no1_export_contract()

    assert contract["source_system"] == "NO1_EXPORT"
    assert contract["max_rows"] == 5000
    assert "notice_no" in contract["row_fields"]
    assert "winning_amount" in contract["row_fields"]
    assert "participants" not in contract["row_fields"]
    assert "scheduled_price" not in contract["row_fields"]
    assert "preliminary_prices" not in contract["row_fields"]
    assert "recommendations" not in contract["row_fields"]
    assert "prediction_fields_accepted" not in contract
    assert contract["source_traffic"] is False
    assert contract["external_db_connected"] is False


def test_validate_import_document_canonicalizes_timezone_and_digest():
    result = ingress.validate_import_document(_document())

    assert result["source_system"] == "NO1_EXPORT"
    assert result["exported_at"] == "2026-10-04T14:30:00+00:00"
    assert result["input_rows"] == 1
    assert result["validated_rows"] == 1
    assert len(result["document_digest"]) == 64
    assert len(result["rows"][0]["source_row_digest"]) == 64
    assert result["source_traffic"] is False
    assert result["external_db_connected"] is False


def test_unknown_prediction_field_rejects_whole_import_document():
    row = _row()
    row["participants"] = [{"company_id": "x"}]

    try:
        ingress.validate_import_document(_document([row]))
    except ValueError as exc:
        assert str(exc).startswith("IMPORT_ROW_FIELD_NOT_ALLOWED:")
        assert "participants" in str(exc)
    else:
        raise AssertionError("prediction fields must fail closed")


def test_document_rejects_raw_payload_and_public_snapshot():
    for key in ("payload", "public_snapshot", "recommendations"):
        row = _row()
        row[key] = {}
        try:
            ingress.validate_import_document(_document([row]))
        except ValueError as exc:
            assert str(exc).startswith("IMPORT_ROW_FIELD_NOT_ALLOWED:")
        else:
            raise AssertionError(f"{key} must not cross the compact boundary")


def test_import_requires_timezone_aware_export_timestamp():
    try:
        ingress.validate_import_document(
            _document(exported_at="2026-10-04T23:30:00")
        )
    except ValueError as exc:
        assert str(exc) == "IMPORT_EXPORTED_AT_TIMEZONE_REQUIRED"
    else:
        raise AssertionError("naive export time must fail closed")


def test_exact_duplicate_execution_is_deduplicated_before_matching():
    row = _row()
    result = ingress.validate_import_document(
        _document([row, dict(row)])
    )

    assert result["input_rows"] == 2
    assert result["validated_rows"] == 1
    assert result["exact_duplicates_removed"] == 1


def test_conflicting_duplicate_execution_rejects_document():
    one = _row()
    two = _row(winning_amount=99999999)

    try:
        ingress.validate_import_document(_document([one, two]))
    except ValueError as exc:
        assert str(exc) == "IMPORT_CONFLICTING_EXECUTION_DUPLICATE"
    else:
        raise AssertionError("conflicting official execution must fail closed")


def test_supplied_row_digest_must_match_canonical_compact_row():
    validated = ingress.validate_import_document(_document())
    good_digest = validated["rows"][0]["source_row_digest"]

    good = _row(source_row_digest=good_digest)
    assert (
        ingress.validate_import_document(_document([good]))
        ["rows"][0]["source_row_digest"]
        == good_digest
    )

    bad = _row(source_row_digest="a" * 64)
    try:
        ingress.validate_import_document(_document([bad]))
    except ValueError as exc:
        assert str(exc) == "IMPORT_SOURCE_ROW_DIGEST_MISMATCH"
    else:
        raise AssertionError("row digest mismatch must fail closed")


def test_existing_41135_evidence_table_adds_provenance_before_index():
    with db.connect() as conn:
        conn.execute(
            """CREATE TABLE budget_execution_evidence(
                   evidence_key TEXT PRIMARY KEY,
                   fiscal_year INTEGER NOT NULL,
                   evidence_type TEXT NOT NULL,
                   source_business_type TEXT NOT NULL,
                   source_reference TEXT NOT NULL,
                   budget_project_identity TEXT NOT NULL
               )"""
        )

    evidence.ensure_schema()

    with db.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(budget_execution_evidence)"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' "
                "AND tbl_name='budget_execution_evidence'"
            ).fetchall()
        }

    assert "ingress_source" in columns
    assert "ingress_row_digest" in columns
    assert "ix_budget_execution_evidence_ingress" in indexes


def test_import_matches_budget_and_persists_only_compact_provenance():
    result = ingress.import_compact_document(
        _document(),
        budgets=[_budget()],
        persist=True,
    )

    assert result["facts"] == 1
    assert len(result["matches"]) == 1
    assert result["saved"] == 1
    assert result["raw_payload_saved"] is False
    assert result["prediction_fields_accepted"] is False
    assert result["source_traffic"] is False
    assert result["external_db_connected"] is False

    rows = evidence.evidence_rows(fiscal_year=2026)
    assert len(rows) == 1
    row = rows[0]
    assert row["ingress_source"] == "NO1_EXPORT"
    assert len(row["ingress_row_digest"]) == 64
    assert row["award_title"] == (
        "솔빛도서관 별빛마당 조명개선 실시설계용역"
    )

    with db.connect() as conn:
        columns = {
            item["name"]
            for item in conn.execute(
                "PRAGMA table_info(budget_execution_evidence)"
            ).fetchall()
        }
    assert "payload_json" not in columns
    assert "participants_json" not in columns
    assert "scheduled_price" not in columns
    assert "preliminary_price_json" not in columns


def test_official_g2b_export_uses_same_compact_contract():
    result = ingress.import_compact_document(
        _document(source_system="OFFICIAL_G2B_EXPORT"),
        budgets=[_budget()],
        persist=False,
    )

    assert result["source_system"] == "OFFICIAL_G2B_EXPORT"
    assert len(result["matches"]) == 1
    assert result["saved"] == 0


def test_service_and_construction_rows_can_share_one_document():
    construction = _row(
        business_type="construction",
        notice_no="R26BK000002",
        title="솔빛도서관 별빛마당 LED 조명 전기공사",
        winning_amount=22222222,
    )
    result = ingress.import_compact_document(
        _document([_row(), construction]),
        budgets=[_budget()],
        persist=False,
    )

    assert result["facts"] == 2
    assert {
        row["evidence_type"] for row in result["matches"]
    } == {
        evidence.SERVICE_AWARD,
        evidence.LIGHTING_WORK_AWARD,
    }


def test_unrelated_award_can_cross_ingress_but_is_not_saved_as_evidence():
    unrelated = _row(
        title="수원시 청사 복사용지 구매",
    )
    result = ingress.import_compact_document(
        _document([unrelated]),
        budgets=[_budget()],
        persist=True,
    )

    assert result["validated_rows"] == 1
    assert result["facts"] == 0
    assert result["saved"] == 0
    assert result["matches"] == []
    assert len(result["invalid"]) == 1


def test_ingress_module_is_source_free_and_no_no1_runtime_import():
    source = Path(
        "budget_execution_evidence_ingress_vnext.py"
    ).read_text(encoding="utf-8")

    assert "vnext_http" not in source
    assert "get_service_key" not in source
    assert "psycopg" not in source
    assert "sqlalchemy" not in source
    assert "snkim77sk/NO1" not in source
    assert "from no1" not in source
    assert all(
        "ScsbidInfoService" not in path
        for path in vnext_source_guard._G2B_SMALL_VALIDATION_PATHS
    )


def test_import_row_limit_is_bounded():
    rows = [_row(notice_no=f"R26BK{i:06d}") for i in range(5001)]
    try:
        ingress.validate_import_document(_document(rows))
    except ValueError as exc:
        assert str(exc) == "IMPORT_ROWS_LIMIT_EXCEEDED"
    else:
        raise AssertionError("oversized compact import must fail closed")
