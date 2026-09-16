import pytest

import budget_vnext
from vnext_paging import PaginationInvariantError


def test_source_key_uses_codes_and_ignores_mutable_business_name():
    original = {"fyr":"2026","laf_cd":"A","dept_cd":"B","dbiz_cd":"C","acnt_dv_cd":"D","dbiz_nm":"도로시설 개선사업"}
    assert budget_vnext._source_key(original,2026) == budget_vnext._source_key(dict(original,dbiz_nm="변경"),2026)
    assert budget_vnext._source_key(original,2026) != budget_vnext._source_key(dict(original,dbiz_cd="C2"),2026)


def test_collect_full_budget_uses_empty_keyword_and_preserves_all(monkeypatch):
    calls=[]; preserved=[]; checkpoints=[]
    pages={1:([{"dbiz_nm":"일반","dbiz_cd":"1"},{"dbiz_nm":"도로","dbiz_cd":"2"}],3,"INFO-000",""),
           2:([{"dbiz_nm":"LED","dbiz_cd":"3"}],3,"INFO-000","")}
    monkeypatch.setattr(budget_vnext,"fetch_budget_page",lambda y,s,k,page=1,size=1000: calls.append((k,page,size)) or pages[page])
    monkeypatch.setattr(budget_vnext,"preserve_budget_rows",lambda rows,y,s: preserved.extend(rows) or len(rows))
    monkeypatch.setattr(budget_vnext,"save_checkpoint",lambda dataset,scope,**values: checkpoints.append(values))
    monkeypatch.setattr("vnext_store.get_checkpoint",lambda dataset,scope: None)
    result=budget_vnext.collect_full_budget(2026,"2026-09-15",page_size=2)
    assert [c[0] for c in calls] == ["",""]
    assert [r["dbiz_cd"] for r in preserved] == ["1","2","3"]
    assert result["complete"] is True and result["fetched"] == 3


def test_missing_total_full_budget_page_never_marks_complete(monkeypatch):
    checkpoints=[]
    monkeypatch.setattr(budget_vnext,"fetch_budget_page",lambda y,s,k,page=1,size=1000: ([{"dbiz_cd":str(i)} for i in range(size)],0,"INFO-000",""))
    monkeypatch.setattr(budget_vnext,"preserve_budget_rows",lambda rows,y,s: len(rows))
    monkeypatch.setattr(budget_vnext,"save_checkpoint",lambda d,s,**v: checkpoints.append(v))
    monkeypatch.setattr("vnext_store.get_checkpoint",lambda d,s: None)
    result=budget_vnext.collect_full_budget(2026,"2026-09-15",page_size=2,max_pages=1)
    assert result["source_total"] == 0 and result["complete"] is False
    assert checkpoints[-1]["status"] == "RUNNING" and checkpoints[-1]["page_no"] == 2


def test_known_total_empty_page_before_total_is_failure(monkeypatch):
    checkpoints=[]
    pages={1:([{"dbiz_cd":"1"}],3,"INFO-000",""),2:([],3,"INFO-000","")}
    monkeypatch.setattr(budget_vnext,"fetch_budget_page",lambda y,s,k,page=1,size=1000: pages[page])
    monkeypatch.setattr(budget_vnext,"preserve_budget_rows",lambda rows,y,s: len(rows))
    monkeypatch.setattr(budget_vnext,"save_checkpoint",lambda d,s,**v: checkpoints.append(v))
    monkeypatch.setattr("vnext_store.get_checkpoint",lambda d,s: None)
    with pytest.raises(PaginationInvariantError):
        budget_vnext.collect_full_budget(2026,"2026-09-15",page_size=2)
    assert checkpoints[-1]["status"] == "FAILED"
    assert checkpoints[-1]["fetched_count"] == 1


def test_repeated_budget_page_is_failure(monkeypatch):
    checkpoints=[]
    row={"dbiz_cd":"1"}
    monkeypatch.setattr(budget_vnext,"fetch_budget_page",lambda y,s,k,page=1,size=1000: ([row],0,"INFO-000",""))
    monkeypatch.setattr(budget_vnext,"preserve_budget_rows",lambda rows,y,s: len(rows))
    monkeypatch.setattr(budget_vnext,"save_checkpoint",lambda d,s,**v: checkpoints.append(v))
    monkeypatch.setattr("vnext_store.get_checkpoint",lambda d,s: None)
    with pytest.raises(PaginationInvariantError,match="repeated"):
        budget_vnext.collect_full_budget(2026,"2026-09-15",page_size=1)
    assert checkpoints[-1]["status"] == "FAILED"
    assert checkpoints[-1]["fetched_count"] == 1


def test_resume_page_size_change_is_rejected(monkeypatch):
    checkpoint={"status":"RUNNING","page_no":2,"page_size":2,"fetched_count":2,"saved_count":2,"source_total":0,"last_page_fingerprint":"x"}
    monkeypatch.setattr("vnext_store.get_checkpoint",lambda d,s: checkpoint)
    with pytest.raises(PaginationInvariantError,match="page_size mismatch"):
        budget_vnext.collect_full_budget(2026,"2026-09-15",page_size=3)


def test_checkpoint_failure_keeps_pre_page_counters_for_safe_resume(monkeypatch):
    events=[]; calls={"n":0}
    monkeypatch.setattr(budget_vnext,"fetch_budget_page",lambda *a,**k: ([{"dbiz_cd":"1"}],9,"INFO-000",""))
    monkeypatch.setattr(budget_vnext,"preserve_budget_rows",lambda rows,y,s: len(rows))
    monkeypatch.setattr("vnext_store.get_checkpoint",lambda d,s: None)
    def save(d,s,**v):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("checkpoint write failed")
        events.append(dict(v))
    monkeypatch.setattr(budget_vnext,"save_checkpoint",save)
    with pytest.raises(RuntimeError,match="checkpoint write failed"):
        budget_vnext.collect_full_budget(2026,"2026-09-15",page_size=1)
    assert events[-1]["status"] == "FAILED"
    assert events[-1]["page_no"] == 1
    assert events[-1]["fetched_count"] == 0
    assert events[-1]["saved_count"] == 0
