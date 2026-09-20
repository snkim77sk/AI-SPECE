import db
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
