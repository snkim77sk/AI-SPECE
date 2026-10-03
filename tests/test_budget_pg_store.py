import datetime as dt
import json

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

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



def test_future_budget_current_state_survives_one_year_retention(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    saved = budget_pg_store.preserve_observation(
        "budget",
        "future-project",
        {
            "fyr": "2027",
            "dbiz_cd": "FUTURE-LED",
            "dbiz_nm": "2027 LED 가로등 교체",
            "bdg_cash_amt": "100000000",
        },
        source_date="2026-10-02",
    )
    engine, tables = budget_pg_store._engine_and_tables()
    old = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            tables["observations"].update()
            .where(tables["observations"].c.id == saved["observation_id"])
            .values(observed_at=old)
        )
        conn.execute(
            tables["states"].update()
            .where(tables["states"].c.record_key == "future-project")
            .values(last_seen_at=old)
        )

    result = budget_pg_store.purge_history(
        365,
        now=dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc),
    )

    assert result["expired_current_records"] == 0
    current = budget_pg_store.current_rows(["budget"])
    assert len(current) == 1
    assert current[0]["payload"]["fyr"] == "2027"
    assert current[0]["payload"]["dbiz_nm"] == "2027 LED 가로등 교체"


def test_complete_snapshot_reconciliation_removes_missing_current_only(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    budget_pg_store.preserve_observation(
        "budget_appropriation", "KEEP",
        {"fyr": "2027", "fld_cd": "F1", "sect_cd": "S1", "biz_bdg_tott_amt": "100"},
    )
    budget_pg_store.preserve_observation(
        "budget_appropriation", "DROP",
        {"fyr": "2027", "fld_cd": "F2", "sect_cd": "S2", "biz_bdg_tott_amt": "200"},
    )
    engine, tables = budget_pg_store._engine_and_tables()
    generation = "reconcile-gen"
    budget_pg_store.save_checkpoint(
        "budget_appropriation", "2027:ALL",
        cursor_value=json.dumps({"generation": generation}),
        page_no=2, page_size=1000, source_total=1,
        fetched_count=1, saved_count=1, status="COMPLETE",
    )
    keep_hash = budget_pg_store.current_payload_hash(
        "budget_appropriation", "KEEP"
    )
    with engine.begin() as conn:
        conn.execute(tables["items"].insert().values(
            dataset="budget_appropriation",
            scope_key="2027:ALL",
            generation=generation,
            source_key="KEEP",
            page_no=1,
            payload_sha256=keep_hash,
        ))

    result = budget_pg_store.reconcile_complete_fiscal_year(
        "budget_appropriation", "2027:ALL", 2027
    )

    assert result["removed_current_records"] == 1
    assert [
        row["record_key"]
        for row in budget_pg_store.current_rows(["budget_appropriation"])
    ] == ["KEEP"]
    # Immutable observation history remains available for audit/revision retention.
    assert len(budget_pg_store.revision_rows("budget_appropriation", "DROP")) == 1


def test_empty_complete_snapshot_does_not_wipe_current_state(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    budget_pg_store.preserve_observation(
        "budget_appropriation", "KEEP",
        {"fyr": "2027", "fld_cd": "F1", "sect_cd": "S1", "biz_bdg_tott_amt": "100"},
    )
    budget_pg_store.save_checkpoint(
        "budget_appropriation", "2027:ALL",
        cursor_value=json.dumps({"generation": "empty-gen"}),
        page_no=2, page_size=1000, source_total=0,
        fetched_count=0, saved_count=0, status="COMPLETE",
    )

    result = budget_pg_store.reconcile_complete_fiscal_year(
        "budget_appropriation", "2027:ALL", 2027
    )

    assert result["reconciled"] is False
    assert result["reason"] == "EMPTY_SNAPSHOT_FAILSAFE"
    assert len(budget_pg_store.current_rows(["budget_appropriation"])) == 1


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



def test_production_ignores_legacy_budget_database_url(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    for name in (
        "G2B_DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL", "DATABASE_URL",
        "DB_HOST", "PGHOST", "POSTGRES_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        "postgresql://legacy:secret@db.example.invalid/g2b",
    )
    budget_pg_store.reset_engine_cache()
    try:
        assert budget_pg_store.resolve_database_url() == ""
        assert budget_pg_store.postgres_url_present() is False
        assert budget_pg_store.postgres_configured() is False
    finally:
        budget_pg_store.reset_engine_cache()


def test_budget_store_uses_canonical_database_url(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    for name in (
        "G2B_BUDGET_DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL", "DATABASE_URL"
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "G2B_DATABASE_URL",
        "postgresql://user:pass@db.example.invalid/g2b",
    )
    budget_pg_store.reset_engine_cache()
    try:
        resolved = budget_pg_store.resolve_database_url()
        assert resolved.startswith("postgresql+psycopg://")
        assert budget_pg_store.postgres_configured() is True
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

    # This checkpoint is older than the 1-year budget history window, so
    # both its compact marker and its bulky page/item receipts are expired.
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



def test_old_active_checkpoint_keeps_current_generation_beyond_short_receipt_window(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    generation = "active-generation"
    budget_pg_store.save_checkpoint(
        "budget", "2026:2026-10-01",
        cursor_value=json.dumps({"generation": generation}),
        range_start="2026", range_end="2026-10-01",
        page_no=2, page_size=1, source_total=2,
        fetched_count=1, saved_count=1, status="RUNNING",
    )
    engine, tables = budget_pg_store._engine_and_tables()
    now = dt.datetime.now(dt.timezone.utc)
    ten_days_old = (now - dt.timedelta(days=10)).isoformat()
    with engine.begin() as conn:
        conn.execute(
            tables["checkpoints"].update()
            .where(tables["checkpoints"].c.scope_key == "2026:2026-10-01")
            .values(updated_at=ten_days_old)
        )
        for generation_name in ("obsolete-generation", generation):
            conn.execute(tables["pages"].insert().values(
                dataset="budget", scope_key="2026:2026-10-01",
                generation=generation_name, page_no=1, page_size=1,
                response_hash=generation_name, item_count=0,
                source_total=2, terminal_reason="",
            ))

    result = budget_pg_store.purge_history(
        365, receipt_retention_days=3, now=now
    )

    assert result["deleted_checkpoints"] == 0
    checkpoint = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    assert checkpoint["status"] == "RUNNING"
    with engine.connect() as conn:
        rows = conn.execute(
            tables["pages"].select().where(
                tables["pages"].c.scope_key == "2026:2026-10-01"
            )
        ).mappings().all()
    assert [row["generation"] for row in rows] == [generation]


def test_new_complete_snapshot_supersedes_older_unresolved_qwgjk(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    budget_pg_store.save_checkpoint(
        "budget", "2026:2026-10-01",
        cursor_value=json.dumps({"generation": "old-gen"}),
        range_start="2026", range_end="2026-10-01",
        page_no=2, page_size=1000, source_total=1500,
        fetched_count=1000, saved_count=1000, status="RUNNING",
    )
    budget_pg_store.save_checkpoint(
        "budget", "2026:2026-10-02",
        cursor_value=json.dumps({"generation": "new-gen"}),
        range_start="2026", range_end="2026-10-02",
        page_no=2, page_size=1000, source_total=1,
        fetched_count=1, saved_count=1, status="COMPLETE",
    )

    result = budget_pg_store.supersede_older_nationwide_checkpoints(
        "budget", 2026, "2026:2026-10-02"
    )

    assert result["superseded_checkpoints"] == 1
    old = budget_pg_store.get_checkpoint("budget", "2026:2026-10-01")
    new = budget_pg_store.get_checkpoint("budget", "2026:2026-10-02")
    assert old["status"] == "SUPERSEDED"
    assert "2026-10-02" in old["last_error"]
    assert new["status"] == "COMPLETE"



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
    # Daily QWGJK COMPLETE markers are the historical-backfill ledger and remain
    # for the 1-year history window; only their page/item receipts expire here.
    assert result["deleted_checkpoints"] == 0
    assert budget_pg_store.get_checkpoint(
        "budget", "2026:2026-09-20"
    )["status"] == "COMPLETE"
    assert result["deleted_collection_pages"] == 1
    assert result["deleted_collection_items"] == 1
    assert result["expired_current_records"] == 0
    assert budget_pg_store.current_payload_hash(
        "budget", "still-current"
    ) == saved["sha256"]


def test_qwgjk_history_retention_uses_source_date_not_late_backfill_time(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)

    current = budget_pg_store.preserve_observation(
        "budget",
        "RETENTION-P1",
        {
            "fyr": "2026",
            "exe_ymd": "20261002",
            "dbiz_cd": "RETENTION-P1",
            "dbiz_nm": "LED 현재예산",
            "bdg_cash_amt": 200,
        },
        source_date="2026-10-02",
    )
    historical = budget_pg_store.preserve_observation(
        "budget",
        "RETENTION-P1",
        {
            "fyr": "2026",
            "exe_ymd": "20260101",
            "dbiz_cd": "RETENTION-P1",
            "dbiz_nm": "LED 과거예산",
            "bdg_cash_amt": 100,
        },
        source_date="2026-01-01",
        advance_current=False,
    )
    assert historical["current_advanced"] is False

    budget_pg_store.save_checkpoint(
        "budget",
        "history:2026:2026-01-01",
        cursor_value=json.dumps({"generation": "history-retention"}),
        range_start="2026",
        range_end="2026-01-01",
        page_no=2,
        page_size=1,
        source_total=1,
        fetched_count=1,
        saved_count=1,
        status="COMPLETE",
    )

    result = budget_pg_store.purge_history(
        365,
        receipt_retention_days=3,
        now=dt.datetime(
            2027, 1, 2, 0, 0, tzinfo=dt.timezone.utc
        ),
    )

    assert result["source_cutoff_date"] == "2026-01-02"
    assert budget_pg_store.get_checkpoint(
        "budget", "history:2026:2026-01-01"
    ) is None
    assert budget_pg_store.current_payload_hash(
        "budget", "RETENTION-P1"
    ) == current["sha256"]
    revisions = budget_pg_store.revision_rows(
        "budget", "RETENTION-P1"
    )
    assert [row["sha256"] for row in revisions] == [current["sha256"]]


def test_budget_store_defines_retention_and_join_indexes(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _engine, tables = budget_pg_store._engine_and_tables()

    observation_indexes = {index.name for index in tables["observations"].indexes}
    state_indexes = {index.name for index in tables["states"].indexes}
    checkpoint_indexes = {index.name for index in tables["checkpoints"].indexes}
    revision_indexes = {index.name for index in tables["project_revisions"].indexes}

    assert "ix_budget_observation_observed" in observation_indexes
    assert "ix_budget_state_observation" in state_indexes
    assert "ix_budget_state_seen" in state_indexes
    assert "ix_budget_checkpoint_updated" in checkpoint_indexes
    assert "ix_budget_project_revision_dataset_date" in revision_indexes


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



def test_revision_project_rows_supports_inclusive_date_region_and_query(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    budget_pg_store.preserve_observation(
        "budget",
        "H1",
        {
            "fyr": "2026",
            "wa_laf_hg_nm": "인천광역시",
            "laf_hg_nm": "인천광역시",
            "dept_nm": "도로과",
            "dbiz_cd": "P1",
            "dbiz_nm": "노후 가로등 LED 교체",
            "bdg_cash_amt": "1000",
            "ep_amt": "100",
        },
        source_date="2026-01-01",
    )
    budget_pg_store.preserve_observation(
        "budget",
        "H1",
        {
            "fyr": "2026",
            "wa_laf_hg_nm": "인천광역시",
            "laf_hg_nm": "인천광역시",
            "dept_nm": "도로과",
            "dbiz_cd": "P1",
            "dbiz_nm": "노후 가로등 LED 교체",
            "bdg_cash_amt": "1500",
            "ep_amt": "400",
        },
        source_date="2026-12-31",
    )
    budget_pg_store.preserve_observation(
        "budget",
        "OUT",
        {
            "fyr": "2027",
            "wa_laf_hg_nm": "서울특별시",
            "laf_hg_nm": "서울특별시",
            "dbiz_cd": "P2",
            "dbiz_nm": "다른 사업",
            "bdg_cash_amt": "9000",
        },
        source_date="2027-01-01",
    )

    rows = budget_pg_store.revision_project_rows(
        "budget",
        start_date="2026-01-01",
        end_date="2026-12-31",
        region_terms=("인천광역시", "인천"),
        query="가로등",
        limit=10,
    )

    assert [row["source_date"] for row in rows] == [
        "2026-12-31",
        "2026-01-01",
    ]
    assert all(row["org_name"] == "인천광역시" for row in rows)
    assert all(row["project_name"] == "노후 가로등 LED 교체" for row in rows)


def test_existing_budget_store_recreates_missing_declared_index(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    engine, _tables = budget_pg_store._engine_and_tables()
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_budget_checkpoint_updated"))

    budget_pg_store.reset_engine_cache()
    engine, _tables = budget_pg_store._engine_and_tables()
    names = {
        row["name"]
        for row in inspect(engine).get_indexes(
            "budget_collection_checkpoints"
        )
    }
    assert "ix_budget_checkpoint_updated" in names


def test_partial_existing_budget_schema_fails_with_contract_error(
    monkeypatch, tmp_path
):
    path = tmp_path / "partial-budget.sqlite3"
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{path}",
    )
    budget_pg_store.reset_engine_cache()

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{path}", future=True)
    with engine.begin() as conn:
        conn.execute(text(
            """CREATE TABLE budget_record_states(
                   dataset TEXT NOT NULL,
                   record_key TEXT NOT NULL,
                   PRIMARY KEY(dataset,record_key)
               )"""
        ))
    engine.dispose()

    try:
        budget_pg_store._engine_and_tables()
    except RuntimeError as exc:
        assert str(exc).startswith(
            "BUDGET_POSTGRES_SCHEMA_CONTRACT_MISMATCH:"
            "budget_record_states:"
        )
        assert "observation_id" in str(exc)
    else:
        raise AssertionError("partial schema must fail closed")
    finally:
        budget_pg_store.reset_engine_cache()


def test_existing_schema_reuse_does_not_attempt_create():
    calls = []

    class FakeConn:
        def execute(self, statement, params=None):
            calls.append((str(statement), params))

            class Result:
                def first(self):
                    return (1,)

            return Result()

    class Context:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def begin(self):
            return Context()

    budget_pg_store._ensure_database_schema(FakeEngine(), "g2b_budget")

    assert len(calls) == 1
    assert "pg_namespace" in calls[0][0]
    assert "CREATE SCHEMA" not in calls[0][0]



def test_postgres_existing_index_ddl_is_concurrent_and_schema_qualified():
    tables = budget_pg_store._build_tables("g2b_budget")

    class FakeEngine:
        dialect = postgresql.dialect()

    target = next(
        index
        for index in tables["checkpoints"].indexes
        if index.name == "ix_budget_checkpoint_updated"
    )
    sql = budget_pg_store._concurrent_index_sql(FakeEngine(), target)

    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in sql
    assert "ix_budget_checkpoint_updated" in sql
    assert "g2b_budget.budget_collection_checkpoints" in sql
    assert "updated_at" in sql


def test_budget_engine_config_key_changes_with_schema_not_legacy_pool(monkeypatch):
    monkeypatch.setenv("G2B_BUDGET_POOL_SIZE", "3")
    first = budget_pg_store._engine_config_key(
        "postgresql+psycopg://user:pass@host/db",
        "g2b_budget",
    )
    second = budget_pg_store._engine_config_key(
        "postgresql+psycopg://user:pass@host/db",
        "g2b_budget_v2",
    )
    monkeypatch.setenv("G2B_BUDGET_POOL_SIZE", "4")
    third = budget_pg_store._engine_config_key(
        "postgresql+psycopg://user:pass@host/db",
        "g2b_budget",
    )

    assert first != second
    assert first == third


def test_postgres_ready_resets_stale_engine_after_core_probe_failure(monkeypatch):
    tables = budget_pg_store._build_tables(None)
    reset = []
    monkeypatch.setattr(
        budget_pg_store, "postgres_url_present", lambda: True
    )

    class BrokenConn:
        def execute(self, statement):
            if "SELECT 1" in str(statement):
                return self
            raise RuntimeError("synthetic core table unavailable")

        def first(self):
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class BrokenEngine:
        def connect(self):
            return BrokenConn()

    monkeypatch.setattr(budget_pg_store, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        budget_pg_store,
        "_engine_and_tables",
        lambda: (BrokenEngine(), tables),
    )
    monkeypatch.setattr(
        budget_pg_store,
        "reset_engine_cache",
        lambda: reset.append(True),
    )

    assert budget_pg_store.postgres_ready() is False
    assert reset == [True]



def test_budget_postgres_ready_exposes_only_safe_error_code(monkeypatch):
    monkeypatch.setattr(
        budget_pg_store, "postgres_url_present", lambda: True
    )
    monkeypatch.setattr(
        budget_pg_store,
        "_engine_and_tables",
        lambda: (_ for _ in ()).throw(
            RuntimeError(
                "BUDGET_POSTGRES_INDEX_MIGRATION_FAILED:"
                "ix_budget_checkpoint_updated"
            )
        ),
    )

    assert budget_pg_store.postgres_ready() is False
    assert (
        budget_pg_store.postgres_last_error_code()
        == "BUDGET_POSTGRES_INDEX_MIGRATION_FAILED:"
        "ix_budget_checkpoint_updated"
    )
    assert "password" not in budget_pg_store.postgres_last_error_code().lower()


def test_budget_engine_rotates_when_database_url_changes(monkeypatch, tmp_path):
    first_path = tmp_path / "first-budget.sqlite3"
    second_path = tmp_path / "second-budget.sqlite3"
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{first_path}",
    )
    budget_pg_store.reset_engine_cache()

    first_engine, _tables = budget_pg_store._engine_and_tables()
    budget_pg_store.preserve_observation(
        "budget", "FIRST", {"fyr": "2026", "dbiz_cd": "FIRST"}
    )

    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{second_path}",
    )
    second_engine, _tables = budget_pg_store._engine_and_tables()

    assert second_engine is not first_engine
    assert budget_pg_store.current_rows(["budget"]) == []

    budget_pg_store.preserve_observation(
        "budget", "SECOND", {"fyr": "2026", "dbiz_cd": "SECOND"}
    )
    assert [
        row["record_key"]
        for row in budget_pg_store.current_rows(["budget"])
    ] == ["SECOND"]


def test_safe_error_code_does_not_echo_generic_exception_message():
    exc = RuntimeError("postgresql://user:secret@db.example.invalid/private")
    assert budget_pg_store._safe_error_code(exc) == "RuntimeError"



def test_invalid_database_url_is_present_but_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.delenv("G2B_BUDGET_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "G2B_DATABASE_URL",
        f"sqlite:///{tmp_path / 'not-postgres.sqlite3'}",
    )
    budget_pg_store.reset_engine_cache()

    assert budget_pg_store.postgres_url_present() is True
    assert budget_pg_store.postgres_configured() is False
    assert budget_pg_store.postgres_ready() is False
    assert (
        budget_pg_store.postgres_last_error_code()
        == "G2B_DATABASE_URL_POSTGRESQL_REQUIRED"
    )


def test_missing_budget_database_url_is_distinct_from_invalid(monkeypatch):
    for name in (
        "G2B_DATABASE_URL", "G2B_BUDGET_DATABASE_URL",
        "POSTGRES_URL", "POSTGRESQL_URL", "DATABASE_URL",
        "DB_HOST", "PGHOST", "POSTGRES_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    budget_pg_store.reset_engine_cache()

    assert budget_pg_store.postgres_url_present() is False
    assert budget_pg_store.postgres_ready() is False
    assert (
        budget_pg_store.postgres_last_error_code()
        == "BUDGET_POSTGRES_NOT_CONFIGURED"
    )



def test_budget_schema_contract_rejects_missing_primary_key(monkeypatch):
    tables = budget_pg_store._build_tables(None)

    class FakeInspector:
        def get_columns(self, table_name, schema=None):
            table = next(
                table
                for name, table in tables.items()
                if name != "metadata" and table.name == table_name
            )
            return [{"name": column.name} for column in table.columns]

        def get_pk_constraint(self, table_name, schema=None):
            return {"constrained_columns": []}

        def get_unique_constraints(self, table_name, schema=None):
            return []

    monkeypatch.setattr(
        budget_pg_store,
        "inspect",
        lambda engine: FakeInspector(),
    )

    try:
        budget_pg_store._verify_table_contract(object(), tables)
    except RuntimeError as exc:
        assert str(exc).startswith(
            "BUDGET_POSTGRES_PRIMARY_KEY_MISMATCH:"
        )
    else:
        raise AssertionError("missing primary key must fail contract")


def test_budget_schema_contract_rejects_missing_unique_constraint(monkeypatch):
    tables = budget_pg_store._build_tables(None)

    class FakeInspector:
        def _table(self, table_name):
            return next(
                table
                for name, table in tables.items()
                if name != "metadata" and table.name == table_name
            )

        def get_columns(self, table_name, schema=None):
            return [
                {"name": column.name}
                for column in self._table(table_name).columns
            ]

        def get_pk_constraint(self, table_name, schema=None):
            return {
                "constrained_columns": [
                    column.name
                    for column in self._table(table_name).primary_key.columns
                ]
            }

        def get_unique_constraints(self, table_name, schema=None):
            return []

    monkeypatch.setattr(
        budget_pg_store,
        "inspect",
        lambda engine: FakeInspector(),
    )

    try:
        budget_pg_store._verify_table_contract(object(), tables)
    except RuntimeError as exc:
        assert str(exc) == (
            "BUDGET_POSTGRES_UNIQUE_CONSTRAINT_MISMATCH:"
            "budget_source_observations"
        )
    else:
        raise AssertionError("missing observation unique constraint must fail")

def test_canonical_current_project_rows_return_normalized_state(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    budget_pg_store.preserve_observation(
        "budget_appropriation",
        "aidfa-current",
        {
            "fyr": "2026",
            "wa_laf_cd": "2800000",
            "wa_laf_hg_nm": "인천광역시",
            "laf_cd": "2817700",
            "laf_hg_nm": "미추홀구",
            "fld_nm": "교통및물류",
            "sect_nm": "도로조명",
            "biz_bdg_tott_amt": "777000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026-10-03",
    )

    rows = budget_pg_store.current_project_rows(
        ["budget_appropriation"],
        fiscal_year=2026,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["dataset"] == "budget_appropriation"
    assert row["record_key"] == "aidfa-current"
    assert row["source_layer"] == "APPROPRIATION"
    assert row["fiscal_year"] == 2026
    assert row["region_name"] == "인천광역시"
    assert row["budget_amount"] == 777000
    assert row["source_date"] == "2026-10-03"

