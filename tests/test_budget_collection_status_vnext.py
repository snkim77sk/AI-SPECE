import budget_collection_status_vnext
import budget_read_vnext
import budget_vnext
import db
import vnext_store


def _by_dataset(status):
    return {row["dataset"]: row for row in status["datasets"]}


def test_empty_budget_collection_status_is_zero_and_never_claims_source_completeness():
    status = budget_collection_status_vnext.budget_collection_status()

    assert status["scope"] == "CURRENT_STORED_RAW_AND_CHECKPOINTS_ONLY"
    assert status["totals"] == {
        "raw_rows": 0,
        "raw_revisions": 0,
        "checkpoints": 0,
        "verified_complete_scopes": 0,
        "compacted_complete_scopes": 0,
        "unverified_complete_scopes": 0,
    }
    assert set(_by_dataset(status)) == {
        "budget", "budget_appropriation", "education_budget"
    }
    assert all(row["scopes"] == [] for row in status["datasets"])
    assert all(
        row["scope"] == "CURRENT_BUDGET_STORAGE_ONLY"
        and row["source_collection_completeness_verified"] is False
        for row in status["datasets"]
    )
    assert status["read_only"] is True
    assert status["source_traffic"] is False
    assert status["source_collection_completeness_verified"] is False


def test_raw_without_checkpoint_is_reported_without_inventing_complete_scope():
    vnext_store.preserve_raw(
        "budget", "q1",
        {
            "fyr": "2026", "exe_ymd": "20260921",
            "laf_cd": "4111000", "dept_cd": "D1",
            "dbiz_cd": "P1", "acnt_dv_cd": "A1",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-21",
    )

    status = budget_collection_status_vnext.budget_collection_status()
    budget = _by_dataset(status)["budget"]

    assert budget["raw_rows"] == 1
    assert budget["raw_revisions"] == 1
    assert budget["checkpoint_count"] == 0
    assert budget["verified_complete_scopes"] == 0
    assert budget["unverified_complete_scopes"] == 0


def test_verified_qwgjk_complete_scope_is_distinguished_from_plain_complete(monkeypatch):
    row = {
        "fyr": "2026",
        "exe_ymd": "20260921",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "D1",
        "dbiz_cd": "P1",
        "acnt_dv_cd": "A1",
    }
    monkeypatch.setattr(
        budget_vnext,
        "fetch_budget_page",
        lambda *args, **kwargs: ([row], 1, "INFO-000", ""),
    )

    collected = budget_vnext.collect_full_budget(
        2026, "2026-09-21", resume=False
    )
    assert collected["complete"] is True

    status = budget_collection_status_vnext.budget_collection_status()
    budget = _by_dataset(status)["budget"]

    assert budget["checkpoint_count"] == 1
    assert budget["checkpoint_status_counts"] == {"COMPLETE": 1}
    assert budget["verified_complete_scopes"] == 1
    assert budget["unverified_complete_scopes"] == 0
    assert budget["scopes"][0]["scope_key"] == "2026:2026-09-21"
    assert budget["scopes"][0]["receipt_verified"] is True
    assert budget["local_receipt_verified_complete_scopes"] == 1
    assert budget["source_collection_completeness_verified"] is False
    assert status["local_storage_completeness_scope"] == "REQUESTED_CHECKPOINT_SCOPES_ONLY"
    assert status["source_collection_completeness_verified"] is False


def test_legacy_or_manual_complete_checkpoint_without_receipts_is_unverified():
    vnext_store.save_checkpoint(
        "budget_appropriation",
        "2026:4100000",
        range_start="2026",
        range_end="4100000",
        page_no=2,
        page_size=1000,
        source_total=10,
        fetched_count=10,
        saved_count=10,
        status="COMPLETE",
    )

    status = budget_collection_status_vnext.budget_collection_status()
    appropriation = _by_dataset(status)["budget_appropriation"]

    assert appropriation["checkpoint_status_counts"] == {"COMPLETE": 1}
    assert appropriation["verified_complete_scopes"] == 0
    assert appropriation["unverified_complete_scopes"] == 1
    assert appropriation["scopes"][0]["receipt_verified"] is False
    assert status["totals"]["unverified_complete_scopes"] == 1


def test_postgres_compacted_history_complete_is_not_unverified(monkeypatch):
    import budget_pg_collection
    import budget_pg_store
    import budget_storage

    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(budget_storage, "backend_name", lambda: "POSTGRESQL")
    monkeypatch.setattr(
        budget_storage,
        "dataset_counts",
        lambda dataset: {
            "dataset": dataset,
            "current_records": 0,
            "observations": 0,
            "superseded_observations": 0,
            "last_seen_at": "",
        },
    )
    monkeypatch.setattr(
        budget_pg_store,
        "list_checkpoints",
        lambda dataset: (
            [{
                "dataset": "budget",
                "scope_key": "history:2026:2026-01-01",
                "cursor_value": "{}",
                "range_start": "2026",
                "range_end": "2026-01-01",
                "page_no": 2,
                "page_size": 1000,
                "source_total": 100,
                "fetched_count": 100,
                "saved_count": 100,
                "status": "COMPLETE",
                "last_error": "",
                "updated_at": "2026-10-02T00:00:00+00:00",
            }] if dataset == "budget" else []
        ),
    )
    monkeypatch.setattr(
        budget_pg_collection,
        "verified_checkpoint",
        lambda checkpoint, require_current=False: False,
    )

    status = budget_collection_status_vnext.budget_collection_status()
    budget = _by_dataset(status)["budget"]

    assert budget["verified_complete_scopes"] == 0
    assert budget["compacted_complete_scopes"] == 1
    assert budget["unverified_complete_scopes"] == 0
    assert budget["scopes"][0]["receipt_verified"] is False
    assert budget["scopes"][0]["receipts_compacted"] is True
    assert status["totals"]["compacted_complete_scopes"] == 1
    assert status["totals"]["unverified_complete_scopes"] == 0


def test_budget_status_surfaces_collection_status_without_mutating_data():
    vnext_store.preserve_raw(
        "education_budget", "e1",
        {
            "YMQ": "2026", "officeCode": "J10",
            "projectCode": "E1", "사업명": "학교 LED 조명 개선",
        },
        source_system="지방교육재정알리미(typeA)",
        source_operation="EDUINFO_FULL_RAW_V1:typeA",
        source_date="2026",
    )
    with db.connect() as conn:
        before = {
            "raw": conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0],
            "revisions": conn.execute(
                "SELECT COUNT(*) FROM raw_record_revisions"
            ).fetchone()[0],
            "checkpoints": conn.execute(
                "SELECT COUNT(*) FROM collection_checkpoints"
            ).fetchone()[0],
        }

    status = budget_read_vnext.budget_status(fiscal_year=2026)

    with db.connect() as conn:
        after = {
            "raw": conn.execute("SELECT COUNT(*) FROM raw_records").fetchone()[0],
            "revisions": conn.execute(
                "SELECT COUNT(*) FROM raw_record_revisions"
            ).fetchone()[0],
            "checkpoints": conn.execute(
                "SELECT COUNT(*) FROM collection_checkpoints"
            ).fetchone()[0],
        }
    assert after == before
    assert status["collection"]["totals"]["raw_rows"] == 1
    assert status["collection"]["source_traffic"] is False
    assert status["collection"]["source_collection_completeness_verified"] is False



