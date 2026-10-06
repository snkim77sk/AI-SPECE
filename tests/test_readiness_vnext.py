import datetime as dt
import json

import db
import budget_collection_status_vnext
import budget_storage
import classification_vnext
import readiness_vnext
import vnext_stability
import vnext_store
from vnext_collection import collect_pages


def _fresh_db(monkeypatch, tmp_path):
    path = tmp_path / "readiness.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    db.init_db()
    vnext_store.ensure_foundation()
    return path


def test_static_coverage_has_no_missing_or_unexpected_dataset():
    coverage = readiness_vnext.static_coverage()
    assert coverage["missing_collectors"] == []
    assert coverage["unexpected_collectors"] == []
    assert coverage["missing_historical"] == []
    assert coverage["unexpected_historical"] == []
    assert coverage["missing_canary"] == []
    assert coverage["unexpected_canary"] == []
    assert len(coverage["expected_raw_datasets"]) == 4
    assert coverage["budget_raw_datasets"] == [
        "budget", "budget_appropriation", "education_budget"
    ]
    assert coverage["canary_datasets"] == []
    assert coverage["historical_datasets"] == []



def test_storage_readiness_includes_aidfa_and_education_budget_raw(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    vnext_store.preserve_raw(
        "budget_appropriation", "a1",
        {"fyr": "2026", "fld_cd": "F1", "biz_bdg_tott_amt": "1000"},
        source_system="지방재정365 AIDFA",
    )
    vnext_store.preserve_raw(
        "education_budget", "e1",
        {"YMQ": "2026", "projectCode": "E1", "예산액": "2000"},
        source_system="지방교육재정알리미(typeA)",
    )

    storage = readiness_vnext.storage_readiness()

    assert set(storage) == set(readiness_vnext.EXPECTED_RAW_DATASETS)
    assert storage["budget_appropriation"]["latest_raw_rows"] == 1
    assert storage["budget_appropriation"]["revision_rows"] == 1
    assert storage["education_budget"]["latest_raw_rows"] == 1
    assert storage["education_budget"]["revision_rows"] == 1
    assert storage["budget_appropriation"]["unclassified_or_stale_rows"] == 1
    assert storage["education_budget"]["unclassified_or_stale_rows"] == 1
    assert storage["budget_appropriation"]["readiness_scope"] == "CURRENT_BUDGET_STORAGE_ONLY"
    assert storage["budget_appropriation"]["source_collection_completeness_verified"] is False
    assert storage["education_budget"]["source_collection_completeness_verified"] is False

def test_credential_readiness_returns_only_booleans(monkeypatch):
    monkeypatch.setattr(
        readiness_vnext,
        "source_credential_configured",
        lambda name: name in {"g2b_service_key", "lofin_api_key", "eduinfo_api_key"},
    )
    result = readiness_vnext.credential_readiness()
    assert result == {
        "g2b_service_key_configured": True,
        "lofin_api_key_configured": True,
        "eduinfo_api_key_configured": True,
    }
    assert "super-secret" not in repr(result)


def test_storage_readiness_marks_changed_raw_as_stale_until_reclassified(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    dataset = "shopping_delivery"
    key = "A|000"
    vnext_store.preserve_raw(dataset, key, {"prdctNm": "LED가로등", "dtilPrdctClsfcNo": "3911160302"}, source_system="G2B")
    classification_vnext.classify_dataset(dataset)

    first = readiness_vnext.storage_readiness()[dataset]
    assert first["latest_raw_rows"] == 1
    assert first["revision_rows"] == 1
    assert first["current_classified_rows"] == 1
    assert first["unclassified_or_stale_rows"] == 0
    assert first["stability_verified_checkpoints"] == 0
    assert first["stability_structural_verified_checkpoints"] == 0
    assert first["stability_fresh_verified_checkpoints"] == 0
    assert first["stability_stale_verified_checkpoints"] == 0
    assert first["stability_verified_without_timestamp"] == 0
    assert first["stability_invalid_metadata_claims"] == 0
    assert first["stability_recollect_required"] == 0
    assert first["oldest_stability_verified_at_utc"] == ""
    assert first["newest_stability_verified_at_utc"] == ""

    vnext_store.preserve_raw(dataset, key, {"prdctNm": "LED보안등", "dtilPrdctClsfcNo": "3911160802"}, source_system="G2B")
    stale = readiness_vnext.storage_readiness()[dataset]
    assert stale["latest_raw_rows"] == 1
    assert stale["revision_rows"] == 2
    assert stale["current_classified_rows"] == 0
    assert stale["unclassified_or_stale_rows"] == 1

    classification_vnext.classify_dataset(dataset)
    current = readiness_vnext.storage_readiness()[dataset]
    assert current["current_classified_rows"] == 1
    assert current["unclassified_or_stale_rows"] == 0


def test_readiness_does_not_trust_direct_stability_metadata_claims(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    dataset = "shopping_delivery"
    now = dt.datetime.now(dt.timezone.utc)

    def save(scope, stamp):
        stable = {"status": "VERIFIED", "generation": scope}
        if stamp is not None:
            stable["verified_at_utc"] = stamp.isoformat()
        meta = {"generation": scope, "stability": stable}
        vnext_store.save_checkpoint(
            dataset, scope,
            cursor_value=json.dumps(meta, sort_keys=True),
            status="COMPLETE",
        )

    save("fresh", now)
    save("stale", now - dt.timedelta(hours=25))
    save("legacy", None)

    result = readiness_vnext.storage_readiness()[dataset]
    assert result["stability_verified_checkpoints"] == 0
    assert result["stability_structural_verified_checkpoints"] == 0
    assert result["stability_fresh_verified_checkpoints"] == 0
    assert result["stability_stale_verified_checkpoints"] == 0
    assert result["stability_verified_without_timestamp"] == 0
    assert result["stability_invalid_metadata_claims"] == 3


def test_readiness_counts_actual_replay_verified_checkpoint(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    dataset = "shopping_delivery"
    scope = "2026-09-16:2026-09-16"
    pages = {1: [{"id": "A"}], 2: []}
    result = collect_pages(
        dataset=dataset,
        scope=scope,
        range_start="2026-09-16",
        range_end="2026-09-16",
        page_size=2,
        max_pages=3,
        resume=True,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
        source_system="TEST",
        source_operation="TEST_LIST",
        source_date=lambda row: "2026-09-16",
        preserve=vnext_store.preserve_raw,
        checkpoint=vnext_store.save_checkpoint,
        lookup=vnext_store.get_checkpoint,
    )
    assert result["complete"] is True
    checked = vnext_stability.verify_checkpoint_source(
        dataset=dataset,
        scope=scope,
        fetch=lambda page, size: (list(pages.get(page, [])), None),
        identity=lambda row: row["id"],
    )
    assert checked["stable"] is True

    ready = readiness_vnext.storage_readiness()[dataset]
    assert ready["stability_verified_checkpoints"] == 1
    assert ready["stability_structural_verified_checkpoints"] == 1
    assert ready["stability_fresh_verified_checkpoints"] == 1
    assert ready["stability_invalid_metadata_claims"] == 0
    assert ready["oldest_stability_verified_at_utc"]
    assert ready["newest_stability_verified_at_utc"]


def test_readiness_scheduler_defaults_off_for_unified(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.delenv("G2B_AUTO_SYNC", raising=False)
    monkeypatch.delenv("G2B_AUTO_SYNC_DISABLE", raising=False)
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(
        readiness_vnext,
        "static_coverage",
        lambda: {
            "missing_collectors": [],
            "unexpected_collectors": [],
            "missing_historical": [],
            "unexpected_historical": [],
            "missing_canary": [],
            "unexpected_canary": [],
        },
    )
    monkeypatch.setattr(
        readiness_vnext,
        "credential_readiness",
        lambda: {
            "g2b_service_key_configured": False,
            "lofin_api_key_configured": False,
            "eduinfo_api_key_configured": False,
        },
    )
    monkeypatch.setattr(
        readiness_vnext,
        "storage_readiness",
        lambda: {
            readiness_vnext.shopping_vnext.DATASET: {
                "readiness_scope": "TEST",
            },
        },
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage, "storage_ready", lambda: True
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage, "storage_error_code", lambda: ""
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage, "backend_name", lambda: "POSTGRESQL"
    )

    report = readiness_vnext.build_readiness_report()
    assert report["production_scheduler_enabled"] is False
    assert report["production_scheduler_policy"] == "EXPLICIT_G2B_AUTO_SYNC_OPT_IN"


def test_readiness_scheduler_respects_emergency_disable(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setenv("G2B_RUNTIME_ROLE", "UNIFIED")
    monkeypatch.setenv("G2B_AUTO_SYNC", "0")
    monkeypatch.setenv("G2B_AUTO_SYNC_DISABLE", "1")
    monkeypatch.setenv("G2B_TEST_MODE", "0")
    monkeypatch.setattr(
        readiness_vnext,
        "static_coverage",
        lambda: {
            "missing_collectors": [],
            "unexpected_collectors": [],
            "missing_historical": [],
            "unexpected_historical": [],
            "missing_canary": [],
            "unexpected_canary": [],
        },
    )
    monkeypatch.setattr(
        readiness_vnext,
        "credential_readiness",
        lambda: {
            "g2b_service_key_configured": False,
            "lofin_api_key_configured": False,
            "eduinfo_api_key_configured": False,
        },
    )
    monkeypatch.setattr(
        readiness_vnext,
        "storage_readiness",
        lambda: {
            readiness_vnext.shopping_vnext.DATASET: {
                "readiness_scope": "TEST",
            },
        },
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage, "storage_ready", lambda: True
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage, "storage_error_code", lambda: ""
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage, "backend_name", lambda: "POSTGRESQL"
    )

    report = readiness_vnext.build_readiness_report()
    assert report["production_scheduler_enabled"] is False
    assert report["production_scheduler_kill_switch"] == (
        "G2B_AUTO_SYNC_DISABLE=1"
    )


def test_readiness_status_stays_blocked_without_g2b_key(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(readiness_vnext, "source_credential_configured", lambda name: False)
    report = readiness_vnext.build_readiness_report()
    assert report["static_coverage_ok"] is True
    assert report["shopping_operational_ready"] is False
    assert report["budget_operational_ready"] is False
    assert report["status"] in {"BUDGET_KEY_WAITING", "BUDGET_POSTGRES_WAITING"}
    assert report["deployment_state"] == "V4_BUDGET_CENTERED"
    assert report["main_merge_hold"] is False
    assert report["live_collection_mode"] == "NORMALIZED_BUDGET_PLUS_NORMALIZED_TARGET_SHOPPING"
    assert report["production_scheduler_enabled"] is False
    assert report["shopping_recent_collection"]["order"] == "FORWARD"
    assert report["shopping_recent_collection"]["start_date"] == "2026-09-01"
    assert report["bulk_historical_hold"] is True
    assert report["approved_historical_context_available"] is False
    assert report["historical_live_collection_locked_by_default"] is True
    assert report["status_scope"] == "EXECUTION_READINESS_NOT_SOURCE_COMPLETENESS"
    assert report["source_collection_completeness_verified"] is False
    assert report["budget_source_collection_completeness_verified"] is False
    assert report["stability_max_age_hours"] == 24
    assert "no1_boundary" in report["notes"]

def test_readiness_reports_source_keys_independently(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)

    monkeypatch.setattr(
        readiness_vnext,
        "source_credential_configured",
        lambda name: name == "g2b_service_key",
    )
    shopping_only = readiness_vnext.build_readiness_report()
    assert shopping_only["shopping_operational_ready"] is True
    assert shopping_only["budget_operational_ready"] is False

    monkeypatch.setattr(
        readiness_vnext,
        "source_credential_configured",
        lambda name: name == "lofin_api_key",
    )
    budget_only = readiness_vnext.build_readiness_report()
    assert budget_only["shopping_operational_ready"] is False
    assert budget_only["budget_operational_ready"] is True


def test_shopping_readiness_does_not_depend_on_budget_postgres(monkeypatch):
    monkeypatch.setattr(
        readiness_vnext,
        "static_coverage",
        lambda: {
            "missing_collectors": [],
            "unexpected_collectors": [],
            "missing_historical": [],
            "unexpected_historical": [],
            "missing_canary": [],
            "unexpected_canary": [],
        },
    )
    monkeypatch.setattr(
        readiness_vnext,
        "credential_readiness",
        lambda: {
            "g2b_service_key_configured": True,
            "lofin_api_key_configured": False,
            "eduinfo_api_key_configured": False,
        },
    )
    monkeypatch.setattr(
        readiness_vnext,
        "storage_readiness",
        lambda: {
            readiness_vnext.shopping_vnext.DATASET: {
                "readiness_scope": "CURRENT_NORMALIZED_SHOPPING_STORAGE_ONLY",
                "raw_backend": "POSTGRESQL",
            },
        },
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage,
        "storage_ready",
        lambda: False,
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage,
        "storage_error_code",
        lambda: "BUDGET_POSTGRES_WAITING",
    )
    monkeypatch.setattr(
        readiness_vnext.budget_storage,
        "backend_name",
        lambda: "POSTGRESQL",
    )

    report = readiness_vnext.build_readiness_report()

    assert report["status"] == "BUDGET_POSTGRES_WAITING"
    assert report["shopping_storage_ready"] is True
    assert report["shopping_operational_ready"] is True
    assert report["budget_operational_ready"] is False


def test_configured_credentials_still_never_claim_source_collection_completeness(
    monkeypatch, tmp_path
):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        readiness_vnext,
        "source_credential_configured",
        lambda name: name in {"g2b_service_key", "lofin_api_key", "eduinfo_api_key"},
    )

    report = readiness_vnext.build_readiness_report()

    assert report["status"] == "OPERATIONAL_READY"
    assert report["shopping_operational_ready"] is True
    assert report["budget_operational_ready"] is True
    assert report["deployment_state"] == "V4_BUDGET_CENTERED"
    assert report["main_merge_hold"] is False
    assert report["live_collection_mode"] == "NORMALIZED_BUDGET_PLUS_NORMALIZED_TARGET_SHOPPING"
    assert report["production_scheduler_enabled"] is False
    assert report["education_budget_key_status"] == "KEY_CONFIGURED_LIVE_HOLD"
    assert report["education_budget_live_transport_hold"] is True
    assert report["status_scope"] == "EXECUTION_READINESS_NOT_SOURCE_COMPLETENESS"
    assert report["source_collection_completeness_verified"] is False
    assert report["budget_source_collection_completeness_verified"] is False
    assert set(report["notes"]["budget_source_limits"]) == {
        "budget", "budget_appropriation", "education_budget"
    }
    assert (
        report["storage"]["education_budget"][
            "source_collection_completeness_verified"
        ]
        is False
    )




def test_postgres_readiness_reuses_bulk_counts_and_hashes(
    monkeypatch, tmp_path
):
    _fresh_db(monkeypatch, tmp_path)
    datasets = tuple(sorted(readiness_vnext.BUDGET_RAW_DATASETS))
    calls = {"counts": 0, "hashes": 0, "status": 0}

    monkeypatch.setattr(
        readiness_vnext,
        "_shopping_storage_readiness",
        lambda: {"readiness_scope": "TEST"},
    )
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_storage,
        "dataset_counts_all",
        lambda selected: calls.__setitem__(
            "counts", calls["counts"] + 1
        ) or {
            name: {
                "dataset": name,
                "current_records": 0,
                "observations": 0,
                "superseded_observations": 0,
                "last_seen_at": "",
            }
            for name in selected
        },
    )
    monkeypatch.setattr(
        budget_storage,
        "current_payload_hashes",
        lambda selected: calls.__setitem__(
            "hashes", calls["hashes"] + 1
        ) or {},
    )
    monkeypatch.setattr(
        budget_storage,
        "dataset_counts",
        lambda dataset: (_ for _ in ()).throw(
            AssertionError("per-dataset postgres count scan forbidden")
        ),
    )
    monkeypatch.setattr(
        budget_collection_status_vnext,
        "budget_collection_status",
        lambda: calls.__setitem__(
            "status", calls["status"] + 1
        ) or {
            "datasets": [
                {
                    "dataset": name,
                    "checkpoint_status_counts": {},
                }
                for name in datasets
            ]
        },
    )

    result = readiness_vnext.storage_readiness()

    assert set(result) == set(readiness_vnext.EXPECTED_RAW_DATASETS)
    assert calls == {"counts": 1, "hashes": 1, "status": 1}
