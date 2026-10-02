import ast
import importlib
from pathlib import Path

import db


def _reload_clean_modules():
    import vnext_clean_db
    import vnext_clean_app
    importlib.reload(vnext_clean_db)
    importlib.reload(vnext_clean_app)
    assert vnext_clean_app.initialize_backend() is True
    return vnext_clean_db, vnext_clean_app


def test_main_entrypoint_has_safe_bootstrap_fallback():
    text = Path("main.py").read_text(encoding="utf-8")
    assert "vnext_clean_app" in text
    assert "build_runtime" in text
    assert "G2B_VNEXT_IMPORT_FAILURE" in text
    assert '"import_error": public_error' in text
    assert "sinsung_" not in text
    assert "scheduler" not in text

    import main

    def broken_import(_name):
        raise RuntimeError("synthetic import failure")

    fallback, error = main.build_runtime(broken_import)
    assert "RuntimeError" in error
    paths = {route.path for route in fallback.routes}
    assert {"/", "/live", "/health", "/__ai_space_health", "/ready"} <= paths


def test_clean_app_exposes_only_new_runtime_routes():
    _db, clean = _reload_clean_modules()
    paths = {route.path for route in clean.app.routes}
    expected = {
        "/", "/health", "/__ai_space_health", "/live", "/ready",
        "/setup", "/login", "/logout",
        "/dashboard", "/collection-monitor", "/shopping", "/vendors",
        "/budget", "/raw", "/settings",
        "/organize/budget",
        "/api/status", "/api/collection-status", "/api/shopping", "/api/vendors",
        "/api/budget",
    }
    assert expected <= paths
    assert "/goods" not in paths
    assert "/api/goods" not in paths
    assert "/service" not in paths
    assert "/api/service" not in paths
    assert "/organize/service" not in paths
    legacy = {
        "/g2b/shopping/prdct_detail.php", "/vendor", "/org",
        "/market", "/ranking", "/sales", "/products", "/bids", "/budgets",
        "/annual", "/category", "/admin/users",
    }
    assert not (legacy & paths)


def test_first_admin_can_be_created_directly_without_setup_token():
    clean_db, _clean = _reload_clean_modules()

    assert clean_db.users_empty() is True
    clean_db.create_admin("admin1", "AdminPassword123!")
    assert clean_db.users_empty() is False
    assert clean_db.authenticate("admin1", "AdminPassword123!") == {
        "username": "admin1",
        "role": "admin",
    }

    import pytest
    with pytest.raises(ValueError, match="최초 관리자가 이미 생성"):
        clean_db.create_admin("admin2", "SecondAdminPassword123!")


def test_clean_health_and_auth_round_trip():
    clean_db, clean = _reload_clean_modules()
    assert clean_db.users_empty()
    clean_db.create_admin("admin1", "AdminPassword123!")
    assert not clean_db.users_empty()
    assert clean_db.authenticate("admin1", "wrong") is None
    user = clean_db.authenticate("admin1", "AdminPassword123!")
    assert user == {"username": "admin1", "role": "admin"}

    token = clean_db.create_session("admin1")
    assert clean_db.session_user(token) == user
    clean_db.delete_session(token)
    assert clean_db.session_user(token) is None

    health = clean.health()
    assert health["status"] == "ok"
    assert health["process_alive"] is True
    assert health["backend_ok"] is True
    assert health["runtime"] == "G2B_VNEXT_CLEAN"
    assert "raw_rows" not in health
    assert health["db_path"].endswith("isolated.sqlite3")
    assert health["db_persistent"] is True
    assert health["persistent_storage_required"] is False
    assert health["storage_backend"] == "SQLITE_TEST"



def test_backend_initialization_failure_is_fail_soft(monkeypatch):
    _db, clean = _reload_clean_modules()

    def broken_storage():
        raise RuntimeError("synthetic storage unavailable")

    monkeypatch.setattr(clean, "ensure_clean_schema", broken_storage)
    clean._BACKEND_STATE.update(
        initialized=False,
        initializing=False,
        backend_ok=False,
        backend_error="",
        attempts=0,
    )

    assert clean.initialize_backend(force=True) is False
    status = clean.health()
    assert status["status"] == "ok"
    assert status["process_alive"] is True
    assert status["backend_ok"] is False
    assert "raw_rows" not in status
    assert status["required_boot_env"] == []
    assert "RuntimeError" in status["backend_error"]

    live = clean.live()
    assert live["status"] == "ok"
    assert live["process_alive"] is True

    ready = clean.ready()
    assert ready.status_code == 503