def test_budget_collection_status_uses_short_operational_cache(monkeypatch):
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_BUDGET_STATUS_CACHE_SECONDS", "15")
    budget_collection_status_vnext._STATUS_CACHE.update(at=0.0, value=None)
    monkeypatch.setattr(
        budget_collection_status_vnext.budget_storage,
        "using_postgres",
        lambda: True,
    )

    calls = []

    def fake_dataset_counts(dataset):
        calls.append(dataset)
        return {
            "dataset": dataset,
            "scope": "CURRENT_BUDGET_STORAGE_ONLY",
            "raw_backend": "POSTGRESQL",
            "raw_rows": 1,
            "raw_revisions": 1,
            "checkpoint_count": 0,
            "checkpoint_status_counts": {},
            "verified_complete_scopes": 0,
            "unverified_complete_scopes": 0,
            "local_receipt_verified_complete_scopes": 0,
            "source_collection_completeness_verified": False,
            "source_collection_completeness_reason": "TEST",
            "scopes": [],
        }

    monkeypatch.setattr(
        budget_collection_status_vnext,
        "_dataset_counts",
        fake_dataset_counts,
    )

    first = budget_collection_status_vnext.budget_collection_status()
    second = budget_collection_status_vnext.budget_collection_status()

    assert first is second
    assert calls == list(budget_collection_status_vnext.BUDGET_DATASETS)
    budget_collection_status_vnext._STATUS_CACHE.update(at=0.0, value=None)

