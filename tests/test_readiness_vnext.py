import datetime as dt
import json

import db
import classification_vnext
import readiness_vnext
import vnext_store


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
    assert len(coverage["expected_raw_datasets"]) == 7
    assert len(coverage["canary_datasets"]) == 6
    assert len(coverage["historical_datasets"]) == 6


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


def test_storage_readiness_partitions_structural_fresh_stale_and_legacy_proofs(monkeypatch, tmp_path):
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
    assert result["stability_verified_checkpoints"] == 3
    assert result["stability_structural_verified_checkpoints"] == 3
    assert result["stability_fresh_verified_checkpoints"] == 1
    assert result["stability_stale_verified_checkpoints"] == 1
    assert result["stability_verified_without_timestamp"] == 1


def test_readiness_status_stays_blocked_without_g2b_key(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    monkeypatch.setattr(readiness_vnext, "get_service_key", lambda default="": "")
    monkeypatch.setattr(readiness_vnext, "get_lofin_key", lambda: "")
    report = readiness_vnext.build_readiness_report()
    assert report["static_coverage_ok"] is True
    assert report["status"] == "G2B_CANARY_BLOCKED"
    assert report["historical_live_collection_locked_by_default"] is True
    assert report["stability_max_age_hours"] == 24
    assert "stability_timestamp" in report["notes"]