def test_clean_schema_is_non_destructive_and_cleanup_is_explicit():
    clean_db, _clean = _reload_clean_modules()

    with db.connect() as conn:
        for table in clean_db.LEGACY_TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table}(id INTEGER)")
            conn.execute(f"INSERT INTO {table}(id) VALUES(1)")
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('auto_sync_enabled','1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )

    clean_db.ensure_clean_schema()
    assert clean_db.legacy_tables_absent() is False

    clean_db.cleanup_legacy_tables()
    assert clean_db.legacy_tables_absent() is True
    with db.connect() as conn:
        settings = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key,value FROM app_settings").fetchall()
        }
    assert "auto_sync_enabled" not in settings
    assert settings["vnext_legacy_cleanup_complete"] == "1"


def test_runtime_port_resolution_is_platform_independent(monkeypatch):
    import run

    assert run.resolve_port("9123") == 9123
    assert run.resolve_port("not-a-number") == 8000
    assert run.resolve_port("70000") == 8000
    monkeypatch.setenv("PORT", "8765")
    assert run.resolve_port() == 8765


def test_operational_layout_exposes_version_and_username_limiter_is_account_bound():
    _db, clean = _reload_clean_modules()
    body = clean.layout("운영", "<p>ok</p>", user={"username": "admin1"}).body.decode("utf-8")
    assert clean.APP_VERSION in body
    assert "G2B vNext " + clean.APP_VERSION in body

    clean._LOGIN_FAILURES.clear()
    first = ("ip:203.0.113.10", "user:admin1")
    spoofed_ip_same_user = ("ip:203.0.113.99", "user:admin1")
    assert clean._login_allowed(first) is True
    for _ in range(clean.LOGIN_MAX_FAILURES):
        clean._login_failed(first)
    assert clean._login_allowed(first) is False
    # Changing/spoofing an IP cannot bypass the account-side limiter.
    assert clean._login_allowed(spoofed_ip_same_user) is False

    clean._login_success(first)
    assert clean._login_allowed(first) is True


def test_public_error_is_minimal_outside_test_mode(monkeypatch):
    _db, clean = _reload_clean_modules()
    monkeypatch.setattr(clean, "TEST_MODE", False)
    assert clean._public_error("OperationalError: /secret/path/file.sqlite3") == "OperationalError"
    monkeypatch.setattr(clean, "TEST_MODE", True)
    assert "/secret/path/file.sqlite3" in clean._public_error(
        "OperationalError: /secret/path/file.sqlite3"
    )


def test_production_readiness_fails_closed_on_nonpersistent_storage(monkeypatch):
    _db, clean = _reload_clean_modules()
    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: False)
    assert clean.backend_status()["backend_ok"] is True

    response = clean.ready()
    assert response.status_code == 503
    payload = __import__("json").loads(response.body.decode("utf-8"))
    assert payload["backend_ok"] is True
    assert payload["db_persistent"] is False
    assert payload["persistent_storage_required"] is True
    assert payload["operational_ready"] is False

    monkeypatch.setattr(clean, "TEST_MODE", True)
    response = clean.ready()
    assert response.status_code == 200



def test_dashboard_stays_200_when_live_collection_blocks_aggregates(monkeypatch):
    _db, clean = _reload_clean_modules()
    import readiness_vnext

    monkeypatch.setattr(
        clean, "require_user",
        lambda request: {"username": "admin1", "role": "admin"},
    )

    def locked():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(clean, "raw_counts", locked)
    monkeypatch.setattr(clean, "target_dataset_counts", locked)
    monkeypatch.setattr(readiness_vnext, "build_readiness_report", locked)

    response = clean.dashboard(object())
    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "G2B vNext 대시보드" in body
    assert "집계 일시 대기" in body
    assert "수집은 계속 진행" in body
    assert "TEMPORARILY_UNAVAILABLE" in body


