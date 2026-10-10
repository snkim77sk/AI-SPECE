"""G2B 4.1.237: menu 502 protection on dashboard's first-load read paths."""

from contextlib import nullcontext
import inspect

import budget_pg_store
import budget_storage
import shopping_store_v41
import vnext_clean_app
from vnext_schema import CLASSIFIER_VERSION


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'g2b-4237-menu.sqlite3'}",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()


def test_budget_pg_target_counts_exclude_other_and_stale_hash(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    original = budget_pg_store.preserve_observation(
        "budget", "LED-A",
        {"fyr": "2026", "dbiz_cd": "A", "dbiz_nm": "LED가로등", "bdg_cash_amt": 1000},
    )
    budget_pg_store.save_classification(
        "budget", "LED-A", "LIGHTING",
        classifier_version=CLASSIFIER_VERSION,
        source_payload_sha256=original["sha256"],
    )
    other = budget_pg_store.preserve_observation(
        "budget", "OTHER-B",
        {"fyr": "2026", "dbiz_cd": "B", "dbiz_nm": "일반행정", "bdg_cash_amt": 200},
    )
    budget_pg_store.save_classification(
        "budget", "OTHER-B", "OTHER",
        classifier_version=CLASSIFIER_VERSION,
        source_payload_sha256=other["sha256"],
    )
    expected = {"budget": 1, "budget_appropriation": 0}
    assert budget_pg_store.current_classified_counts(
        ("budget", "budget_appropriation"),
        CLASSIFIER_VERSION,
        categories=("LIGHTING", "POLE", "ELECTRICAL", "SOLAR"),
    ) == expected
    # The default readiness count still includes OTHER; no reporting regression.
    assert budget_pg_store.current_classified_counts(
        ("budget",), CLASSIFIER_VERSION
    ) == {"budget": 2}

    changed = budget_pg_store.preserve_observation(
        "budget", "LED-A",
        {"fyr": "2026", "dbiz_cd": "A", "dbiz_nm": "LED가로등", "bdg_cash_amt": 3000},
    )
    assert changed["sha256"] != original["sha256"]
    assert budget_pg_store.current_classified_counts(
        ("budget",),
        CLASSIFIER_VERSION,
        categories=("LIGHTING", "POLE"),
    ) == {"budget": 0}
    assert budget_pg_store.current_classified_counts(
        ("budget",), CLASSIFIER_VERSION, categories=()
    ) == {"budget": 0}


def test_dashboard_does_not_allocate_all_budget_hashes_or_source_schema(monkeypatch):
    clean = vnext_clean_app
    monkeypatch.setitem(clean._BACKEND_STATE, "backend_ok", True)
    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "current_payload_hashes",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("unbounded current budget hash load on dashboard")
        ),
    )
    monkeypatch.setattr(
        shopping_store_v41, "ensure_schema",
        lambda: (_ for _ in ()).throw(
            AssertionError("shopping DDL forbidden on production GET dashboard")
        ),
    )
    seen = []
    monkeypatch.setattr(
        budget_pg_store, "current_classified_counts",
        lambda datasets, version, *, categories=None: (
            seen.append((datasets, version, categories))
            or {"budget": 6, "budget_appropriation": 1, "education_budget": 2}
        ),
    )

    class QueryResult:
        def fetchall(self):
            return [{"primary_category": "LIGHTING", "n": 10}]

    class Conn:
        def execute(self, *args, **kwargs):
            return QueryResult()

    monkeypatch.setattr(clean, "connect", lambda: nullcontext(Conn()))
    result = clean.target_dataset_counts()
    assert result["shopping_delivery"] == 10
    assert result["budget"] == 6
    assert result["education_budget"] == 2
    assert seen and seen[0][2] == ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")


