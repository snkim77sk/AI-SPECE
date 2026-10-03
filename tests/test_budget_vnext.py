import datetime as dt
import db
import hashlib
import json
import vnext_store
import budget_vnext


def test_source_key_uses_codes_and_ignores_mutable_business_name():
    original = {
        "fyr": "2026", "laf_cd": "A", "dept_cd": "B", "dbiz_cd": "C",
        "acnt_dv_cd": "D", "dbiz_nm": "도로시설 개선사업",
    }
    renamed = dict(original, dbiz_nm="도로시설 개선사업 변경")
    different_code = dict(original, dbiz_cd="C2")
    assert budget_vnext._source_key(original, 2026) == budget_vnext._source_key(renamed, 2026)
    assert budget_vnext._source_key(original, 2026) != budget_vnext._source_key(different_code, 2026)


def test_source_key_uses_name_only_as_fallback_when_business_code_missing():
    one = {"fyr": "2026", "laf_cd": "A", "dept_cd": "B", "dbiz_cd": "", "acnt_dv_cd": "D", "dbiz_nm": "사업1"}
    two = dict(one, dbiz_nm="사업2")
    assert budget_vnext._source_key(one, 2026) != budget_vnext._source_key(two, 2026)


def test_collect_full_budget_uses_empty_keyword_and_preserves_all(monkeypatch):
    calls=[]
    pages={1:([{'dbiz_nm':'일반 행정사업','dbiz_cd':'1'}, {'dbiz_nm':'도로시설 개선','dbiz_cd':'2'}],3,'INFO-000',''),
           2:([{'dbiz_nm':'LED 가로등 교체','dbiz_cd':'3'}],3,'INFO-000','')}
    def fetch(year,snapshot,keyword,page=1,size=1000):
        calls.append((keyword,page,size)); return pages[page]
    monkeypatch.setattr(budget_vnext,'fetch_budget_page',fetch)
    result=budget_vnext.collect_full_budget(2026,'2026-09-15',page_size=2)
    assert [x[0] for x in calls]==['','']
    assert result['fetched']==result['saved']==3 and result['complete']
    with db.connect() as conn:
        payloads=[json.loads(x['payload_json']) for x in conn.execute("SELECT payload_json FROM raw_records WHERE dataset='budget' ORDER BY id")]
    assert [x['dbiz_cd'] for x in payloads]==['1','2','3']
    assert vnext_store.get_checkpoint('budget','2026:2026-09-15')['status']=='COMPLETE'


def test_canary_max_pages_keeps_partial_budget_resumable(monkeypatch):
    calls=[]
    def fetch(year,snapshot,keyword,page=1,size=1000):
        calls.append((keyword,page)); return [{'dbiz_cd':'1'}],9999,'INFO-000',''
    monkeypatch.setattr(budget_vnext,'fetch_budget_page',fetch)
    result=budget_vnext.collect_full_budget(2026,'2026-09-15',page_size=1,max_pages=1)
    assert calls==[('',1)] and result['fetched']==1 and not result['complete']
    cp=vnext_store.get_checkpoint('budget','2026:2026-09-15')
    assert cp['status']=='RUNNING' and cp['page_no']==2


def test_missing_total_full_budget_page_never_marks_complete(monkeypatch):
    monkeypatch.setattr(budget_vnext,'fetch_budget_page',lambda *a,**k: ([{'dbiz_cd':'1'},{'dbiz_cd':'2'}],None,'INFO-000',''))
    result=budget_vnext.collect_full_budget(2026,'2026-09-15',page_size=2,max_pages=1)
    assert result['fetched']==2 and result['source_total'] is None and not result['complete']
    cp=vnext_store.get_checkpoint('budget','2026:2026-09-15')
    assert cp['status']=='RUNNING' and cp['page_no']==2


def test_oversized_budget_page_size_uses_lofin_max_for_completion(monkeypatch):
    seen=[]
    def fetch(year,snapshot,keyword,page=1,size=1000):
        seen.append(size); return [{'dbiz_cd':str(i)} for i in range(size)],None,'INFO-000',''
    monkeypatch.setattr(budget_vnext,'fetch_budget_page',fetch)
    result=budget_vnext.collect_full_budget(2026,'2026-09-15',page_size=5000,max_pages=1,resume=False)
    assert seen==[1000] and result['fetched']==1000 and not result['complete']
    assert vnext_store.get_checkpoint('budget','2026:2026-09-15')['page_no']==2


