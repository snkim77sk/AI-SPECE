import datetime as dt

import budget_read_vnext
import budget_pg_store
import budget_projection_vnext
import budget_reorganize_vnext
import db
import vnext_store


def _seed():
    vnext_store.preserve_raw(
        "budget", "q1",
        {
            "fyr": "2026", "exe_ymd": "20260919",
            "wa_laf_cd": "4100000", "laf_cd": "4111000", "laf_hg_nm": "수원시",
            "dept_cd": "D1", "dbiz_cd": "P1", "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "1", "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "1000", "ep_amt": "100",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )
    vnext_store.preserve_raw(
        "budget_appropriation", "a1",
        {
            "fyr": "2026", "wa_laf_cd": "4100000", "laf_cd": "4111000",
            "laf_hg_nm": "수원시", "fld_cd": "F1", "fld_nm": "교통및물류",
            "sect_cd": "S1", "sect_nm": "도로", "acnt_dv_nm": "일반회계",
            "biz_bdg_tott_amt": "5000",
        },
        source_system="지방재정365 AIDFA",
        source_operation="AIDFA_FULL_V1",
        source_date="2026",
    )
    vnext_store.preserve_raw(
        "education_budget", "e1",
        {
            "YMQ": "2026", "officeCode": "J10", "교육청명": "경기도교육청",
            "projectCode": "E1", "사업명": "학교 LED 조명 개선",
            "예산액": "3000", "집행액": "500",
        },
        source_system="지방교육재정알리미(typeA)",
        source_operation="EDUINFO_FULL_RAW_V1:typeA",
        source_date="2026",
    )


def _raw_counts():
    with db.connect() as conn:
        return {
            dataset: conn.execute(
                "SELECT COUNT(*) FROM raw_records WHERE dataset=?", (dataset,)
            ).fetchone()[0]
            for dataset in budget_reorganize_vnext.BUDGET_DATASETS
        }


def _revision_counts():
    with db.connect() as conn:
        return {
            dataset: conn.execute(
                "SELECT COUNT(*) FROM raw_record_revisions WHERE dataset=?", (dataset,)
            ).fetchone()[0]
            for dataset in budget_reorganize_vnext.BUDGET_DATASETS
        }


def test_offline_budget_reorganization_projects_and_classifies_without_new_raw():
    _seed()
    before = _raw_counts()
    revisions_before = _revision_counts()

    result = budget_reorganize_vnext.reorganize_existing_budget_raw(
        fiscal_year=2026,
    )

    assert result["source_traffic"] is False
    assert result["mode"] == "EXISTING_RAW_REORGANIZATION_ONLY"
    assert result["complete"] is True
    assert result["raw_counts_unchanged"] is True
    assert result["revision_counts_unchanged"] is True
    assert before == _raw_counts()
    assert revisions_before == _revision_counts()
    assert result["revision_counts_before"] == revisions_before
    assert result["revision_counts_after"] == revisions_before
    assert result["projection"]["projected"] == 3
    assert all(
        row["projection_complete_for_current_raw"]
        for row in result["projection_coverage"]
    )
    assert result["classification_current_for_organized_rows"] is True
    assert result["current_projects"] == 3


def test_changed_budget_raw_is_reorganized_locally_without_refetch():
    _seed()
    first = budget_reorganize_vnext.reorganize_existing_budget_raw(fiscal_year=2026)
    assert first["complete"] is True

    # Legitimate later RAW revision under the same identity; no source call involved.
    vnext_store.preserve_raw(
        "budget", "q1",
        {
            "fyr": "2026", "exe_ymd": "20260919",
            "wa_laf_cd": "4100000", "laf_cd": "4111000", "laf_hg_nm": "수원시",
            "dept_cd": "D1", "dbiz_cd": "P1", "dbiz_nm": "LED 가로등 교체 확대",
            "acnt_dv_cd": "1", "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "2000", "ep_amt": "400",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )

    result = budget_reorganize_vnext.reorganize_existing_budget_raw(fiscal_year=2026)

    assert result["complete"] is True
    with db.connect() as conn:
        projection = conn.execute(
            """SELECT project_name,budget_amount,executed_amount,remaining_amount
               FROM vnext_budget_projection
               WHERE raw_dataset='budget' AND raw_source_key='q1'"""
        ).fetchone()
        classification = conn.execute(
            """SELECT c.primary_category,c.source_payload_sha256,r.payload_sha256
               FROM classifications c
               JOIN raw_records r
                 ON r.dataset=c.entity_type AND r.source_key=c.entity_key
               WHERE c.entity_type='budget' AND c.entity_key='q1'"""
        ).fetchone()
    assert projection["project_name"] == "LED 가로등 교체 확대"
    assert projection["budget_amount"] == 2000
    assert projection["executed_amount"] == 400
    assert projection["remaining_amount"] == 1600
    assert classification["primary_category"] == "LIGHTING"
    assert classification["source_payload_sha256"] == classification["payload_sha256"]


def test_empty_local_budget_storage_does_not_claim_source_completeness():
    result = budget_reorganize_vnext.reorganize_existing_budget_raw(fiscal_year=2026)

    assert result["complete"] is True
    assert sum(result["raw_counts_before"].values()) == 0
    assert result["source_collection_completeness_verified"] is False
    assert result["source_collection_completeness_reason"] == "NOT_EVALUATED_BY_OFFLINE_REORGANIZATION"

def test_offline_reorganization_updates_target_and_prebid_read_model_without_refetch():
    _seed()

    first = budget_reorganize_vnext.reorganize_existing_budget_raw(
        fiscal_year=2026,
    )
    assert first["complete"] is True
    first_payload = budget_read_vnext.budget_read_model(fiscal_year=2026)

    assert {row["raw_source_key"] for row in first_payload["target_rows"]} == {
        "q1", "e1"
    }
    assert {row["raw_source_key"] for row in first_payload["prebid_rows"]} == {
        "q1", "e1"
    }
    assert all(
        row["source_layer"] in {"DETAIL_EXECUTION", "EDUCATION"}
        for row in first_payload["prebid_rows"]
    )

    # A later stored RAW revision can change sales eligibility locally. No source
    # request is needed: reorganization must update projection/classification and
    # the prebid read model from the already-stored revision.
    vnext_store.preserve_raw(
        "budget", "q1",
        {
            "fyr": "2026", "exe_ymd": "20260919",
            "wa_laf_cd": "4100000", "laf_cd": "4111000", "laf_hg_nm": "수원시",
            "dept_cd": "D1", "dbiz_cd": "P1", "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "1", "acnt_dv_nm": "일반회계",
            "bdg_cash_amt": "1000", "ep_amt": "1000",
        },
        source_system="지방재정365 QWGJK",
        source_operation="QWGJK_FULL_V2_SNAPSHOT",
        source_date="2026-09-19",
    )

    second = budget_reorganize_vnext.reorganize_existing_budget_raw(
        fiscal_year=2026,
    )
    assert second["complete"] is True
    assert second["source_traffic"] is False
    second_payload = budget_read_vnext.budget_read_model(fiscal_year=2026)

    assert {row["raw_source_key"] for row in second_payload["target_rows"]} == {
        "q1", "e1"
    }
    assert {row["raw_source_key"] for row in second_payload["prebid_rows"]} == {
        "e1"
    }
    q1 = next(
        row for row in second_payload["target_rows"]
        if row["raw_source_key"] == "q1"
    )
    assert q1["remaining_amount"] == 0




def test_postgres_retention_keeps_screen_current_and_prunes_sqlite_read_model(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("G2B_TEST_MODE", "1")
    monkeypatch.setenv("G2B_BUDGET_STORAGE", "postgresql")
    monkeypatch.setenv(
        "G2B_BUDGET_DATABASE_URL",
        f"sqlite:///{tmp_path / 'budget-current.sqlite3'}",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRESQL_URL", raising=False)
    budget_pg_store.reset_engine_cache()

    def payload(code, name):
        return {
            "fyr": "2026",
            "exe_ymd": "20261001",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "laf_hg_nm": "수원시",
            "dept_cd": "D1",
            "dbiz_cd": code,
            "dbiz_nm": name,
            "acnt_dv_cd": "A1",
            "bdg_cash_amt": "1000",
            "ep_amt": "100",
        }

    try:
        active = budget_pg_store.preserve_observation(
            "budget", "active", payload("ACTIVE", "LED 가로등 교체"),
            source_system="지방재정365", source_operation="QWGJK_FULL_V2_SNAPSHOT",
            source_date="2026-10-01",
        )
        stale = budget_pg_store.preserve_observation(
            "budget", "stale", payload("STALE", "LED 보안등 교체"),
            source_system="지방재정365", source_operation="QWGJK_FULL_V2_SNAPSHOT",
            source_date="2026-10-01",
        )

        organized = budget_reorganize_vnext.reorganize_existing_budget_raw(
            fiscal_year=2026
        )
        assert organized["complete"] is True

        with db.connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM vnext_budget_projection WHERE raw_dataset='budget'"
            ).fetchone()[0] == 2
            assert conn.execute(
                "SELECT COUNT(*) FROM classifications WHERE entity_type='budget'"
            ).fetchone()[0] == 2

        engine, tables = budget_pg_store._engine_and_tables()
        old = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=500)
        ).isoformat()
        with engine.begin() as conn:
            conn.execute(
                tables["observations"].update()
                .where(tables["observations"].c.id == stale["observation_id"])
                .values(observed_at=old)
            )
            conn.execute(
                tables["states"].update()
                .where(tables["states"].c.record_key == "stale")
                .values(last_seen_at=old)
            )

        purged = budget_pg_store.purge_history(365)
        assert purged["expired_current_records"] == 1

        # The user-facing read model must already follow PostgreSQL current state,
        # even before physical SQLite cleanup runs.
        payload_now = budget_read_vnext.budget_read_model(
            fiscal_year=2026,
            categories=["LIGHTING"],
        )
        assert {row["raw_source_key"] for row in payload_now["current_rows"]} == {
            "active"
        }
        assert {row["raw_source_key"] for row in payload_now["target_rows"]} == {
            "active"
        }
        assert {row["raw_source_key"] for row in payload_now["prebid_rows"]} == {
            "active"
        }

        pruned = budget_projection_vnext.prune_stale_budget_read_model(
            datasets=["budget"]
        )
        assert pruned["deleted_projection_rows"] == 1
        assert pruned["deleted_classification_rows"] == 1
        assert pruned["current_raw_rows"] == 1

        with db.connect() as conn:
            projection = conn.execute(
                """SELECT raw_source_key FROM vnext_budget_projection
                   WHERE raw_dataset='budget'"""
            ).fetchall()
            classifications = conn.execute(
                """SELECT entity_key FROM classifications
                   WHERE entity_type='budget'"""
            ).fetchall()
        assert [row["raw_source_key"] for row in projection] == ["active"]
        assert [row["entity_key"] for row in classifications] == ["active"]
        assert active["observation_id"]
    finally:
        budget_pg_store.reset_engine_cache()
