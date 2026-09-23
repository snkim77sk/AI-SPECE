import datetime as dt
import json

import db
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
    assert len(coverage["expected_raw_datasets"]) == 9
    assert coverage["budget_raw_datasets"] == [
        "budget", "budget_appropriation", "education_budget"
    ]
    assert len(coverage["canary_datasets"]) == 6
    assert len(coverage["historical_datasets"]) == 6



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

def test_credential_readiness_returns_only_booleans(monkeypatch):
    monkeypatch.setattr(readiness_vnext, "get_service_key", lambda default="": "super-secret-g2b")
    monkeypatch.setattr(readiness_vnext, "get_lofin_key", lambda: "super-secret-lofin")
    result = readiness_vnext.credential_readiness()
    assert result == {
        "g2b_service_key_configured": True,
        "lofin_api_key_configured": True,
    }
    assert "super-secret" not in repr(result)


def test_storage_readiness_marks_changed_raw_as_stale_until_reclassified(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    dataset = "bid_notice_goods"
    key = "A|000"
    vnext_store.preserve_raw(dataset, key, {"bidNtceNm": "일반 비품 구매"}, source_system="G2B")
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

    vnext_store.preserve_raw(dataset, key, {"bidNtceNm": "LED 가로등 구매"}, source_system="G2B")
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
    dataset = "bid_notice_goods"
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
    dataset = "bid_notice_goods"
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


def test_readiness_status_stays_blocked_without_g2b_key(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(readiness_vnext, "get_service_key", lambda default="": "")
    monkeypatch.setattr(readiness_vnext, "get_lofin_key", lambda: "")
    report = readiness_vnext.build_readiness_report()
    assert report["static_coverage_ok"] is True
    assert report["status"] == "G2B_CANARY_BLOCKED"
    assert report["historical_live_collection_locked_by_default"] is True
    assert report["stability_max_age_hours"] == 24
    assert "stability_proof" in report["notes"]
