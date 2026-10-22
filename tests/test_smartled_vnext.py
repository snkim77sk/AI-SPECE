import datetime as dt
import pytest
import db
import shopping_store_v41 as store
import shopping_vnext
import smartled_vnext as smart
import smartled_collection_vnext as replay


def put(key, code='3911980301', date='2026-10-01', **extra):
    store.ensure_schema()
    row = dict(dlvrReqNo=key, prdctSno='1', dlvrReqRcptDate=date,
        dtilPrdctClsfcNo=code, prdctNm='확인 제품', dminsttNm='부여군',
        corpNm='조명회사', cntrctNo='C1', prdctAmt='123', fnlDlvrReqYn='Y')
    row.update(extra)
    store.preserve_record('shopping_delivery', key, row, source_date=date,
        source_system='G2B', source_operation=shopping_vnext.SHOP_OPERATION, smartled_replay=True)


def test_exact_codes_date_boundary_and_mixed_amount():
    put('before', date='2026-09-30')
    put('start')
    put('ordinary', '3911160302', prdctNm='스마트 도시 LED')
    put('control', '3911980401')
    with pytest.raises(ValueError,match='OUTSIDE_TARGET_SCOPE'): put('lamp', '3911980101')
    put('mixed', '3911980201', dlvrReqAmt='999999')
    rows = smart.shopping_rows(end_date='2026-10-22')['rows']
    assert {r['source_key'] for r in rows} == {'start', 'mixed'}
    assert sum(r['amount'] for r in rows) == 246
    assert smart.shopping_rows(kind='CONTROL')['rows'][0]['source_key'] == 'control'
    with pytest.raises(ValueError): smart.shopping_rows(kind='LAMP')
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM shopping_records WHERE source_key='lamp'").fetchone()['n']==0


def test_repeated_update_and_retired_records_not_double_counted():
    put('same')
    put('same', prdctAmt='456')
    put('old', fnlDlvrReqYn='N')
    put('deleted')
    with db.connect() as conn:
        conn.execute("UPDATE shopping_records SET is_active=0 WHERE source_key='deleted'")
    rows = smart.shopping_rows()['rows']
    assert [(r['source_key'], r['amount']) for r in rows] == [('same', 456)]


def test_bounded_pagination_and_literal_search():
    for i in range(4): put(str(i))
    a = smart.shopping_rows(limit=2)
    b = smart.shopping_rows(limit=2, offset=2)
    assert a['has_more'] and not b['has_more']
    assert len({r['source_key'] for r in a['rows'] + b['rows']}) == 4
    assert not smart.shopping_rows(query='%')['rows']
    with pytest.raises(ValueError): smart.shopping_rows(offset=50001)
    with pytest.raises(ValueError): smart.shopping_rows(kind='ALL')
    with pytest.raises(ValueError): smart.date_range('2026-09-30', '2026-10-01')


def test_menu_no_login_and_escaped_read_only(monkeypatch):
    from starlette.requests import Request
    import vnext_clean_app as app
    req = Request({'type': 'http', 'method': 'GET', 'path': '/smartled', 'query_string': b'region=', 'headers': []})
    monkeypatch.setattr(app, 'require_user', lambda r: None)
    assert app.smartled_page(req).status_code == 302
    monkeypatch.setattr(app, 'require_user', lambda r: {'username': 'test'})
    monkeypatch.setattr(app, 'is_result_server', lambda: False)
    put('safe', prdctNm='<script>alert(1)</script>')
    text = app.smartled_page(req).body.decode()
    assert '스마트LED' in text and '&lt;script&gt;' in text
    assert '<script>alert(1)</script>' not in text
    assert '기존 LED조명 서비스와 동일한' in text and '전체 기간 총액 아님' in text


def test_replay_resumes_partial_no_normal_checkpoint_reset(monkeypatch):
    calls = []
    def collect(start, end, **kwargs):
        calls.append((start, end, kwargs))
        return {'complete': len(calls) > 1, 'saved': 1}
    monkeypatch.setattr(shopping_vnext, 'collect_all', collect)
    assert replay.collect_next_day(today=dt.date(2026,10,22))['status'] == 'PARTIAL'
    assert db.get_setting(replay.CURSOR_KEY, smart.START_DATE) == smart.START_DATE
    assert replay.collect_next_day(today=dt.date(2026,10,22))['status'] == 'COMPLETE'
    assert db.get_setting(replay.CURSOR_KEY, '') == '2026-10-02'
    assert calls[0][0] == calls[1][0] == '2026-10-01'
    assert calls[0][2] == dict(page_size=250, max_pages=2, resume=True, smartled_replay=True)


def test_replay_distinct_receipt_and_all_targets_preserved(monkeypatch):
    rows = [dict(dlvrReqNo='R1', prdctSno='1', dlvrReqRcptDate='20261001', dtilPrdctClsfcNo='3911980401'),
            dict(dlvrReqNo='R2', prdctSno='1', dlvrReqRcptDate='20261001', dtilPrdctClsfcNo='3911160302')]
    monkeypatch.setattr(shopping_vnext, 'fetch_page', lambda *a, **k: (rows, 2))
    result = shopping_vnext.collect_all('2026-10-01', '2026-10-01', smartled_replay=True)
    assert result['complete']
    from vnext_store import get_checkpoint
    assert get_checkpoint('shopping_delivery','smartled-v1:2026-10-01:2026-10-01')
    assert not get_checkpoint('shopping_delivery','2026-10-01:2026-10-01')
    with db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) n FROM shopping_records').fetchone()['n'] == 2