def test_monitor_budget_status_batches_counts_and_skips_receipt_fanout(monkeypatch):
    import budget_pg_collection

    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setenv("G2B_MONITOR_STATUS_CACHE_SECONDS", "30")
    budget_collection_status_vnext._MONITOR_STATUS_CACHE.update(at=0.0, value=None)
    monkeypatch.setattr(
        budget_collection_status_vnext.budget_storage,
        "using_postgres",
        lambda: True,
    )
    monkeypatch.setattr(
        budget_collection_status_vnext.budget_storage,
        "backend_name",
        lambda: "POSTGRESQL",
    )

    count_calls = []
    def counts_all(datasets):
        count_calls.append(tuple(datasets))
        return {
            name: {
                "dataset": name,
                "current_records": 10 if name == "budget" else 1,
                "observations": 20 if name == "budget" else 1,
                "last_seen_at": "2026-10-07T00:00:00+00:00",
            }
            for name in datasets
        }

    checkpoint_calls = []
    def checkpoints(dataset):
        checkpoint_calls.append(dataset)
        if dataset != "budget":
            return []
        return [{
            "dataset": "budget",
            "scope_key": "history:2026:2026-01-01",
            "cursor_value": "{}",
            "range_start": "2026",
            "range_end": "2026-01-01",
            "page_no": 2,
            "page_size": 1000,
            "source_total": 10,
            "fetched_count": 10,
            "saved_count": 10,
            "status": "COMPLETE",
            "last_error": "",
            "updated_at": "2026-10-07T00:00:00+00:00",
        }]

    monkeypatch.setattr(
        budget_collection_status_vnext.budget_storage,
        "dataset_counts_all",
        counts_all,
    )
    monkeypatch.setattr(
        budget_collection_status_vnext.budget_pg_store,
        "list_checkpoints",
        checkpoints,
    )
    monkeypatch.setattr(
        budget_pg_collection,
        "verified_checkpoint",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("monitor fast path must not reverify every receipt")
        ),
    )

    first = budget_collection_status_vnext.budget_collection_monitor_status()
    second = budget_collection_status_vnext.budget_collection_monitor_status()

    assert first is second
    assert first["monitor_fast_path"] is True
    assert first["receipt_verification_performed"] is False
    assert count_calls == [budget_collection_status_vnext.BUDGET_DATASETS]
    assert checkpoint_calls == list(budget_collection_status_vnext.BUDGET_DATASETS)
    budget = _by_dataset(first)["budget"]
    assert budget["checkpoint_count"] == 1
    assert budget["compacted_complete_scopes"] == 1
    assert budget["receipt_verification_performed"] is False
    budget_collection_status_vnext._MONITOR_STATUS_CACHE.update(at=0.0, value=None)

