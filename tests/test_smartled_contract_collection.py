import contextlib
import datetime as dt
import json
import pytest
import db
import smartled_contract_collection as collect
import smartled_contract_vnext as api
import smartled_contract_store_vnext as store

DAY = dt.date(2026,10,21)
HEADER = {'untyCntrctNo':'U1','cntrctCnclsDate':'2026-10-01','cntrctNm':'스마트LED','dcsnCntrctNo':'R1','thtmCntrctAmt':'1000'}
DETAIL = {'untyCntrctNo':'U1','dcsnCntrctNo':'R1','prdctClsfcNo':'39119802','krnPrdctNm':'스마트LED 실내등','prdctAmt':'100'}

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    store.ensure_schema()
    monkeypatch.setattr(collect,'smartled_contract_source_context',lambda **kw: contextlib.nullcontext())


def test_latest_then_october_backfill_and_idempotent_queue(monkeypatch):
    calls=[]
    def fetch(op,**kw):
        calls.append(kw['start_date'])
        return [HEADER],1
    monkeypatch.setattr(api,'fetch_page',fetch)
    collect._scan('search_cursor',api.SEARCH,DAY)
    collect._scan('search_cursor',api.SEARCH,DAY)
    assert calls==['2026-10-21','2026-10-01']
    assert store.queue_status()['pending']==1
    assert json.loads(db.get_setting('smartled_contract_search_cursor',''))['day']=='2026-10-02'
    assert db.get_setting('shopping_cursor','')==''


def test_invalid_page_count_does_not_enqueue_or_advance(monkeypatch):
    monkeypatch.setattr(api,'fetch_page',lambda *a,**k:([HEADER],2))
    with pytest.raises(api.ContractResponseError): collect._scan('search_cursor',api.SEARCH,DAY)
    assert store.queue_status()['pending']==0
    assert db.get_setting('smartled_contract_search_cursor_latest','')==''


def test_full_queue_does_not_advance(monkeypatch):
    monkeypatch.setattr(store,'MAX_PENDING',1)
    store.enqueue('OLD')
    monkeypatch.setattr(api,'fetch_page',lambda *a,**k:([HEADER],1))
    with pytest.raises(store.QueueFullError): collect._scan('search_cursor',api.SEARCH,DAY)
    assert db.get_setting('smartled_contract_search_cursor_latest','')==''


def test_complete_refresh_then_no_match_retires_current(monkeypatch):
    code=['39119802']
    def fetch(op,**kw):
        return ([{**DETAIL,'prdctClsfcNo':code[0]}],1) if op==api.DETAIL else ([HEADER],1)
    monkeypatch.setattr(api,'fetch_page',fetch)
    store.enqueue('U1')
    assert collect._refresh_one(DAY)['status']=='SAVED'
    assert store.list_current()[0]['fixture_amount']=='100'
    code[0]='39112102';store.enqueue('U1','CHANGE')
    assert collect._refresh_one(DAY)['status']=='SAVED'
    assert store.list_current()==[] and store.queue_status()['pending']==0


def test_incomplete_refresh_preserves_prior_snapshot(monkeypatch):
    prior=api.normalize_snapshot(HEADER,[DETAIL],complete=True,expected_count=1)
    store.save_snapshot(prior);store.enqueue('U1','CHANGE')
    monkeypatch.setattr(api,'fetch_page',lambda op,**kw: ([DETAIL],2) if op==api.DETAIL else ([HEADER],1))
    assert collect._refresh_one(DAY)['status']=='UNVERIFIED'
    assert store.list_current()[0]['fixture_amount']=='100'
    assert store.next_pending()['attempts']==1


def test_header_changes_while_detail_loading_never_commits(monkeypatch):
    n=[0]
    def fetch(op,**kw):
        if op==api.DETAIL:return [DETAIL],1
        n[0]+=1
        return [{**HEADER,'thtmCntrctAmt':str(n[0])}],1
    monkeypatch.setattr(api,'fetch_page',fetch);store.enqueue('U1')
    assert collect._refresh_one(DAY)['status']=='UNVERIFIED'
    assert store.get_snapshot('U1') is None


def test_corrected_date_before_boundary_retires_not_deletes(monkeypatch):
    prior=api.normalize_snapshot(HEADER,[DETAIL],complete=True,expected_count=1)
    store.save_snapshot(prior);store.enqueue('U1','CHANGE')
    monkeypatch.setattr(api,'fetch_page',lambda *a,**kw:([{**HEADER,'cntrctCnclsDate':'2026-09-30'}],1))
    assert collect._refresh_one(DAY)['status']=='SAVED'
    assert not store.list_current()
    assert store.get_snapshot('U1')['snapshot']==prior


