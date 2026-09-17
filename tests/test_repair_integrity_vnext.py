"""Extra failure-injection and source-latest consistency regressions."""
import json
import pytest
import db
import budget_vnext
import award_projection
import award_vnext
import contract_projection
import analysis_vnext
import classification_vnext
import historical_vnext
import g2b_vnext_pipeline
import vnext_store


def setup_notice_and_award():
    notice={'bidNtceNo':'A','bidNtceOrd':'000','bidNtceNm':'일반 용역'}
    vnext_store.preserve_raw('bid_notice_service','A|000',notice)
    final={'bidNtceNo':'A','bidNtceOrd':'000','bidClsfcNo':'1','rbidNo':'0',
           'bidwinnrNm':'SYNTHETIC','bidwinnrBizno':'1111111111','sucsfbidAmt':'100'}
    key=award_vnext._raw_source_key(final)
    vnext_store.preserve_raw('award_result_service',key,final)
    award_projection.project_final_award_row(final,raw_source_key=key)
    classification_vnext.classify_all()
    return final,key


def setup_contract(key='CR',number='C1'):
    payload={'dcsnCntrctNo':number,'bidNtceNo':'A','bidNtceOrd':'000','thtmCntrctAmt':'100',
             'corpList':'[1^단독^^SYNTHETIC^REP^KR^100^^CONTACT^1111111111]'}
    vnext_store.preserve_raw('contract_service',key,payload)
    contract_projection.project_contract_row(payload,raw_source_key=key)
    return payload


def test_new_final_execution_invalidates_contract_without_contract_refetch():
    final,key=setup_notice_and_award();setup_contract()
    new={**final,'bidClsfcNo':'2'};newkey=award_vnext._raw_source_key(new)
    vnext_store.preserve_raw('award_result_service',newkey,new)
    award_projection.project_final_award_row(new,raw_source_key=newkey)
    with db.connect() as conn:
        assert conn.execute('SELECT contract_no FROM award_results WHERE source_key=?',(key,)).fetchone()['contract_no']==''
        assert not conn.execute("SELECT * FROM lifecycle_links WHERE link_type='RESULTED_IN_CONTRACT' AND confidence>0").fetchall()


def test_changed_final_before_normalizing_hides_old_final_and_contract():
    final,key=setup_notice_and_award();setup_contract()
    vnext_store.preserve_raw('award_result_service',key,{**final,'bidwinnrNm':'CORRECTED'})
    out=analysis_vnext.service_lifecycle_rows()[0]
    assert out['final_vendor']=='' and out['contract_no']==''
    assert out['final_award_stale']


def test_current_multiple_contracts_return_list_without_arbitrary_single():
    setup_notice_and_award(); setup_contract(); setup_contract('CR2','C2')
    out=analysis_vnext.service_lifecycle_rows()[0]
    assert out['contract_no']=='' and out['contract_count']==2 and out['multiple_contracts']
    assert {r['contract_no'] for r in out['contracts']}=={'C1','C2'}


def test_raw_race_is_refused_not_labeled_with_new_digest():
    final,key=setup_notice_and_award()
    vnext_store.preserve_raw('award_result_service',key,{**final,'bidwinnrNm':'NEW'})
    with pytest.raises(ValueError):award_projection.project_final_award_row(final,raw_source_key=key)
    assert analysis_vnext.service_lifecycle_rows()[0]['final_vendor']==''


def test_initial_checkpoint_claim_does_not_overwrite_concurrent_advance(monkeypatch):
    scope='2026:2026-09-01'
    monkeypatch.setattr(budget_vnext,'fetch_budget_page',lambda *a,**k:([{'dbiz_cd':'A'},{'dbiz_cd':'B'}],4,'INFO-000',''))
    budget_vnext.collect_full_budget(2026,'2026-09-01',page_size=2,max_pages=1)
    real=budget_vnext.get_checkpoint
    def raced(*a,**k):
        previous=real(*a,**k)
        with db.connect() as conn:
            conn.execute("UPDATE collection_checkpoints SET page_no=3,fetched_count=4 WHERE dataset='budget' AND scope_key=?",(scope,))
        return previous
    monkeypatch.setattr(budget_vnext,'get_checkpoint',raced)
    with pytest.raises(RuntimeError,match='CONCURRENT_CHECKPOINT_CHANGED'):
        budget_vnext.collect_full_budget(2026,'2026-09-01',page_size=2,max_pages=1)
    assert real('budget',scope)['page_no']==3


def test_bounded_normalization_reports_pending_then_advances():
    for no in ('A','B'):
        payload={'bidNtceNo':no,'bidNtceOrd':'000','opengCorpInfo':'A^1111111111^REP^100^88', 'progrsDivCdNm':'개찰완료'}
        vnext_store.preserve_raw('opening_result_service',no,payload)
    first=award_projection.normalize_dataset('opening_result_service',limit=1)
    second=award_projection.normalize_dataset('opening_result_service',limit=1)
    assert first['pending']==1 and not first['complete']
    assert second['pending']==0 and second['complete'] and second['processed']==1


def test_finalize_stops_classification_while_normalization_pending(monkeypatch):
    monkeypatch.setattr(historical_vnext,'audit_backfill',lambda *a,**k:{'all_complete':True})
    monkeypatch.setattr(historical_vnext.award_projection,'normalize_dataset',lambda *a,**k:{'pending':1,'errors':[]})
    monkeypatch.setattr(historical_vnext.contract_projection,'normalize_contracts',lambda **k:{'pending':0,'errors':[]})
    monkeypatch.setattr(historical_vnext.classification_vnext,'classify_all',lambda **k:(_ for _ in ()).throw(AssertionError('premature classification')))
    out=historical_vnext.finalize_backfill('2026-09-01','2026-09-01',normalize_limit=1)
    assert not out['complete'] and out['classification']['status']=='BLOCKED_NORMALIZATION_PENDING'