def test_collect_full_budget_region_partition_uses_separate_checkpoint_scope(monkeypatch):
    calls = []

    def fetch(year, snapshot, keyword, page=1, size=1000, *, region_code="", **kwargs):
        calls.append((year, snapshot, keyword, region_code, page, size))
        return [
            {
                "fyr": "2026", "exe_ymd": "20260915", "wa_laf_cd": "4100000",
                "laf_cd": "4111000", "dept_cd": "D1", "dbiz_cd": "P1",
            }
        ], 1, "INFO-000", ""

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", fetch)
    result = budget_vnext.collect_full_budget(
        2026, "2026-09-15", region_code="4100000", resume=False
    )

    assert result["complete"] is True
    assert calls == [(2026, "2026-09-15", "", "4100000", 1, 1000)]
    assert result["scope"] == "2026:2026-09-15:4100000"
    assert vnext_store.get_checkpoint(
        "budget", "2026:2026-09-15:4100000"
    )["status"] == "COMPLETE"
    # Existing nationwide scope key remains unchanged/backward-compatible.
    assert vnext_store.get_checkpoint("budget", "2026:2026-09-15") is None


def test_region_partition_fails_closed_when_source_row_reports_other_region(monkeypatch):
    def fetch(year, snapshot, keyword, page=1, size=1000, *, region_code="", **kwargs):
        return [
            {
                "fyr": "2026", "exe_ymd": "20260915", "wa_laf_cd": "1100000",
                "laf_cd": "1111000", "dept_cd": "D1", "dbiz_cd": "P1",
            }
        ], 1, "INFO-000", ""

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", fetch)
    result = budget_vnext.collect_full_budget(
        2026, "2026-09-15", region_code="4100000", resume=False
    )

    assert result["complete"] is False
    assert result["reason"] == "BUDGET_REGION_MISMATCH"
    cp = vnext_store.get_checkpoint("budget", "2026:2026-09-15:4100000")
    assert cp["status"] == "INCOMPLETE"


def test_explicit_region_partition_plan_collects_each_region_without_completeness_overclaim(monkeypatch):
    calls = []

    def fake_collect(year, snapshot, *, region_code="", **kwargs):
        calls.append(region_code)
        return {
            "scope": f"{year}:{snapshot}:{region_code}",
            "complete": True,
            "fetched": 10,
            "saved": 10,
        }

    monkeypatch.setattr(budget_vnext, "collect_full_budget", fake_collect)
    result = budget_vnext.collect_budget_region_partitions(
        2026,
        "2026-09-15",
        ["4100000", "1100000", "4100000"],
    )

    assert calls == ["4100000", "1100000"]
    assert result["region_codes"] == ["4100000", "1100000"]
    assert result["complete_for_planned_regions"] is True
    assert result["partition_scope"] == "EXPLICIT_REGION_LIST"
    assert result["source_collection_completeness_verified"] is False


def test_nationwide_then_region_partition_reuses_same_qwgjk_raw_identity(monkeypatch):
    row = {
        "fyr": "2026",
        "exe_ymd": "20260919",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "D1",
        "dbiz_cd": "P1",
        "acnt_dv_cd": "A1",
        "dbiz_nm": "LED 가로등 교체",
    }
    calls = []

    def fetch(year, snapshot, keyword, page=1, size=1000, *, region_code="", **kwargs):
        calls.append(region_code)
        return [dict(row)], 1, "INFO-000", ""

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", fetch)

    nationwide = budget_vnext.collect_full_budget(
        2026, "2026-09-19", resume=False
    )
    regional = budget_vnext.collect_full_budget(
        2026, "2026-09-19", region_code="4100000", resume=False
    )

    assert nationwide["complete"] is True
    assert regional["complete"] is True
    assert calls == ["", "4100000"]
    with db.connect() as conn:
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM raw_records WHERE dataset='budget'"
        ).fetchone()[0]
        revision_count = conn.execute(
            "SELECT COUNT(*) FROM raw_record_revisions WHERE dataset='budget'"
        ).fetchone()[0]
    assert raw_count == 1
    assert revision_count == 1
    assert vnext_store.get_checkpoint(
        "budget", "2026:2026-09-19"
    )["status"] == "COMPLETE"
    assert vnext_store.get_checkpoint(
        "budget", "2026:2026-09-19:4100000"
    )["status"] == "COMPLETE"