def test_existing_contract_change_without_smart_name_is_queued(monkeypatch):
    store.save_snapshot(api.normalize_snapshot(HEADER,[DETAIL],complete=True,expected_count=1))
    monkeypatch.setattr(api,'fetch_page',lambda *a,**kw:([{**HEADER,'cntrctNm':'일반 LED로 정정'}, {'untyCntrctNo':'UNRELATED'}],2))
    collect._scan('changes_cursor',api.CHANGES,DAY)
    assert store.next_pending()['unified_no']=='U1'
    assert store.queue_status()['pending']==1


def test_service_failure_is_not_success_or_zero(monkeypatch):
    def fail(*a,**kw):raise RuntimeError('KEY_UNAVAILABLE')
    monkeypatch.setattr(api,'fetch_page',fail)
    result=collect.run_slice(today=dt.date(2026,10,22))
    assert result['status']=='UNVERIFIED'
    assert db.get_setting('smartled_contract_status','')=='UNVERIFIED'


def test_contract_page_authenticated_read_only_summary(monkeypatch):
    import vnext_clean_app as app
    from starlette.requests import Request
    request=Request({'type':'http','method':'GET','path':'/smartled','query_string':b'','headers':[]})
    store.save_snapshot(api.normalize_snapshot({**HEADER,'cntrctNm':'<script>bad()</script>'},[DETAIL],complete=True,expected_count=1))
    monkeypatch.setattr(app,'require_user',lambda r:{'username':'test'})
    monkeypatch.setattr(app,'is_result_server',lambda:False)
    monkeypatch.setattr(store,'ensure_schema',lambda:pytest.fail('DDL in HTTP read'))
    monkeypatch.setattr(api,'fetch_page',lambda *a,**k:pytest.fail('source fetch in HTTP read'))
    text=app.smartled_page(request).body.decode()
    assert '&lt;script&gt;' in text and '<script>bad()' not in text
    assert '39119802' in text and '스마트LED 실내등' in text
    assert '전수 수집을 보장하지' in text


def test_changed_total_shrink_restarts_page_one():
    value=collect._next_cursor({'day':'2026-10-21','page':2,'total':200},[],80)
    assert json.loads(value)=={'day':'2026-10-21','page':1}


@pytest.mark.parametrize('which',['contract','shopping'])
def test_memory_hold_propagates_without_failure_or_extra_work(monkeypatch,which):
    import memory_guard
    import smartled_collection_vnext as shopping
    def hold(*a,**kw):raise memory_guard.MemoryPressureError('TEST_HOLD')
    if which=='shopping':
        monkeypatch.setattr(shopping,'collect_next_day',hold)
        runner=shopping.run_slice
    else:
        store.enqueue('U1')
        monkeypatch.setattr(api,'fetch_page',hold)
        runner=lambda:collect.run_slice(today=dt.date(2026,10,22))
    with pytest.raises(memory_guard.MemoryPressureError):runner()
    if which=='contract':assert store.next_pending()['attempts']==0


def test_contract_and_shopping_share_quota_but_connection_status_is_separate(monkeypatch):
    import vnext_http
    monkeypatch.setenv('G2B_VNEXT_API_DAILY_LIMIT','2')
    assert vnext_http._quota_take('contract')==(1,2)
    assert vnext_http._quota_take('shopping')==(2,2)
    with pytest.raises(vnext_http.VNextLocalQuotaReached):vnext_http._quota_take('contract')
    vnext_http._record_connection_probe('OK','SUCCESS')
    vnext_http._record_connection_probe('FAILED','AUTH',prefix='smartled_contract')
    assert db.get_setting('g2b_api_connection_status','')=='OK'
    assert db.get_setting('smartled_contract_connection_status','')=='FAILED'


def test_latest_partial_page_survives_midnight(monkeypatch):
    db.set_setting('smartled_contract_changes_cursor_latest',json.dumps({'day':'2026-10-20','page':2,'total':101}))
    called=[]
    def fetch(op,**kw):
        called.append((kw['start_date'],kw['page']))
        return [{'untyCntrctNo':'UNKNOWN'}],101
    monkeypatch.setattr(api,'fetch_page',fetch)
    collect._scan('changes_cursor',api.CHANGES,DAY)
    assert called==[('2026-10-20',2)]
    assert json.loads(db.get_setting('smartled_contract_changes_cursor_latest',''))['day']=='2026-10-21'
