import datetime as dt

import budget_pg_store


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
    assert deleted == 1
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