def test_dashboard_snapshot_can_return_partial_counts(monkeypatch):
    _db, clean = _reload_clean_modules()
    import readiness_vnext

    monkeypatch.setattr(
        clean,
        "raw_counts",
        lambda: [{"dataset": "shopping_delivery", "n": 1997, "last_at": "now"}],
    )
    monkeypatch.setattr(
        clean,
        "target_dataset_counts",
        lambda: {"shopping_delivery": 321},
    )
    monkeypatch.setattr(
        readiness_vnext,
        "build_readiness_report",
        lambda: {"status": "READY", "status_scope": "TEST"},
    )
    snapshot = clean._dashboard_snapshot()
    assert snapshot["total"] == 1997
    assert snapshot["target"]["shopping_delivery"] == 321
    assert snapshot["warnings"] == []



def test_result_server_disables_source_collection_and_decodes_snapshot(monkeypatch):
    import gzip
    import json
    _db, clean = _reload_clean_modules()

    monkeypatch.setenv("G2B_RUNTIME_ROLE", "RESULT_SERVER")
    assert clean.schedule_recent_collection(force=True) is False
    assert clean._auto_sync_enabled() is False

    payload = {"schema_version": 1, "sections": {}}
    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
    decoded = clean._decode_result_sync_body(compressed, "gzip")
    assert decoded == payload


def test_local_collector_role_can_schedule_when_not_test_mode(monkeypatch):
    _db, clean = _reload_clean_modules()
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "LOCAL_COLLECTOR")
    monkeypatch.setenv("G2B_AUTO_SYNC", "1")
    monkeypatch.setattr(clean, "TEST_MODE", False)
    assert clean._auto_sync_enabled() is True


def test_clean_app_exposes_result_sync_and_compaction_routes():
    _db, clean = _reload_clean_modules()
    paths = {route.path for route in clean.app.routes}
    assert "/api/result-sync" in paths
    assert "/settings/result-sync-token" in paths
    assert "/settings/compact-result-server" in paths



def test_health_reports_hybrid_runtime_role(monkeypatch):
    _db, clean = _reload_clean_modules()
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "RESULT_SERVER")
    health = clean.health()
    assert health["runtime_role"] == "RESULT_SERVER"
    assert "result_snapshot_active" in health


def test_result_server_organize_routes_are_guarded(monkeypatch):
    _db, clean = _reload_clean_modules()
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "RESULT_SERVER")
    assert clean.is_result_server() is True



def test_result_server_does_not_run_removed_v4_scope_cleanup(monkeypatch):
    _db, clean = _reload_clean_modules()
    import v4_scope_migration

    monkeypatch.setenv("G2B_RUNTIME_ROLE", "RESULT_SERVER")
    monkeypatch.setattr(
        v4_scope_migration,
        "apply_v4_scope_reset",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("4.1 runtime must not invoke v4 scope cleanup")
        ),
    )
    clean._BACKEND_STATE.update(
        initialized=False,
        initializing=False,
        backend_ok=False,
        backend_error="",
        attempts=0,
    )

    assert clean.initialize_backend(force=True) is True
    state = clean.backend_status()
    assert state["backend_ok"] is True
    assert state["backend_error"] == ""



def test_new_operational_worker_does_not_inherit_stale_wake(monkeypatch):
    _db, clean = _reload_clean_modules()
    started = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def is_alive(self):
            return False

        def start(self):
            started.append(self.name)

    clean._RECENT_COLLECTION_THREAD = None
    clean._RECENT_COLLECTION_WAKE.set()
    monkeypatch.setattr(clean, "can_collect_sources", lambda: True)
    monkeypatch.setattr(clean.threading, "Thread", FakeThread)

    assert clean.schedule_recent_collection(force=True) is True
    assert started == ["g2b-v4-operational-sync"]
    assert clean._RECENT_COLLECTION_WAKE.is_set() is False


def test_operational_worker_starts_while_singleton_lock_is_held(monkeypatch):
    _db, clean = _reload_clean_modules()
    created = []

    class ReservedThread:
        ident = None

        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
            created.append(self)

        def is_alive(self):
            return self.started

        def start(self):
            assert clean._RECENT_COLLECTION_LOCK.locked() is True
            self.started = True
            self.ident = 12345

    clean._RECENT_COLLECTION_THREAD = None
    monkeypatch.setattr(clean, "can_collect_sources", lambda: True)
    monkeypatch.setattr(clean.threading, "Thread", ReservedThread)

    assert clean.schedule_recent_collection(force=True) is True
    assert len(created) == 1
    assert clean._RECENT_COLLECTION_THREAD is created[0]
    assert created[0].started is True

    # Once the singleton has started, another request cannot create a second one.
    assert clean.schedule_recent_collection(force=True) is False
    assert len(created) == 1


