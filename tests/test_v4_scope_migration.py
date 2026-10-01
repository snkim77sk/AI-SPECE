import db
import result_snapshot_vnext
import v4_scope_migration
import vnext_store


def _count(dataset):
    with db.connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM raw_records WHERE dataset=?", (dataset,)
        ).fetchone()[0] or 0)


def test_v4_reset_retries_snapshot_without_redeleting_new_target_data(monkeypatch):
    vnext_store.preserve_raw(
        "shopping_delivery", "old-shopping",
        {"dlvrReqNo": "OLD", "prdctSno": "1", "dtilPrdctClsfcNo": "9999999999"},
    )
    vnext_store.preserve_raw(
        "bid_notice_service", "old-service",
        {"bidNtceNo": "SVC", "bidNtceOrd": "00"},
    )
    vnext_store.preserve_raw(
        "budget", "budget-keep",
        {"fyr": "2026", "dbiz_cd": "P1", "dbiz_nm": "LED 조명 교체"},
    )

    attempts = []

    def fail_snapshot():
        attempts.append("fail")
        raise RuntimeError("synthetic snapshot failure")

    monkeypatch.setattr(result_snapshot_vnext, "clear_snapshot", fail_snapshot)
    first = v4_scope_migration.apply_v4_scope_reset()

    assert first["status"] == "PARTIAL"
    assert first["snapshot_cleared"] is False
    assert _count("shopping_delivery") == 0
    assert _count("bid_notice_service") == 0
    assert _count("budget") == 1
    assert db.get_setting(v4_scope_migration.MIGRATION_KEY, "") == "1"
    assert db.get_setting(v4_scope_migration.SNAPSHOT_MIGRATION_KEY, "") == ""

    # New v4 target data may arrive before the next restart. Snapshot retry must not
    # run the destructive source-data reset again.
    vnext_store.preserve_raw(
        "shopping_delivery", "new-target-shopping",
        {
            "dlvrReqNo": "NEW", "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
        },
    )

    monkeypatch.setattr(
        result_snapshot_vnext,
        "clear_snapshot",
        lambda: attempts.append("success") or {"cleared": True},
    )
    second = v4_scope_migration.apply_v4_scope_reset()

    assert second["status"] == "COMPLETE"
    assert second["deleted"] == {}
    assert second["snapshot_cleared"] is True
    assert _count("shopping_delivery") == 1
    assert _count("budget") == 1
    assert attempts == ["fail", "success"]

    third = v4_scope_migration.apply_v4_scope_reset()
    assert third["status"] == "SKIPPED"
    assert third["already_applied"] is True
