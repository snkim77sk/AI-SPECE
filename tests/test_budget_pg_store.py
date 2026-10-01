import datetime as dt
import json

import budget_pg_store
import budget_projection_vnext


def _configure(monkeypatch, tmp_path):
    path = tmp_path / "budget-pg-test.sqlite3"
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()
    return path


def test_budget_store_deduplicates_identical_payload(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    payload = {"fyr": "2026", "dbiz_cd": "P1", "dbiz_nm": "LED 조명 교체", "amount": 100}

    first = budget_pg_store.preserve_observation(
        "budget", "stable-project", payload,
        source_system="지방재정365", source_operation="QWGJK",
        source_date="2026-10-01",
    )
    second = budget_pg_store.preserve_observation(
        "budget", "stable-project", payload,
        source_system="지방재정365", source_operation="QWGJK",
        source_date="2026-10-02",
    )

    assert first["new_observation"] is True
    assert second["new_observation"] is False
    assert first["observation_id"] == second["observation_id"]
    status = budget_pg_store.storage_status()
    assert status["observations"] == 1
    assert status["current_records"] == 1

    current = budget_pg_store.current_rows(["budget"])
    assert len(current) == 1
    assert current[0]["source_date"] == "2026-10-02"
    assert current[0]["payload"]["dbiz_nm"] == "LED 조명 교체"


def test_budget_store_preserves_changed_payload_revision(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    first = {"fyr": "2026", "dbiz_cd": "P1", "amount": 100}
    changed = {"fyr": "2026", "dbiz_cd": "P1", "amount": 150}

    one = budget_pg_store.preserve_observation("budget", "stable-project", first)
    two = budget_pg_store.preserve_observation("budget", "stable-project", changed)

    assert one["sha256"] != two["sha256"]
    assert two["new_observation"] is True
    revisions = budget_pg_store.revision_rows("budget", "stable-project")
    assert len(revisions) == 2
    current = budget_pg_store.current_rows(["budget"])
    assert current[0]["payload"]["amount"] == 150
    assert current[0]["payload_sha256"] == two["sha256"]


def test_budget_store_checkpoint_round_trip(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    budget_pg_store.save_checkpoint(
        "budget", "2026:ALL",
        range_start="2026", range_end="ALL",
        page_no=3, page_size=1000, source_total=2500,
        fetched_count=2000, saved_count=2000, status="RUNNING",
    )
    row = budget_pg_store.get_checkpoint("budget", "2026:ALL")
    assert row["page_no"] == 3
    assert row["source_total"] == 2500
    assert row["status"] == "RUNNING"


def test_budget_store_classification_round_trip(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    saved = budget_pg_store.preserve_observation(
        "education_budget", "edu-key", {"projectName": "LED 교체"}
    )
    budget_pg_store.save_classification(
        "education_budget", "edu-key", "LIGHTING",
        classifier_version="test-v1", subcategory="INDOOR",
        confidence=1.0, reason="exact", source_payload_sha256=saved["sha256"],
    )
    rows = budget_pg_store.classification_rows(["education_budget"], "test-v1")
    assert len(rows) == 1
    assert rows[0]["primary_category"] == "LIGHTING"
    assert rows[0]["source_payload_sha256"] == saved["sha256"]


def test_budget_store_retention_keeps_current_state(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    first = budget_pg_store.preserve_observation("budget", "same", {"amount": 1})
    second = budget_pg_store.preserve_observation("budget", "same", {"amount": 2})
    engine, tables = budget_pg_store._engine_and_tables()
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=500)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            tables["observations"].update()
            .where(tables["observations"].c.id == first["observation_id"])
            .values(observed_at=old)
        )
        conn.execute(
            tables["observations"].update()
            .where(tables["observations"].c.id == second["observation_id"])
            .values(observed_at=old)
        )

    deleted = budget_pg_store.purge_history(365)
    assert deleted["expired_current_records"] == 0
    assert deleted["deleted_observations"] == 1
    revisions = budget_pg_store.revision_rows("budget", "same")
    assert len(revisions) == 1
    assert revisions[0]["id"] == second["observation_id"]


def test_production_rejects_sqlite_budget_url(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'bad.sqlite3'}")
    budget_pg_store.reset_engine_cache()
    try:
        assert budget_pg_store.postgres_configured() is False
    finally:
        budget_pg_store.reset_engine_cache()



def test_qwgjk_projection_uses_current_state_source_date():
    payload = {
        "fyr": "2026",
        "exe_ymd": "20260930",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "D1",
        "dbiz_cd": "P1",
        "acnt_dv_cd": "A1",
    }
    projected = budget_projection_vnext.project_payload(
        "budget", payload, source_date="2026-10-01"
    )
    assert projected["snapshot_date"] == "2026-10-01"



def test_budget_store_retention_expires_unseen_current_state(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    saved = budget_pg_store.preserve_observation(
        "budget", "stale-project", {"fyr": "2026", "dbiz_cd": "STALE", "amount": 10}
    )
    engine, tables = budget_pg_store._engine_and_tables()
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=500)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            tables["observations"].update()
            .where(tables["observations"].c.id == saved["observation_id"])
            .values(observed_at=old)
        )
        conn.execute(
            tables["states"].update()
            .where(tables["states"].c.record_key == "stale-project")
            .values(last_seen_at=old)
        )

    result = budget_pg_store.purge_history(365)

    assert result["expired_current_records"] == 1
    assert result["deleted_observations"] == 1
    assert budget_pg_store.current_rows(["budget"]) == []
    assert budget_pg_store.revision_rows("budget", "stale-project") == []



def test_budget_store_requires_dedicated_database_url(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.delenv("G2B_BUDGET_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'generic.sqlite3'}")
    monkeypatch.setenv("POSTGRES_URL", f"sqlite:///{tmp_path / 'generic2.sqlite3'}")
    budget_pg_store.reset_engine_cache()
    try:
        assert budget_pg_store.resolve_database_url() == ""
        assert budget_pg_store.postgres_configured() is False
        assert budget_pg_store.postgres_ready() is False
    finally:
        budget_pg_store.reset_engine_cache()


def test_budget_store_retention_prunes_old_checkpoint_receipts(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    generation = "old-generation"
    budget_pg_store.save_checkpoint(
        "budget", "2025:old-scope",
        cursor_value=json.dumps({"generation": generation}),
        range_start="2025", range_end="2025-01-01",
        page_no=2, page_size=1, source_total=1,
        fetched_count=1, saved_count=1, status="COMPLETE",
    )
    engine, tables = budget_pg_store._engine_and_tables()
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=500)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            tables["checkpoints"].update()
            .where(tables["checkpoints"].c.scope_key == "2025:old-scope")
            .values(updated_at=old)
        )
        conn.execute(tables["pages"].insert().values(
            dataset="budget", scope_key="2025:old-scope", generation=generation,
            page_no=1, page_size=1, response_hash="hash", item_count=1,
            source_total=1, terminal_reason="TOTAL_REACHED",
        ))
        conn.execute(tables["items"].insert().values(
            dataset="budget", scope_key="2025:old-scope", generation=generation,
            source_key="old-key", page_no=1, payload_sha256="sha",
        ))

    result = budget_pg_store.purge_history(365)

    assert result["deleted_checkpoints"] == 1
    assert result["deleted_collection_pages"] == 1
    assert result["deleted_collection_items"] == 1
    assert budget_pg_store.get_checkpoint("budget", "2025:old-scope") is None
    with engine.connect() as conn:
        assert conn.execute(
            tables["pages"].select().where(
                tables["pages"].c.scope_key == "2025:old-scope"
            )
        ).first() is None
        assert conn.execute(
            tables["items"].select().where(
                tables["items"].c.scope_key == "2025:old-scope"
            )
        ).first() is None


def test_budget_store_retention_keeps_only_current_receipt_generation(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    current_generation = "current-generation"
    budget_pg_store.save_checkpoint(
        "budget", "2026:current-scope",
        cursor_value=json.dumps({"generation": current_generation}),
        range_start="2026", range_end="2026-10-01",
        page_no=1, page_size=1, source_total=-1,
        fetched_count=0, saved_count=0, status="RUNNING",
    )
    engine, tables = budget_pg_store._engine_and_tables()
    with engine.begin() as conn:
        for generation in ("obsolete-generation", current_generation):
            conn.execute(tables["pages"].insert().values(
                dataset="budget", scope_key="2026:current-scope", generation=generation,
                page_no=1, page_size=1, response_hash=generation,
                item_count=0, source_total=-1, terminal_reason="",
            ))

    result = budget_pg_store.purge_history(365)

    assert result["deleted_checkpoints"] == 0
    assert result["deleted_collection_pages"] == 1
    with engine.connect() as conn:
        rows = conn.execute(
            tables["pages"].select().where(
                tables["pages"].c.scope_key == "2026:current-scope"
            )
        ).mappings().all()
    assert [row["generation"] for row in rows] == [current_generation]



def test_budget_store_ready_is_false_when_configured_database_is_unreachable(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    assert budget_pg_store.postgres_configured() is True

    def unavailable():
        raise RuntimeError("synthetic postgres unavailable")

    monkeypatch.setattr(budget_pg_store, "_engine_and_tables", unavailable)
    assert budget_pg_store.postgres_ready() is False



def test_receipt_retention_is_shorter_than_raw_retention(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    saved = budget_pg_store.preserve_observation(
        "budget",
        "still-current",
        {"fyr": "2026", "dbiz_cd": "P1", "amount": 100},
        source_date="2026-10-01",
    )
    generation = "receipt-generation"
    budget_pg_store.save_checkpoint(
        "budget",
        "2026:2026-09-20",
        cursor_value=json.dumps({"generation": generation}),
        range_start="2026",
        range_end="2026-09-20",
        page_no=2,
        page_size=1,
        source_total=1,
        fetched_count=1,
        saved_count=1,
        status="COMPLETE",
    )
    engine, tables = budget_pg_store._engine_and_tables()
    now = dt.datetime.now(dt.timezone.utc)
    ten_days_old = (now - dt.timedelta(days=10)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            tables["checkpoints"].update()
            .where(tables["checkpoints"].c.scope_key == "2026:2026-09-20")
            .values(updated_at=ten_days_old)
        )
        conn.execute(tables["pages"].insert().values(
            dataset="budget",
            scope_key="2026:2026-09-20",
            generation=generation,
            page_no=1,
            page_size=1,
            response_hash="hash",
            item_count=1,
            source_total=1,
            terminal_reason="TOTAL_REACHED",
        ))
        conn.execute(tables["items"].insert().values(
            dataset="budget",
            scope_key="2026:2026-09-20",
            generation=generation,
            source_key="still-current",
            page_no=1,
            payload_sha256=saved["sha256"],
        ))

    result = budget_pg_store.purge_history(
        365,
        receipt_retention_days=3,
        now=now,
    )

    assert result["receipt_retention_days"] == 3
    assert result["deleted_checkpoints"] == 1
    assert result["deleted_collection_pages"] == 1
    assert result["deleted_collection_items"] == 1
    assert result["expired_current_records"] == 0
    assert budget_pg_store.current_payload_hash(
        "budget", "still-current"
    ) == saved["sha256"]


def test_budget_store_defines_retention_and_join_indexes(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _engine, tables = budget_pg_store._engine_and_tables()

    observation_indexes = {index.name for index in tables["observations"].indexes}
    state_indexes = {index.name for index in tables["states"].indexes}
    checkpoint_indexes = {index.name for index in tables["checkpoints"].indexes}

    assert "ix_budget_observation_observed" in observation_indexes
    assert "ix_budget_state_observation" in state_indexes
    assert "ix_budget_state_seen" in state_indexes
    assert "ix_budget_checkpoint_updated" in checkpoint_indexes


def test_budget_store_pool_and_timeout_settings_are_bounded(monkeypatch):
    monkeypatch.setenv("G2B_BUDGET_POOL_SIZE", "999")
    monkeypatch.setenv("G2B_BUDGET_MAX_OVERFLOW", "-5")
    monkeypatch.setenv("G2B_BUDGET_POOL_TIMEOUT_SECONDS", "999")
    monkeypatch.setenv("G2B_BUDGET_STATEMENT_TIMEOUT_MS", "9999999")
    monkeypatch.setenv("G2B_BUDGET_LOCK_TIMEOUT_MS", "1")

    assert budget_pg_store._pool_size() == 10
    assert budget_pg_store._max_overflow() == 0
    assert budget_pg_store._pool_timeout_seconds() == 30
    assert budget_pg_store._statement_timeout_ms() == 600000
    assert budget_pg_store._lock_timeout_ms() == 1000


def test_current_payload_hash_is_direct_and_all_revision_rows_bind_current(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    first = budget_pg_store.preserve_observation(
        "budget", "P1", {"fyr": "2026", "dbiz_cd": "P1", "amount": 100},
        source_date="2026-09-01",
    )
    second = budget_pg_store.preserve_observation(
        "budget", "P1", {"fyr": "2026", "dbiz_cd": "P1", "amount": 200},
        source_date="2026-10-01",
    )
    budget_pg_store.preserve_observation(
        "budget", "P2", {"fyr": "2026", "dbiz_cd": "P2", "amount": 300},
        source_date="2026-10-01",
    )

    assert budget_pg_store.current_payload_hash("budget", "P1") == second["sha256"]
    rows = budget_pg_store.all_revision_rows(
        "budget", source_date_prefix="2026"
    )
    p1 = [row for row in rows if row["record_key"] == "P1"]
    assert [row["sha256"] for row in p1] == [first["sha256"], second["sha256"]]
    assert all(
        row["current_payload_sha256"] == second["sha256"]
        for row in p1
    )