def test_regular_reconciliation_does_not_retire_smart_extras(monkeypatch):
    lamp=dict(dlvrReqNo='L1',prdctSno='1',dlvrReqRcptDate='20261001',dtilPrdctClsfcNo='3911980401')
    regular=dict(dlvrReqNo='R1',prdctSno='1',dlvrReqRcptDate='20261001',dtilPrdctClsfcNo='3911160302')
    monkeypatch.setattr(shopping_vnext,'fetch_page',lambda *a,**kw:([lamp,regular],2))
    shopping_vnext.collect_all('2026-10-01','2026-10-01',smartled_replay=True)
    monkeypatch.setattr(shopping_vnext,'fetch_page',lambda *a,**kw:([regular],1))
    shopping_vnext.collect_all('2026-10-01','2026-10-01')
    assert len(smart.shopping_rows(kind='CONTROL')['rows'])==1
    shopping_vnext.collect_all('2026-10-01','2026-10-01',resume=False,smartled_replay=True)
    assert not smart.shopping_rows(kind='CONTROL')['rows']


def test_finished_smart_baseline_rotates_recheck_without_resetting_partial(monkeypatch):
    db.set_setting(replay.CURSOR_KEY,'2026-10-22')
    import vnext_store
    state=['COMPLETE'];calls=[]
    monkeypatch.setattr(vnext_store,'get_checkpoint',lambda *a,**k:{'status':state[0]})
    monkeypatch.setattr(shopping_vnext,'collect_all',lambda *a,**k:calls.append(k) or {'complete':False})
    replay.collect_next_day(today=dt.date(2026,10,22))
    assert calls[-1]['resume'] is False
    state[0]='INCOMPLETE'
    replay.collect_next_day(today=dt.date(2026,10,22))
    assert calls[-1]['resume'] is True
    assert db.get_setting(replay.CURSOR_KEY,'')=='2026-10-22'


def test_replay_memory_hold_is_not_swallowed(monkeypatch):
    import memory_guard
    def hold():raise memory_guard.MemoryPressureError('TEST_HOLD')
    monkeypatch.setattr(replay,'collect_next_day',hold)
    with pytest.raises(memory_guard.MemoryPressureError):replay.run_slice()


def test_normal_collection_precedes_smart_replay_in_same_worker(monkeypatch):
    import vnext_clean_app as app
    import shopping_recent_vnext
    events=[]
    monkeypatch.setattr(app,'backend_status',lambda:{'backend_ok':True})
    monkeypatch.setattr(app,'is_unified',lambda:False)
    monkeypatch.setattr(app,'get_service_key',lambda default='':'fixture-key')
    monkeypatch.setattr(shopping_recent_vnext,'collect_forward',lambda **kw:events.append('normal') or {'status':'COMPLETE','complete':True})
    monkeypatch.setattr(replay,'run_slice',lambda:events.append('smart') or {'status':'PARTIAL'})
    monkeypatch.setattr(store,'purge_history',lambda *a,**kw:{'deleted_records':0})
    app._run_recent_collection_once_impl(source='shopping')
    assert events==['normal','smart']


def test_attached_exact_seven_codes_and_lamp_excluded():
    expected={'3911980201','3911980202','3911980301','3911980302','3911980303','3911980304','3911980401'}
    assert set(smart.CODES)==expected
    for code in expected:put(code,code)
    with pytest.raises(ValueError,match='OUTSIDE_TARGET_SCOPE'): put('not-requested-lamp','3911980101')
    fixture=smart.shopping_rows(kind='FIXTURE')['rows']
    control=smart.shopping_rows(kind='CONTROL')['rows']
    assert {r['detail_item_no'] for r in fixture+control}==expected
    assert len(fixture)==6 and len(control)==1
    assert set(smart.KINDS)=={'FIXTURE','CONTROL'}


def test_recent_and_longtail_rechecks_alternate_without_extra_slots(monkeypatch):
    db.set_setting(replay.CURSOR_KEY,'2026-10-22')
    import vnext_store
    monkeypatch.setattr(vnext_store,'get_checkpoint',lambda *a,**kw:{'status':'COMPLETE'})
    calls=[]
    monkeypatch.setattr(shopping_vnext,'collect_all',lambda start,end,**kw:calls.append((start,end,kw)) or {'complete':True})
    replay.collect_next_day(today=dt.date(2026,10,22))
    replay.collect_next_day(today=dt.date(2026,10,22))
    assert [x[0] for x in calls]==['2026-10-15','2026-10-01']
    assert all(x[0]==x[1] and x[2]['max_pages']==2 for x in calls)
    assert db.get_setting('smartled_shopping_recheck_lane_v1','')=='recent'
    assert db.get_setting('smartled_shopping_longtail_next_date_v1','')=='2026-10-02'
