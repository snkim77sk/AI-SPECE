"""Legacy scope migration is retired: existing source records must survive."""
import db
import result_snapshot_vnext
import v4_scope_migration
import vnext_store


def _count(dataset):
    with db.connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM raw_records WHERE dataset=?", (dataset,)
        ).fetchone()[0] or 0)


def test_retired_v4_scope_reset_preserves_old_and_new_raw_checkpoints(monkeypatch):
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

    datasets = ("shopping_delivery", "bid_notice_service", "budget")
    before = {name: _count(name) for name in datasets}
    markers = {
        key: db.get_setting(key, "")
        for key in (
            v4_scope_migration.MIGRATION_KEY,
            v4_scope_migration.SNAPSHOT_MIGRATION_KEY,
        )
    }
    assert before == {
        "shopping_delivery": 1,
        "bid_notice_service": 1,
        "budget": 1,
    }

    def forbidden_snapshot_clear():
        raise AssertionError("retired scope reset must not delete snapshots")

    monkeypatch.setattr(result_snapshot_vnext, "clear_snapshot", forbidden_snapshot_clear)
    first = v4_scope_migration.apply_v4_scope_reset()
    assert first["status"] == "RETIRED"
    assert first["deleted"] == {}
    assert first["snapshot_cleared"] is False
    assert first["source_api_calls"] == 0
    assert first["already_applied"] is None
    assert {name: _count(name) for name in datasets} == before

    vnext_store.preserve_raw(
        "shopping_delivery", "new-target-shopping",
        {
            "dlvrReqNo": "NEW", "prdctSno": "1",
            "dtilPrdctClsfcNo": "3911160302",
        },
    )
    expected = {**before, "shopping_delivery": 2}

    # A forced retry is NOT permission to erase historical or new data.
    forced = v4_scope_migration.apply_v4_scope_reset(force=True)
    assert forced["status"] == "RETIRED"
    assert forced["force_ignored"] is True
    assert forced["deleted"] == {}
    assert forced["result_snapshot_preserved"] is True
    assert {name: _count(name) for name in datasets} == expected

    repeated = v4_scope_migration.apply_v4_scope_reset()
    assert repeated["status"] == "RETIRED"
    assert {name: _count(name) for name in datasets} == expected
    assert {
        key: db.get_setting(key, "")
        for key in markers
    } == markers