def test_worker_start_failure_releases_singleton_slot(monkeypatch):
    _db, clean = _reload_clean_modules()

    class BrokenThread:
        ident = None

        def __init__(self, *, target, name, daemon):
            pass

        def is_alive(self):
            return False

        def start(self):
            raise RuntimeError("synthetic worker start failure")

    clean._RECENT_COLLECTION_THREAD = None
    monkeypatch.setattr(clean, "can_collect_sources", lambda: True)
    monkeypatch.setattr(clean.threading, "Thread", BrokenThread)

    with __import__("pytest").raises(
        RuntimeError, match="synthetic worker start failure"
    ):
        clean.schedule_recent_collection(force=True)

    assert clean._RECENT_COLLECTION_THREAD is None


def test_existing_operational_worker_is_not_woken_by_passive_schedule(monkeypatch):
    _db, clean = _reload_clean_modules()

    class LiveThread:
        def is_alive(self):
            return True

    clean._RECENT_COLLECTION_THREAD = LiveThread()
    clean._RECENT_COLLECTION_WAKE.clear()
    monkeypatch.setattr(clean, "can_collect_sources", lambda: True)
    monkeypatch.setattr(clean, "_auto_sync_enabled", lambda: True)

    assert clean.schedule_recent_collection() is False
    assert clean._RECENT_COLLECTION_WAKE.is_set() is False


def test_existing_operational_worker_is_woken_without_second_thread(monkeypatch):
    _db, clean = _reload_clean_modules()

    class LiveThread:
        def is_alive(self):
            return True

    clean._RECENT_COLLECTION_THREAD = LiveThread()
    clean._RECENT_COLLECTION_WAKE.clear()
    monkeypatch.setattr(clean, "can_collect_sources", lambda: True)

    assert clean.schedule_recent_collection(force=True) is False
    assert clean._RECENT_COLLECTION_WAKE.is_set() is True


def test_cross_process_lease_blocks_source_cycle_when_held_elsewhere(monkeypatch):
    from contextlib import nullcontext

    _db, clean = _reload_clean_modules()
    import budget_storage

    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: True)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "operational_cycle_lease",
        lambda: nullcontext(False),
    )
    monkeypatch.setattr(
        clean,
        "_run_recent_collection_once_impl",
        lambda: (_ for _ in ()).throw(
            AssertionError("source cycle must not run without process lease")
        ),
    )

    result = clean._run_recent_collection_once()
    status = clean.recent_collection_status()

    assert result["operational_cycle_lease"] == "HELD_BY_OTHER_PROCESS"
    assert result["shopping"] is None
    assert result["budget"] is None
    assert status["state"] == "IDLE"
    assert status["last_status"] == "LEASE_HELD"


def test_cross_process_lease_allows_single_source_cycle(monkeypatch):
    from contextlib import nullcontext

    _db, clean = _reload_clean_modules()
    import budget_storage

    calls = []
    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: True)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "operational_cycle_lease",
        lambda: nullcontext(True),
    )
    monkeypatch.setattr(
        clean,
        "_run_recent_collection_once_impl",
        lambda: calls.append("cycle") or {"budget": {"status": "COMPLETE"}},
    )

    result = clean._run_recent_collection_once()

    assert calls == ["cycle"]
    assert result["budget"]["status"] == "COMPLETE"


def test_cycle_exception_after_process_lease_reaches_worker_safety_net(monkeypatch):
    from contextlib import nullcontext

    _db, clean = _reload_clean_modules()
    import budget_storage

    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: True)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "operational_cycle_lease",
        lambda: nullcontext(True),
    )
    monkeypatch.setattr(
        clean,
        "_run_recent_collection_once_impl",
        lambda: (_ for _ in ()).throw(
            RuntimeError("synthetic cycle bug")
        ),
    )

    with __import__("pytest").raises(RuntimeError, match="synthetic cycle bug"):
        clean._run_recent_collection_once()