def test_fully_coded_qwgjk_source_key_is_stable_across_snapshot_dates():
    row = {
        "fyr": "2026",
        "exe_ymd": "20260919",
        "wa_laf_cd": "4100000",
        "wa_laf_hg_nm": "경기",
        "laf_cd": "4111000",
        "laf_hg_nm": "수원시",
        "dept_cd": "D1",
        "dept_nm": "도로과",
        "dbiz_cd": "P1",
        "dbiz_nm": "LED 가로등 교체",
        "acnt_dv_cd": "A1",
        "acnt_dv_nm": "일반회계",
    }
    stable_parts = [
        "2026", "4100000", "4111000", "D1", "P1", "A1"
    ]
    expected = hashlib.sha1("|".join(stable_parts).encode("utf-8")).hexdigest()

    assert budget_vnext._source_key(row, 2026, "2026-09-19") == expected
    assert budget_vnext._source_key(row, 2026, "2026-09-20") == expected


def test_missing_department_code_uses_department_name_to_avoid_raw_collision():
    base = {
        "fyr": "2026",
        "exe_ymd": "20260919",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "",
        "dbiz_cd": "P1",
        "dbiz_nm": "LED 가로등 교체",
        "acnt_dv_cd": "A1",
    }
    one = dict(base, dept_nm="도로과")
    two = dict(base, dept_nm="시설과")

    assert budget_vnext._source_key(
        one, 2026, "2026-09-19"
    ) != budget_vnext._source_key(
        two, 2026, "2026-09-19"
    )


def test_missing_account_code_uses_account_name_to_avoid_raw_collision():
    base = {
        "fyr": "2026",
        "exe_ymd": "20260919",
        "wa_laf_cd": "4100000",
        "laf_cd": "4111000",
        "dept_cd": "D1",
        "dbiz_cd": "P1",
        "dbiz_nm": "LED 가로등 교체",
        "acnt_dv_cd": "",
    }
    one = dict(base, acnt_dv_nm="일반회계")
    two = dict(base, acnt_dv_nm="특별회계")

    assert budget_vnext._source_key(
        one, 2026, "2026-09-19"
    ) != budget_vnext._source_key(
        two, 2026, "2026-09-19"
    )


def test_present_codes_ignore_mutable_dimension_names():
    original = {
        "fyr": "2026",
        "exe_ymd": "20260919",
        "wa_laf_cd": "4100000",
        "wa_laf_hg_nm": "경기",
        "laf_cd": "4111000",
        "laf_hg_nm": "수원시",
        "dept_cd": "D1",
        "dept_nm": "도로과",
        "dbiz_cd": "P1",
        "dbiz_nm": "LED 가로등 교체",
        "acnt_dv_cd": "A1",
        "acnt_dv_nm": "일반회계",
    }
    renamed = dict(
        original,
        wa_laf_hg_nm="경기도",
        laf_hg_nm="수원특례시",
        dept_nm="도로관리과",
        dbiz_nm="LED 가로등 교체사업 변경",
        acnt_dv_nm="일반회계 명칭변경",
    )

    assert budget_vnext._source_key(
        original, 2026, "2026-09-19"
    ) == budget_vnext._source_key(
        renamed, 2026, "2026-09-19"
    )


def test_collection_preserves_same_business_code_from_two_name_only_departments(monkeypatch):
    rows = [
        {
            "fyr": "2026",
            "exe_ymd": "20260919",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "dept_cd": "",
            "dept_nm": "도로과",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1",
        },
        {
            "fyr": "2026",
            "exe_ymd": "20260919",
            "wa_laf_cd": "4100000",
            "laf_cd": "4111000",
            "dept_cd": "",
            "dept_nm": "시설과",
            "dbiz_cd": "P1",
            "dbiz_nm": "LED 가로등 교체",
            "acnt_dv_cd": "A1",
        },
    ]

    monkeypatch.setattr(
        budget_vnext,
        "fetch_budget_page",
        lambda *args, **kwargs: (rows, 2, "INFO-000", ""),
    )

    result = budget_vnext.collect_full_budget(
        2026, "2026-09-19", page_size=100, resume=False
    )

    assert result["complete"] is True
    assert result["saved"] == 2
    with db.connect() as conn:
        raw = conn.execute(
            "SELECT source_key,payload_json FROM raw_records "
            "WHERE dataset='budget' ORDER BY source_key"
        ).fetchall()
    assert len(raw) == 2
    assert {
        json.loads(row["payload_json"])["dept_nm"] for row in raw
    } == {"도로과", "시설과"}