def test_dashboard_budget_counter_timeout_is_safe_no_retry(monkeypatch):
    clean = vnext_clean_app
    monkeypatch.setitem(clean._BACKEND_STATE, "backend_ok", True)
    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "current_payload_hashes",
        lambda *_args: (_ for _ in ()).throw(AssertionError("no fallback")),
    )
    monkeypatch.setattr(
        budget_pg_store, "current_classified_counts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("query timeout")
        ),
    )

    class QueryResult:
        def fetchall(self):
            return []

    class Conn:
        def execute(self, *_a, **_kw):
            return QueryResult()

    monkeypatch.setattr(clean, "connect", lambda: nullcontext(Conn()))
    out = clean.target_dataset_counts()
    assert out == {"shopping_delivery": 0}


def test_dashboard_raw_counts_uses_one_bulk_budget_query(monkeypatch):
    clean = vnext_clean_app
    monkeypatch.setitem(clean._BACKEND_STATE, "backend_ok", True)
    monkeypatch.setattr(
        shopping_store_v41,
        "count",
        lambda: {
            "active_records": 7,
            "history_records": 9,
            "inactive_records": 2,
            "last_at": "2026-10-10",
        },
    )
    calls = []
    monkeypatch.setattr(
        budget_storage, "dataset_counts_all",
        lambda datasets: (
            calls.append(tuple(datasets))
            or {
                name: {
                    "current_records": index + 1,
                    "observations": index + 3,
                    "last_seen_at": "2026-10-10",
                }
                for index, name in enumerate(datasets)
            }
        ),
    )
    monkeypatch.setattr(
        budget_storage, "dataset_counts",
        lambda _dataset: (_ for _ in ()).throw(
            AssertionError("individual budget scan forbidden on dashboard")
        ),
    )
    rows = {row["dataset"]: row for row in clean.raw_counts()}
    assert calls == [tuple(sorted(budget_storage.BUDGET_DATASETS))]
    assert rows["shopping_delivery"]["n"] == 7
    assert rows["shopping_delivery"]["history_n"] == 9
    assert rows["budget"]["n"] >= 1
    assert len(rows) == 4


def test_dashboard_raw_budget_counts_timeout_returns_zeros_without_retry(monkeypatch):
    clean = vnext_clean_app
    monkeypatch.setitem(clean._BACKEND_STATE, "backend_ok", True)
    monkeypatch.setattr(shopping_store_v41, "count", lambda: {
        "active_records": 3, "history_records": 3, "inactive_records": 0,
    })
    monkeypatch.setattr(
        budget_storage, "dataset_counts_all",
        lambda *_: (_ for _ in ()).throw(TimeoutError("budget unavailable")),
    )
    result = {row["dataset"]: row for row in clean.raw_counts()}
    assert result["shopping_delivery"]["n"] == 3
    assert result["budget"]["n"] == 0
    assert result["budget"]["last_at"] == "POSTGRES_UNAVAILABLE"


def test_shopping_count_prod_never_invokes_schema_ddl(monkeypatch):
    monkeypatch.setattr(shopping_store_v41, "_test_mode", lambda: False)
    monkeypatch.setattr(
        shopping_store_v41, "ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("DDL on web read")),
    )

    class Result:
        def fetchone(self):
            return {
                "history_n": 5,
                "active_n": 4,
                "inactive_n": 1,
                "last_at": "now",
                "active_last_at": "now",
            }

    class Conn:
        def execute(self, *_args, **_kwargs):
            return Result()

    monkeypatch.setattr(shopping_store_v41, "connect", lambda: nullcontext(Conn()))
    assert shopping_store_v41.count()["active_records"] == 4


def test_dashboard_web_handlers_keep_login_and_routes():
    source = inspect.getsource(vnext_clean_app.target_dataset_counts)
    assert "budget_pg_store.current_classified_counts" in source
    assert "current_payload_hashes" not in source
    assert "classifications = conn.execute" not in source
    assert "if TEST_MODE:" in source
    route_paths = {route.path for route in vnext_clean_app.app.routes}
    assert {"/shopping", "/vendors", "/budget", "/dashboard", "/live", "/health"} <= route_paths