def test_process_lease_connection_failure_is_fail_soft(monkeypatch):
    from contextlib import contextmanager

    _db, clean = _reload_clean_modules()
    import budget_storage

    @contextmanager
    def broken_lease():
        raise RuntimeError("synthetic lease unavailable")
        yield

    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: True)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage, "operational_cycle_lease", broken_lease
    )
    monkeypatch.setattr(
        clean,
        "_run_recent_collection_once_impl",
        lambda: (_ for _ in ()).throw(
            AssertionError("source cycle must not run without lease")
        ),
    )

    result = clean._run_recent_collection_once()
    status = clean.recent_collection_status()

    assert result["operational_cycle_lease"] == "UNAVAILABLE"
    assert status["state"] == "WAITING_STORAGE"
    assert status["budget_status"] == "WAITING_POSTGRES"
    assert status["last_error"] == "LEASE:RuntimeError"


def test_worker_retries_process_lease_conflict_quickly(monkeypatch):
    _db, clean = _reload_clean_modules()

    waits = []

    class FakeWake:
        def clear(self):
            pass

        def wait(self, seconds):
            waits.append(seconds)
            raise SystemExit("stop after first wait")

    monkeypatch.setattr(
        clean,
        "_run_recent_collection_once",
        lambda: {"operational_cycle_lease": "HELD_BY_OTHER_PROCESS"},
    )
    monkeypatch.setattr(clean, "_RECENT_COLLECTION_WAKE", FakeWake())

    with __import__("pytest").raises(SystemExit, match="stop after first wait"):
        clean._recent_collection_worker()

    assert waits == [clean.OPERATIONAL_LEASE_RETRY_SECONDS]
    assert clean.OPERATIONAL_LEASE_RETRY_SECONDS < clean.SHOPPING_SYNC_INTERVAL_SECONDS


def test_backend_initialization_does_not_prequeue_second_collection_cycle(monkeypatch):
    _db, clean = _reload_clean_modules()
    calls = []
    clean._RECENT_COLLECTION_WAKE.clear()
    monkeypatch.setattr(
        clean,
        "schedule_recent_collection",
        lambda **kwargs: calls.append(dict(kwargs)) or True,
    )

    assert clean.initialize_backend(force=True) is True
    assert calls == [{}]
    assert clean._RECENT_COLLECTION_WAKE.is_set() is False


def test_budget_running_cycle_is_never_promoted_to_complete(monkeypatch):
    from contextlib import nullcontext

    _db, clean = _reload_clean_modules()
    import budget_appropriation_vnext
    import budget_reorganize_vnext
    import budget_storage
    import budget_vnext
    import lofin_vnext_http
    import shopping_recent_vnext
    import vnext_source_guard

    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "get_service_key", lambda default="": "G2B")
    monkeypatch.setattr(
        shopping_recent_vnext,
        "collect_forward",
        lambda **kwargs: {"status": "COMPLETE"},
    )
    monkeypatch.setattr(budget_storage, "storage_ready", lambda: True)
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "LOFIN")
    monkeypatch.setattr(
        vnext_source_guard,
        "operational_budget_source_context",
        lambda **kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        budget_appropriation_vnext,
        "collect_full_appropriation",
        lambda *args, **kwargs: {"status": "COMPLETE", "complete": True},
    )
    monkeypatch.setattr(
        budget_vnext,
        "collect_full_budget",
        lambda *args, **kwargs: {"status": "RUNNING", "complete": False},
    )
    monkeypatch.setattr(
        budget_reorganize_vnext,
        "reorganize_existing_budget_raw",
        lambda **kwargs: {"complete": True},
    )
    monkeypatch.setattr(
        budget_storage,
        "purge_history",
        lambda days, **kwargs: {"retention_days": days, **kwargs},
    )

    clean._run_recent_collection_once()
    status = clean.recent_collection_status()

    assert status["shopping_status"] == "COMPLETE"
    assert status["budget_status"] == "PARTIAL"
    assert status["state"] == "PARTIAL"
    assert status["last_status"] == "PARTIAL"