def test_historical_qwgjk_compacts_receipts_after_complete(monkeypatch):
    import budget_pg_collection
    import budget_pg_store
    import budget_storage

    compacted = []
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_pg_collection,
        "collect_pages",
        lambda **kwargs: {
            "complete": True,
            "status": "COMPLETE",
            "scope": kwargs["scope"],
        },
    )
    monkeypatch.setattr(
        budget_pg_store,
        "clear_collection_receipts",
        lambda dataset, scope: (
            compacted.append((dataset, scope))
            or {
                "deleted_collection_items": 123,
                "deleted_collection_pages": 4,
            }
        ),
    )

    result = budget_vnext.collect_full_budget(
        2026,
        "2026-01-01",
        max_pages=4,
        resume=True,
        advance_current=False,
    )

    assert compacted == [
        ("budget", "history:2026:2026-01-01")
    ]
    assert result["historical_only"] is True
    assert result["receipt_compaction"] == {
        "deleted_collection_items": 123,
        "deleted_collection_pages": 4,
    }


def test_historical_qwgjk_compaction_failure_is_fail_soft(monkeypatch):
    import budget_pg_collection
    import budget_pg_store
    import budget_storage

    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_pg_collection,
        "collect_pages",
        lambda **kwargs: {
            "complete": True,
            "status": "COMPLETE",
            "scope": kwargs["scope"],
        },
    )

    def broken_compaction(*args, **kwargs):
        raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(
        budget_pg_store,
        "clear_collection_receipts",
        broken_compaction,
    )

    result = budget_vnext.collect_full_budget(
        2026,
        "2026-01-01",
        max_pages=4,
        resume=True,
        advance_current=False,
    )

    assert result["complete"] is True
    assert result["status"] == "COMPLETE"
    assert result["historical_only"] is True
    assert result["receipt_compaction"] == {
        "status": "DEFERRED",
        "error": "RuntimeError",
        "deleted_collection_items": 0,
        "deleted_collection_pages": 0,
    }


def test_historical_qwgjk_uses_separate_checkpoint_namespace(monkeypatch):
    import budget_pg_collection
    import budget_storage

    captured = {}
    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_pg_collection,
        "collect_pages",
        lambda **kwargs: captured.update(kwargs) or {
            "complete": False,
            "status": "RUNNING",
        },
    )

    result = budget_vnext.collect_full_budget(
        2026,
        "2026-01-01",
        max_pages=1,
        resume=True,
        advance_current=False,
    )

    assert result["status"] == "RUNNING"
    assert captured["scope"] == "history:2026:2026-01-01"
    assert captured["checkpoint_contract"] == (
        budget_vnext.HISTORY_CHECKPOINT_CONTRACT
    )
    assert captured["advance_current"] is False


def test_current_pending_snapshot_ignores_history_checkpoint(monkeypatch):
    import budget_pg_store
    import budget_storage

    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_pg_store,
        "list_checkpoints",
        lambda dataset: [
            {
                "scope_key": "history:2026:2026-01-01",
                "status": "RUNNING",
            },
            {
                "scope_key": "2026:2026-10-01",
                "status": "RUNNING",
            },
        ],
    )

    pending = budget_vnext.pending_nationwide_snapshot_date(
        today=dt.date(2026, 10, 2)
    )
    assert pending == dt.date(2026, 10, 1)


def test_history_date_scanner_rolls_forward_after_retention_window(monkeypatch):
    import budget_pg_store
    import budget_storage

    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_pg_store,
        "list_checkpoints",
        lambda dataset: [],
    )

    assert budget_vnext.next_historical_snapshot_date(
        today=dt.date(2027, 1, 2),
        retention_days=365,
    ) == dt.date(2026, 1, 2)


def test_history_date_scanner_accepts_current_or_history_complete_markers(monkeypatch):
    import budget_pg_store
    import budget_storage

    monkeypatch.setattr(budget_storage, "using_postgres", lambda: True)
    monkeypatch.setattr(
        budget_pg_store,
        "list_checkpoints",
        lambda dataset: [
            {
                "scope_key": "history:2026:2026-01-01",
                "status": "COMPLETE",
            },
            {
                "scope_key": "2026:2026-01-02",
                "status": "COMPLETE",
            },
        ],
    )

    assert budget_vnext.next_historical_snapshot_date(
        today=dt.date(2026, 1, 4)
    ) == dt.date(2026, 1, 3)


