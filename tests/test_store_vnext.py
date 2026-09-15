import db
import contract_projection
import vnext_store


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "g2b-vnext.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


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