def test_budget_retention_runs_even_when_lofin_key_is_missing(monkeypatch):
    _db, clean = _reload_clean_modules()
    import budget_storage
    import lofin_vnext_http

    calls = []
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "get_service_key", lambda default="": "")
    monkeypatch.setattr(budget_storage, "storage_ready", lambda: True)
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "")
    monkeypatch.setattr(
        budget_storage,
        "purge_history",
        lambda days, **kwargs: calls.append((days, kwargs)) or {"retention_days": days, **kwargs},
    )

    result = clean._run_recent_collection_once()
    status = clean.recent_collection_status()

    assert calls == [(
        clean.BUDGET_RETENTION_DAYS,
        {"receipt_retention_days": clean.BUDGET_RECEIPT_RETENTION_DAYS},
    )]
    assert result["budget"] is None
    assert result["budget_retention"]["retention_days"] == clean.BUDGET_RETENTION_DAYS
    assert status["budget_status"] == "WAITING_KEY"
    assert status["state"] == "WAITING_KEYS"



def test_retention_expiry_triggers_budget_read_model_prune_without_source_key(
    monkeypatch
):
    _db, clean = _reload_clean_modules()
    import budget_projection_vnext
    import budget_storage
    import lofin_vnext_http

    prunes = []
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "get_service_key", lambda default="": "")
    monkeypatch.setattr(budget_storage, "storage_ready", lambda: True)
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "")
    monkeypatch.setattr(
        budget_storage,
        "purge_history",
        lambda days, **kwargs: {
            "retention_days": days,
            "expired_current_records": 2,
            **kwargs,
        },
    )
    monkeypatch.setattr(
        budget_projection_vnext,
        "prune_stale_budget_read_model",
        lambda: prunes.append("pruned") or {
            "deleted_projection_rows": 2,
            "deleted_classification_rows": 2,
        },
    )

    result = clean._run_recent_collection_once()
    status = clean.recent_collection_status()

    assert prunes == ["pruned"]
    assert result["budget_read_model_prune"] == {
        "deleted_projection_rows": 2,
        "deleted_classification_rows": 2,
    }
    assert status["budget_status"] == "WAITING_KEY"
    assert status["state"] == "WAITING_KEYS"



def test_runtime_counts_use_postgres_budget_current_state(monkeypatch, tmp_path):
    _db, clean = _reload_clean_modules()
    import budget_pg_store
    import budget_reorganize_vnext
    import vnext_store
    from vnext_schema import CLASSIFIER_VERSION

    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'runtime-budget.sqlite3'}",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()

    payload = {
        "fyr": "2026",
        "exe_ymd": "20261001",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "laf_hg_nm": "수원시",
        "dept_cd": "D1",
        "dbiz_cd": "ACTIVE",
        "dbiz_nm": "LED 가로등 교체",
        "acnt_dv_cd": "A1",
        "bdg_cash_amt": "1000",
        "ep_amt": "100",
    }

    try:
        budget_pg_store.preserve_observation(
            "budget",
            "pg-active",
            payload,
            source_system="지방재정365",
            source_operation="QWGJK_FULL_V2_SNAPSHOT",
            source_date="2026-10-01",
        )
        assert budget_reorganize_vnext.reorganize_existing_budget_raw(
            fiscal_year=2026
        )["complete"] is True

        legacy_sha = vnext_store.preserve_raw(
            "budget",
            "sqlite-stale",
            {**payload, "dbiz_cd": "STALE", "dbiz_nm": "LED 과거 잔여"},
            source_system="legacy",
            source_operation="legacy",
            source_date="2026-09-01",
        )
        vnext_store.save_classification(
            "budget",
            "sqlite-stale",
            "LIGHTING",
            classifier_version=CLASSIFIER_VERSION,
            source_payload_sha256=legacy_sha,
        )

        clean._BACKEND_STATE["backend_ok"] = True
        raw = {
            str(row["dataset"]): row
            for row in clean.raw_counts()
        }
        targets = clean.target_dataset_counts()

        assert raw["budget"]["n"] == 1
        assert raw["budget"]["last_at"]
        assert targets["budget"] == 1
    finally:
        budget_pg_store.reset_engine_cache()



