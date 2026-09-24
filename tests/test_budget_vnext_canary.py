import budget_vnext_canary


def test_budget_canary_requires_business_name_and_stable_identity():
    good=budget_vnext_canary.summarize_budget_rows([{"dbiz_nm":"일반사업","dbiz_cd":"A"}],0)
    bad=budget_vnext_canary.summarize_budget_rows([{"unexpected":"x"}],0)
    assert good["conclusive"] is True
    assert bad["conclusive"] is False


def test_budget_canary_uses_unfiltered_one_page(monkeypatch):
    seen={}
    def fake(year,snapshot,keyword,page=1,size=1000):
        seen.update(year=year,snapshot=snapshot,keyword=keyword,page=page,size=size)
        return [{"dbiz_nm":"사업","dbiz_cd":"1"}],0,"INFO-000","OK"
    monkeypatch.setattr(budget_vnext_canary,"fetch_budget_page",fake)
    report=budget_vnext_canary.run_canary(fiscal_year=2026,snapshot_date="2026-09-17",rows=25)
    assert seen == {"year":2026,"snapshot":"2026-09-17","keyword":"","page":1,"size":25}
    assert report["status"] == "CONCLUSIVE"
    assert report["dataset"] == "budget"
    assert report["source"] == "LOFIN/QWGJK"
    assert report["probe_scope"] == "LOFIN_QWGJK_ONE_PAGE_ONLY"
    assert report["source_collection_completeness_verified"] is False
    assert report["other_budget_sources_verified"] is False
    assert report["unverified_budget_sources"] == [
        "budget_appropriation:AIDFA",
        "education_budget:EDUINFO",
    ]
    assert report["keyword_filter_used"] is False
