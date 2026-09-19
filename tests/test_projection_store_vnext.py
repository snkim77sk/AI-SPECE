import db
import projection_store_vnext
import vnext_schema
import vnext_store


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "projection-store.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


def _row(source_key):
    with db.connect() as conn:
        value = conn.execute(
            "SELECT * FROM award_results WHERE source_key=?",
            (source_key,),
        ).fetchone()
        return dict(value) if value else None


def test_opening_group_revision_clears_old_first_rank(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    key = "A|000|1|0"
    raw = "A|000|1|0"
    projection_store_vnext.replace_fact_group(
        key,
        "opening",
        raw,
        base_facts={"notice_no": "A", "notice_order": "000", "business_type": "용역"},
        opening_date="2026-09-16",
        participant_count=12,
        first_rank_vendor="기존1순위",
        first_rank_bizno="1111111111",
        first_rank_amount=100,
    )
    projection_store_vnext.replace_fact_group(
        key,
        "opening",
        raw,
        base_facts={"notice_no": "A", "notice_order": "000", "business_type": "용역"},
        opening_date="2026-09-16",
        participant_count=12,
        first_rank_vendor="",
        first_rank_bizno="",
        first_rank_amount=0,
    )
    row = _row(key)
    assert row["opening_raw_key"] == raw
    assert row["first_rank_vendor"] == ""
    assert row["first_rank_bizno"] == ""
    assert row["first_rank_amount"] == 0
    assert row["participant_count"] == 12


def test_final_award_group_revision_clears_old_final_facts(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    key = "A|000|1|0"
    raw = "FINAL-A"
    projection_store_vnext.replace_fact_group(
        key,
        "final_award",
        raw,
        base_facts={"notice_no": "A", "notice_order": "000"},
        final_vendor="기존낙찰자",
        final_vendor_bizno="2222222222",
        final_award_amount=200,
        award_rate=88.1,
    )
    projection_store_vnext.replace_fact_group(
        key,
        "final_award",
        raw,
        base_facts={"notice_no": "A", "notice_order": "000"},
        final_vendor="",
        final_vendor_bizno="",
        final_award_amount=0,
        award_rate=0.0,
    )
    row = _row(key)
    assert row["final_award_raw_key"] == raw
    assert row["final_vendor"] == ""
    assert row["final_vendor_bizno"] == ""
    assert row["final_award_amount"] == 0
    assert row["award_rate"] == 0


def test_contract_group_revision_clears_old_single_vendor_but_keeps_contract(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    key = "A|000|1|0"
    raw = "CONTRACT-A"
    projection_store_vnext.replace_fact_group(
        key,
        "contract",
        raw,
        contract_no="C-1",
        contract_vendor="기존단독업체",
        contract_vendor_bizno="3333333333",
        contract_amount=300,
    )
    projection_store_vnext.replace_fact_group(
        key,
        "contract",
        raw,
        contract_no="C-1",
        contract_vendor="",
        contract_vendor_bizno="",
        contract_amount=300,
    )
    row = _row(key)
    assert row["contract_raw_key"] == raw
    assert row["contract_no"] == "C-1"
    assert row["contract_amount"] == 300
    assert row["contract_vendor"] == ""
    assert row["contract_vendor_bizno"] == ""


def test_same_raw_source_reassignment_clears_old_execution(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    old_key = "A|000|1|0"
    new_key = "A|000|1|1"
    raw = "OPENING-RAW-A"
    projection_store_vnext.replace_fact_group(
        old_key,
        "opening",
        raw,
        base_facts={"notice_no": "A", "notice_order": "000"},
        first_rank_vendor="과거집행",
        first_rank_bizno="1111111111",
        first_rank_amount=100,
    )
    projection_store_vnext.replace_fact_group(
        new_key,
        "opening",
        raw,
        base_facts={"notice_no": "A", "notice_order": "000"},
        first_rank_vendor="현재집행",
        first_rank_bizno="2222222222",
        first_rank_amount=110,
    )
    old = _row(old_key)
    new = _row(new_key)
    assert old["opening_raw_key"] == ""
    assert old["first_rank_vendor"] == ""
    assert old["first_rank_amount"] == 0
    assert new["opening_raw_key"] == raw
    assert new["first_rank_vendor"] == "현재집행"
    assert new["first_rank_amount"] == 110


def test_old_award_results_table_migrates_provenance_columns_without_losing_row(monkeypatch, tmp_path):
    path = tmp_path / "old-award.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS award_results")
        conn.execute(
            """CREATE TABLE award_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notice_no TEXT NOT NULL DEFAULT '',
                notice_order TEXT NOT NULL DEFAULT '',
                business_type TEXT NOT NULL DEFAULT '',
                opening_date TEXT NOT NULL DEFAULT '',
                participant_count INTEGER NOT NULL DEFAULT 0,
                first_rank_vendor TEXT NOT NULL DEFAULT '',
                first_rank_bizno TEXT NOT NULL DEFAULT '',
                first_rank_amount INTEGER NOT NULL DEFAULT 0,
                final_vendor TEXT NOT NULL DEFAULT '',
                final_vendor_bizno TEXT NOT NULL DEFAULT '',
                final_award_amount INTEGER NOT NULL DEFAULT 0,
                award_rate REAL NOT NULL DEFAULT 0,
                contract_no TEXT NOT NULL DEFAULT '',
                contract_vendor TEXT NOT NULL DEFAULT '',
                contract_vendor_bizno TEXT NOT NULL DEFAULT '',
                contract_amount INTEGER NOT NULL DEFAULT 0,
                source_key TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            "INSERT INTO award_results(notice_no,notice_order,first_rank_vendor,source_key) VALUES(?,?,?,?)",
            ("A", "000", "기존1순위", "A|000|1|0"),
        )
        vnext_schema.ensure_vnext_schema(conn)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(award_results)").fetchall()}
        row = conn.execute(
            "SELECT notice_no,notice_order,first_rank_vendor,opening_raw_key,final_award_raw_key,contract_raw_key "
            "FROM award_results WHERE source_key='A|000|1|0'"
        ).fetchone()
    assert {"opening_raw_key", "final_award_raw_key", "contract_raw_key"}.issubset(cols)
    assert row["notice_no"] == "A"
    assert row["first_rank_vendor"] == "기존1순위"
    assert row["opening_raw_key"] == row["final_award_raw_key"] == row["contract_raw_key"] == ""