def test_budget_postgres_failure_does_not_take_http_process_down(monkeypatch):
    _db, clean = _reload_clean_modules()
    import budget_storage
    import lofin_vnext_http

    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "get_service_key", lambda default="": "")
    monkeypatch.setattr(
        budget_storage,
        "storage_ready",
        lambda: (_ for _ in ()).throw(
            RuntimeError("BUDGET_POSTGRES_SCHEMA_CREATE_FAILED")
        ),
    )
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "LOFIN")

    result = clean._run_recent_collection_once()
    status = clean.recent_collection_status()
    live = clean.live()
    health = clean.health()

    assert result["budget"] is None
    assert status["budget_status"] == "WAITING_POSTGRES"
    assert status["state"] == "FAILED"
    assert "budget_prepare:RuntimeError" in status["last_error"]
    assert live["status"] == "ok"
    assert live["process_alive"] is True
    assert health["status"] == "ok"
    assert health["process_alive"] is True



def test_unified_production_ready_requires_budget_postgres_but_live_stays_up(
    monkeypatch
):
    _db, clean = _reload_clean_modules()
    import budget_storage

    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "is_unified", lambda: True)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)
    monkeypatch.setattr(
        budget_storage, "storage_configured", lambda: False
    )
    monkeypatch.setattr(
        budget_storage, "storage_error_code", lambda: ""
    )

    response = clean.ready()
    payload = __import__("json").loads(response.body.decode("utf-8"))
    live = clean.live()
    health = clean.health()

    assert response.status_code == 503
    assert payload["budget_postgres_required"] is True
    assert payload["budget_postgres_configured"] is False
    assert payload["budget_postgres_ready"] is False
    assert payload["budget_postgres_error_code"] == "BUDGET_POSTGRES_NOT_CONFIGURED"
    assert payload["operational_ready"] is False
    assert live["status"] == "ok"
    assert live["process_alive"] is True
    assert health["status"] == "ok"
    assert health["process_alive"] is True
    assert health["required_boot_env"] == ["G2B_DATABASE_URL"]


def test_unified_production_ready_turns_200_after_budget_postgres_is_ready(
    monkeypatch
):
    _db, clean = _reload_clean_modules()
    import budget_storage

    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "is_unified", lambda: True)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)
    monkeypatch.setattr(
        budget_storage, "storage_configured", lambda: True
    )
    monkeypatch.setattr(
        budget_storage, "storage_ready", lambda: True
    )
    monkeypatch.setattr(
        budget_storage, "storage_error_code", lambda: ""
    )

    response = clean.ready()
    payload = __import__("json").loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["budget_postgres_required"] is True
    assert payload["budget_postgres_configured"] is True
    assert payload["budget_postgres_ready"] is True
    assert payload["operational_ready"] is True


def test_result_server_production_does_not_require_budget_postgres(monkeypatch):
    _db, clean = _reload_clean_modules()

    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)

    response = clean.ready()
    payload = __import__("json").loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["budget_postgres_required"] is False
    assert payload["budget_postgres_ready"] is True
    assert payload["operational_ready"] is True



def test_unified_health_never_probes_postgres_network(monkeypatch):
    _db, clean = _reload_clean_modules()
    import budget_storage

    monkeypatch.setattr(clean, "TEST_MODE", False)
    monkeypatch.setattr(clean, "is_unified", lambda: True)
    monkeypatch.setattr(clean, "db_is_persistent", lambda: True)
    monkeypatch.setattr(
        budget_storage, "storage_configured", lambda: True
    )
    monkeypatch.setattr(
        budget_storage,
        "storage_ready",
        lambda: (_ for _ in ()).throw(
            AssertionError("health must not probe postgres network")
        ),
    )
    monkeypatch.setattr(
        budget_storage, "storage_error_code", lambda: ""
    )
    clean._BUDGET_POSTGRES_PROBE_STATE.update(
        configured=True,
        ready=False,
        error_code="",
        checked_at=0.0,
    )

    health = clean.health()

    assert health["status"] == "ok"
    assert health["process_alive"] is True
    assert health["budget_postgres_required"] is True
    assert health["budget_postgres_configured"] is True
    assert health["budget_postgres_ready"] is False
    assert health["operational_ready"] is False


