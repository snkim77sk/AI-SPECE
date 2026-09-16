import hashlib
import json

import db
import contract_projection
import vnext_store


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "g2b-vnext.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


def test_existing_raw_snapshot_is_seeded_into_revision_history(monkeypatch, tmp_path):
    path = tmp_path / "pre-revision.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    payload = json.dumps({"bidNtceNm": "기존 수집 공고"}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with db.connect() as conn:
        conn.execute(
            """CREATE TABLE raw_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset TEXT NOT NULL,
                source_system TEXT NOT NULL DEFAULT '',
                source_operation TEXT NOT NULL DEFAULT '',
                source_key TEXT NOT NULL,
                source_date TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL DEFAULT '',
                normalized_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(dataset, source_key)
            )"""
        )
        conn.execute(
            "INSERT INTO raw_records(dataset,source_system,source_key,payload_json,payload_sha256) "
            "VALUES(?,?,?,?,?)",
            ("bid_notice_service", "G2B", "OLD|000", payload, digest),
        )

    vnext_store.ensure_foundation()
    with db.connect() as conn:
        revisions = conn.execute(
            "SELECT payload_json,payload_sha256 FROM raw_record_revisions "
            "WHERE dataset='bid_notice_service' AND source_key='OLD|000'"
        ).fetchall()
        marker = conn.execute(
            "SELECT value FROM app_settings WHERE key='vnext_raw_revision_seed_v1'"
        ).fetchone()
    assert len(revisions) == 1
    assert revisions[0]["payload_sha256"] == digest
    assert json.loads(revisions[0]["payload_json"])["bidNtceNm"] == "기존 수집 공고"
    assert marker["value"] == "complete"


def test_raw_revisions_preserve_changed_source_payloads(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    key = "R26BK00000001|000"
    first = {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidNtceNm": "최초 공고"}
    changed = {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidNtceNm": "변경 공고"}

    vnext_store.preserve_raw("bid_notice_service", key, first, source_system="G2B")
    vnext_store.preserve_raw("bid_notice_service", key, first, source_system="G2B")
    vnext_store.preserve_raw("bid_notice_service", key, changed, source_system="G2B")

    with db.connect() as conn:
        latest = conn.execute(
            "SELECT payload_json FROM raw_records WHERE dataset=? AND source_key=?",
            ("bid_notice_service", key),
        ).fetchone()
        revisions = conn.execute(
            "SELECT payload_json FROM raw_record_revisions WHERE dataset=? AND source_key=? ORDER BY id",
            ("bid_notice_service", key),
        ).fetchall()

    assert json.loads(latest["payload_json"])["bidNtceNm"] == "변경 공고"
    assert [json.loads(row["payload_json"])["bidNtceNm"] for row in revisions] == ["최초 공고", "변경 공고"]


def test_award_result_merges_first_rank_final_award_and_contract(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    key = "R26BK00000001|000"

    vnext_store.upsert_award_result(
        key,
        notice_no="R26BK00000001",
        notice_order="000",
        business_type="용역",
        participant_count=17,
        first_rank_vendor="가격1순위",
        first_rank_bizno="1111111111",
        first_rank_amount=100000000,
    )
    vnext_store.upsert_award_result(
        key,
        final_vendor="최종낙찰업체",
        final_vendor_bizno="2222222222",
        final_award_amount=101000000,
        award_rate=89.012,
    )
    vnext_store.upsert_award_result(
        key,
        contract_no="R26TA0000000100",
        contract_vendor="계약업체",
        contract_vendor_bizno="2222222222",
        contract_amount=101500000,
    )

    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM award_results WHERE source_key=?", (key,)).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["first_rank_vendor"] == "가격1순위"
    assert row["first_rank_amount"] == 100000000
    assert row["final_vendor"] == "최종낙찰업체"
    assert row["final_award_amount"] == 101000000
    assert row["contract_no"] == "R26TA0000000100"
    assert row["contract_vendor"] == "계약업체"
    assert row["contract_amount"] == 101500000


def test_lifecycle_link_is_idempotent(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    vnext_store.save_lifecycle_link(
        "bid_notice", "A|000", "contract", "C1", "HAS_CONTRACT",
        confidence=0.8, reason="first",
    )
    vnext_store.save_lifecycle_link(
        "bid_notice", "A|000", "contract", "C1", "HAS_CONTRACT",
        confidence=1.0, reason="exact",
    )
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM lifecycle_links").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["confidence"] == 1.0
    assert row["reason"] == "exact"


def test_contract_notice_reference_resolves_only_exact_existing_identity(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    notice_key = "R26BK00000001|000"
    vnext_store.preserve_raw(
        "bid_notice_service", notice_key,
        {"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "bidNtceNm": "일반 용역"},
        source_system="G2B", source_operation="getBidPblancListInfoServc",
    )

    assert contract_projection.resolve_notice_key({"ntceNo": "R26BK00000001000"}) == notice_key
    assert contract_projection.resolve_notice_key({"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000"}) == notice_key
    assert contract_projection.resolve_notice_key({"ntceNo": "UNKNOWN"}) == ""


def test_contract_notice_reference_stays_unresolved_when_ambiguous(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    for order in ("000", "001"):
        vnext_store.preserve_raw(
            "bid_notice_service", f"SAME|{order}",
            {"bidNtceNo": "SAME", "bidNtceOrd": order},
            source_system="G2B", source_operation="getBidPblancListInfoServc",
        )
    assert contract_projection.resolve_notice_key({"ntceNo": "SAME"}) == ""
