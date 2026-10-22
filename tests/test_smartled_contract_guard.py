import datetime as dt
from urllib.parse import urlencode
import pytest
import smartled_contract_vnext as api
import vnext_source_guard as guard


def url(op, **kw):
    params=api.request_params(op, **kw);params['serviceKey']='test-key'
    return api.BASE_URL+'/'+op+'?'+urlencode(params)


def test_narrow_contract_context(monkeypatch):
    monkeypatch.setattr(guard,'_today_kst',lambda: dt.date(2026,10,22))
    with guard.smartled_contract_source_context(collection_date='2026-10-01',max_requests=1):
        good=url(api.SEARCH,start_date='2026-10-01',end_date='2026-10-01',product_name='스마트LED')
        assert guard.require_source_request_context(g2b_url=good)==guard.OPERATIONAL_SMARTLED_CONTRACT
        with pytest.raises(guard.VNextSourceAccessError):guard.require_source_request_context(g2b_url=good)


@pytest.mark.parametrize('bad',[
    lambda:url(api.SEARCH,start_date='2026-10-02',end_date='2026-10-02',product_name='스마트LED'),
    lambda:url(api.SEARCH,start_date='2026-10-01',end_date='2026-10-01'),
    lambda:url(api.DETAIL,unified_no='OTHER'),
    lambda:url(api.SEARCH,start_date='2026-10-01',end_date='2026-10-01',product_name='스마트LED').replace('https://','http://'),
])
def test_reject_broad_or_different_target(monkeypatch,bad):
    monkeypatch.setattr(guard,'_today_kst',lambda: dt.date(2026,10,22))
    with guard.smartled_contract_source_context(collection_date='2026-10-01'):
        with pytest.raises(guard.VNextSourceAccessError):guard.require_source_request_context(g2b_url=bad())


def test_unified_scope_cannot_cross_contract(monkeypatch):
    monkeypatch.setattr(guard,'_today_kst',lambda:dt.date(2026,10,22))
    with guard.smartled_contract_source_context(collection_date='2026-10-01', unified_no='U1'):
        guard.require_source_request_context(g2b_url=url(api.DETAIL,unified_no='U1'))
        with pytest.raises(guard.VNextSourceAccessError):guard.require_source_request_context(g2b_url=url(api.SUMMARY,unified_no='U2'))
    with pytest.raises(guard.VNextSourceAccessError):
        with guard.smartled_contract_source_context(collection_date='2026-09-30'):pass


def test_actual_transport_path_keeps_guard_and_shared_quota(monkeypatch):
    import json
    import vnext_http
    import db
    monkeypatch.setattr(guard,'_today_kst',lambda:dt.date(2026,10,22))
    monkeypatch.setattr(api,'get_service_key',lambda default='':'fixture-key')
    class Reply:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):
            return json.dumps({'response':{'header':{'resultCode':'00'},'body':{'totalCount':1,'items':[{'untyCntrctNo':'U1'}]}}}).encode()
    monkeypatch.setattr(vnext_http.urllib.request,'urlopen',lambda *a,**k:Reply())
    with guard.smartled_contract_source_context(collection_date='2026-10-01',max_requests=1):
        rows,total=api.fetch_page(api.SEARCH,start_date='2026-10-01',end_date='2026-10-01',product_name='스마트LED')
    assert rows==[{'untyCntrctNo':'U1'}] and total==1
    assert vnext_http.api_usage('contract')['kind_count']==1
    assert db.get_setting('smartled_contract_connection_status','')=='OK'
    assert not db.get_setting('g2b_api_connection_status','')