def test_operational_budget_collects_future_aidfa_before_current_qwgjk(monkeypatch):
    from contextlib import nullcontext

    _db, clean = _reload_clean_modules()
    import budget_appropriation_vnext
    import budget_reorganize_vnext
    import budget_storage
    import budget_vnext
    import lofin_vnext_http
    import shopping_recent_vnext
    import vnext_source_guard

    calls = []
    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "get_service_key", lambda default="": "G2B")
    monkeypatch.setattr(
        shopping_recent_vnext,
        "collect_forward",
        lambda **kwargs: {"status": "COMPLETE"},
    )
    monkeypatch.setattr(budget_storage, "storage_ready", lambda: True)
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "LOFIN")
    monkeypatch.setattr(
        vnext_source_guard,
        "operational_budget_source_context",
        lambda **kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        budget_appropriation_vnext,
        "collect_full_appropriation",
        lambda year, **kwargs: (
            calls.append(("future", year, kwargs.get("refresh_date")))
            or {"status": "COMPLETE", "complete": True}
        ),
    )
    monkeypatch.setattr(
        budget_vnext,
        "collect_full_budget",
        lambda year, *args, **kwargs: (
            calls.append(("current", year, args[0] if args else ""))
            or {"status": "COMPLETE", "complete": True}
        ),
    )
    monkeypatch.setattr(
        budget_reorganize_vnext,
        "reorganize_existing_budget_raw",
        lambda **kwargs: {"complete": True},
    )
    monkeypatch.setattr(
        budget_storage,
        "purge_history",
        lambda days, **kwargs: {"retention_days": days, **kwargs},
    )

    result = clean._run_recent_collection_once()
    status = clean.recent_collection_status()

    assert calls[0][0] == "future"
    assert calls[0][1] == calls[1][1] + 1
    assert calls[0][2]
    assert calls[1][0] == "current"
    assert result["future_budget"]["complete"] is True
    assert result["budget"]["complete"] is True
    assert status["future_budget_status"] == "COMPLETE"
    assert status["budget_status"] == "COMPLETE"


def test_operational_budget_caps_pages_to_remaining_lofin_quota(monkeypatch):
    from contextlib import contextmanager

    _db, clean = _reload_clean_modules()
    import budget_appropriation_vnext
    import budget_reorganize_vnext
    import budget_storage
    import budget_vnext
    import lofin_vnext_http
    import shopping_recent_vnext
    import vnext_source_guard

    monkeypatch.setattr(clean, "backend_status", lambda: {"backend_ok": True})
    monkeypatch.setattr(clean, "is_unified", lambda: False)
    monkeypatch.setattr(clean, "get_service_key", lambda default="": "")
    monkeypatch.setattr(budget_storage, "storage_ready", lambda: True)
    monkeypatch.setattr(lofin_vnext_http, "get_lofin_key", lambda: "LOFIN")
    monkeypatch.setattr(
        lofin_vnext_http,
        "daily_quota_status",
        lambda: {"date": "2026-10-02", "limit": 100, "used": 90, "remaining": 10},
    )

    state = {"permits_used": 0, "limit": 0}

    @contextmanager
    def fake_context(**kwargs):
        state["limit"] = kwargs["max_requests"]
        yield {}

    monkeypatch.setattr(
        vnext_source_guard, "operational_budget_source_context", fake_context
    )
    monkeypatch.setattr(
        vnext_source_guard,
        "current_source_request_context",
        lambda: {"permits_used": state["permits_used"]},
    )

    future_calls = []
    current_calls = []

    def future(year, **kwargs):
        future_calls.append((year, kwargs["max_pages"]))
        state["permits_used"] = 6
        return {"status": "RUNNING", "complete": False}

    monkeypatch.setattr(
        budget_appropriation_vnext, "collect_full_appropriation", future
    )
    monkeypatch.setattr(
        budget_vnext,
        "collect_full_budget",
        lambda year, *args, **kwargs: (
            current_calls.append((year, kwargs["max_pages"]))
            or {"status": "RUNNING", "complete": False}
        ),
    )
    monkeypatch.setattr(
        budget_reorganize_vnext,
        "reorganize_existing_budget_raw",
        lambda **kwargs: {"complete": True},
    )
    monkeypatch.setattr(
        budget_storage,
        "purge_history",
        lambda days, **kwargs: {"retention_days": days, **kwargs},
    )

    result = clean._run_recent_collection_once()

    assert state["limit"] == 10
    assert future_calls[0][1] == 10
    assert current_calls[0][1] == 4
    assert result["lofin_cycle_request_budget"] == 10
