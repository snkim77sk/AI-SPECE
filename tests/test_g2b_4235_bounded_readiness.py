"""4.1.235: 256 MiB readiness paths must not load entire budget hash maps."""

import inspect

import budget_pg_store
import readiness_vnext
import budget_collection_status_vnext
import budget_storage


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL", f"sqlite:///{tmp_path / 'budget-readiness.sqlite3'}"
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()


def test_aggregate_counts_only_exact_current_classifications(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    version = "ci-readiness-v1"
    dataset = "budget"
    first = budget_pg_store.preserve_observation(
        dataset, "LED-P1",
        {"fyr": "2026", "dbiz_cd": "LED-P1", "bdg_cash_amt": 100},
        source_date="2026-10-01",
    )
    budget_pg_store.save_classification(
        dataset, "LED-P1", "LIGHTING",
        classifier_version=version,
        source_payload_sha256=first["sha256"],
    )
    counts = budget_pg_store.current_classified_counts(
        ("budget", "budget_appropriation", "education_budget"), version
    )
    assert counts == {
        "budget": 1, "budget_appropriation": 0, "education_budget": 0
    }

    # Current state has a new hash; old classification must not be counted.
    changed = budget_pg_store.preserve_observation(
        dataset, "LED-P1",
        {"fyr": "2026", "dbiz_cd": "LED-P1", "bdg_cash_amt": 200},
        source_date="2026-10-02",
    )
    assert changed["sha256"] != first["sha256"]
    assert budget_pg_store.current_classified_counts((dataset,), version) == {
        dataset: 0
    }

    budget_pg_store.save_classification(
        dataset, "LED-P1", "LIGHTING",
        classifier_version=version,
        source_payload_sha256=changed["sha256"],
    )
    assert budget_pg_store.current_classified_counts((dataset,), version) == {
        dataset: 1
    }


def test_aggregate_returns_zero_on_empty_valid_dataset(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    assert budget_pg_store.current_classified_counts(
        ("budget", "education_budget"), "ci"
    ) == {"budget": 0, "education_budget": 0}
    assert budget_pg_store.current_classified_counts((), "ci") == {}


def test_pg_aggregate_never_materializes_ids_or_runs_migration():
    source = inspect.getsource(budget_pg_store.current_classified_counts)
    assert "_read_only_engine_and_tables()" in source
    assert "_bound_budget_view_query(conn, 4500)" in source
    assert "func.count()" in source
    assert "source_payload_sha256" in source
    assert "payload_sha256" in source
    assert "classifier_version" in source
    assert ".fetchall()" not in source
    assert ".mappings().all()" not in source
    assert "create_all" not in source


def test_readiness_bulk_query_error_degrades_without_expensive_retry(monkeypatch):
    monkeypatch.setattr(
        readiness_vnext, "_shopping_storage_readiness",
        lambda: {"readiness_scope": "TEST"},
    )
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "dataset_counts_all",
        lambda selected: (_ for _ in ()).throw(TimeoutError("DB query budget")),
    )
    monkeypatch.setattr(
        budget_storage, "current_payload_hashes",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("unbounded fallback must not run")
        ),
    )
    monkeypatch.setattr(
        budget_storage, "dataset_counts",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("retry per dataset forbidden")
        ),
    )
    monkeypatch.setattr(
        budget_pg_store, "current_classified_counts",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("classification scan after count timeout forbidden")
        ),
    )
    monkeypatch.setattr(
        budget_collection_status_vnext, "budget_collection_status",
        lambda: {"datasets": []},
    )
    result = readiness_vnext.storage_readiness()
    for dataset in sorted(readiness_vnext.BUDGET_RAW_DATASETS):
        assert result[dataset]["storage_error"] == "TimeoutError"
        assert result[dataset]["latest_raw_rows"] == 0
        assert result[dataset]["source_collection_completeness_verified"] is False


def test_production_shopping_readiness_has_no_ddl():
    source = inspect.getsource(readiness_vnext._shopping_storage_readiness)
    assert "shopping_store_v41.ensure_schema()" not in source
    assert "SELECT COUNT(*)" in source


def test_dashboard_calls_bounded_readiness_after_first_paint():
    import vnext_clean_app

    dashboard = inspect.getsource(vnext_clean_app.dashboard)
    payload = inspect.getsource(vnext_clean_app._dashboard_snapshot)
    assert "_dashboard_summary_payload()" not in dashboard
    assert "readiness_vnext.build_readiness_report()" in payload