def test_qwgjk_matching_execution_date_can_complete(monkeypatch):
    row = {
        "fyr": "2026",
        "exe_ymd": "20260919",
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

    result = budget_vnext.collect_full_budget(
        2026, "2026-09-19", resume=False
    )

    assert result["complete"] is True
    assert result["saved"] == 1
    assert vnext_store.get_checkpoint(
        "budget", "2026:2026-09-19"
    )["status"] == "COMPLETE"


def test_qwgjk_response_execution_date_mismatch_preserves_raw_evidence_but_fails_scope(monkeypatch):
    row = {
        "fyr": "2026",
        "exe_ymd": "20260918",
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

    result = budget_vnext.collect_full_budget(
        2026, "2026-09-19", resume=False
    )

    assert result["complete"] is False
    assert result["reason"] == "BUDGET_SNAPSHOT_DATE_MISMATCH"
    assert result["saved"] == 0
    checkpoint = vnext_store.get_checkpoint("budget", "2026:2026-09-19")
    assert checkpoint["status"] == "INCOMPLETE"
    with db.connect() as conn:
        raw = conn.execute(
            "SELECT payload_json FROM raw_records WHERE dataset='budget'"
        ).fetchall()
        receipt_items = conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_items
               WHERE dataset='budget' AND scope_key='2026:2026-09-19'"""
        ).fetchone()[0]
        receipt_pages = conn.execute(
            """SELECT COUNT(*) FROM vnext_collection_pages
               WHERE dataset='budget' AND scope_key='2026:2026-09-19'"""
        ).fetchone()[0]
    assert len(raw) == 1
    assert json.loads(raw[0]["payload_json"])["exe_ymd"] == "20260918"
    assert receipt_items == 0
    assert receipt_pages == 0


def test_current_qwgjk_snapshot_date_is_d_minus_one():
    assert budget_vnext.current_snapshot_date(
        today=dt.date(2026, 10, 3)
    ) == dt.date(2026, 10, 2)


def test_zero_complete_current_qwgjk_replays_once_on_new_kst_day(monkeypatch):
    day = "2026-10-03"
    calls = []

    def empty_fetch(year, snapshot, keyword, page=1, size=1000):
        calls.append(("empty", page))
        return [], 0, "INFO-000", ""

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", empty_fetch)
    first = budget_vnext.collect_full_budget(
        2026,
        day,
        resume=False,
    )
    assert first["complete"] is True
    assert first["fetched"] == 0
    assert calls == [("empty", 1)]

    # Pin the original 0-row COMPLETE marker to the source day in KST.
    with db.connect() as conn:
        conn.execute(
            """UPDATE collection_checkpoints
               SET updated_at=?
               WHERE dataset='budget' AND scope_key=?""",
            ("2026-10-03T00:00:00+00:00", f"2026:{day}"),
        )

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError(
            "same-KST-day zero COMPLETE must not refetch"
        )

    monkeypatch.setattr(budget_vnext, "fetch_budget_page", forbidden_fetch)
    same_day = budget_vnext.collect_full_budget(
        2026,
        day,
        resume=True,
        refresh_date="2026-10-03",
    )
    assert same_day["complete"] is True
    assert same_day["fetched"] == 0

    row = {
        "fyr": "2026",
        "exe_ymd": "20261003",
        "wa_laf_cd": "2800000",
        "laf_cd": "2817700",
        "dept_cd": "D1",
        "dbiz_cd": "P1",
        "dbiz_nm": "LED 가로등 교체",
        "acnt_dv_cd": "A1",
        "bdg_cash_amt": "1000",
        "ep_amt": "100",
    }

    def recovered_fetch(year, snapshot, keyword, page=1, size=1000):
        calls.append(("recovered", page))
        return [row], 1, "INFO-000", ""

    monkeypatch.setattr(
        budget_vnext,
        "fetch_budget_page",
        recovered_fetch,
    )
    replayed = budget_vnext.collect_full_budget(
        2026,
        day,
        resume=True,
        refresh_date="2026-10-04",
    )

    assert replayed["complete"] is True
    assert replayed["fetched"] == 1
    assert replayed["saved"] == 1
    assert calls == [("empty", 1), ("recovered", 1)]
    cp = vnext_store.get_checkpoint("budget", f"2026:{day}")
    assert cp["status"] == "COMPLETE"
    assert int(cp["fetched_count"]) == 1

