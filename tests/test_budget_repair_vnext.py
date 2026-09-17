"""Budget-focused regression: wire parser -> real SQLite -> receipt audit.

All names, codes, and amounts below are synthetic; the suite denies live network.
"""
import io
import json
import urllib.error
from contextlib import nullcontext

import pytest
import db
import budget_vnext as budget
import budget_snapshot_vnext as snapshots
import lofin_vnext_http as wire
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint
from vnext_collection import verified_checkpoint

DAY = '2026-09-01'
SCOPE = '2026:' + DAY


def row(code='A', **extra):
    return dict(fyr='2026', wa_laf_cd='WA', laf_cd='LA', dept_cd='DEPT',
                dbiz_cd=code, acnt_dv_cd='ACCOUNT', dbiz_nm='일반 행정사업', **extra)


def response(batch, total=None):
    head = [{'RESULT': {'CODE': 'INFO-000'}}]
    if total is not None:
        head.append({'list_total_count': total})
    return json.dumps({'QWGJK': [{'head': head}, {'row': batch}]}).encode()


def count(table='raw_records'):
    with db.connect() as conn:
        return conn.execute(f'SELECT COUNT(*) n FROM {table} WHERE dataset=?', ('budget',)).fetchone()['n']


@pytest.mark.parametrize('fmt', ['json', 'xml'])
def test_missing_total_through_real_budget_parser_and_all_pages(monkeypatch, fmt):
    seen = []
    def fetch(*args, page=1, **kwargs):
        seen.append(page)
        batch = [row('A'), row('B')] if page == 1 else [row('C')]
        data = response(batch)
        if fmt == 'xml':
            data = '<QWGJK><head><RESULT><CODE>INFO-000</CODE></RESULT></head>' + ''.join('<row>'+''.join(f'<{k}>{v}</{k}>' for k,v in r.items())+'</row>' for r in batch) + '</QWGJK>'
        return wire.parse_response(data)
    monkeypatch.setattr(budget, 'fetch_budget_page', fetch)
    result = budget.collect_full_budget(2026, DAY, page_size=2)
    assert result['complete'] and result['source_total'] is None and count() == 3
    assert seen == [1,2] and verified_checkpoint(get_checkpoint('budget', SCOPE))


@pytest.mark.parametrize('data', [b'<html>maintenance</html>', b'{"error":"backend down"}',
    b'{"QWGJK":[{"head":[{"RESULT":{"CODE":"INFO-000"}}]}]}',
    b'<RESULT><CODE>ERROR-300</CODE></RESULT>', b'', b'<QWGJK><row/></QWGJK>',
    b'<QWGJK><head><RESULT><CODE>INFO-000</CODE></RESULT></head><row><dbiz_cd>A</dbiz_cd></row><list_total_count>-1</list_total_count></QWGJK>'])
def test_invalid_budget_wire_is_never_complete(monkeypatch, data):
    # The last invalid total occurs outside the supported head and is tested below
    # separately as envelope metadata drift, not accepted as a valid total.
    if b'</row><list_total_count>' in data:
        data = data.replace(b'</row><list_total_count>-1</list_total_count>', b'</row>')
        data = data.replace(b'<head>', b'<head><list_total_count>-1</list_total_count>')
    monkeypatch.setattr(budget, 'fetch_budget_page', lambda *a,**k: wire.parse_response(data))
    with pytest.raises(Exception):
        budget.collect_full_budget(2026, DAY, max_pages=1)
    assert get_checkpoint('budget', SCOPE)['status'] == 'FAILED'
    assert count() == 0


@pytest.mark.parametrize('data', [b'<RESULT><CODE>INFO-200</CODE><MESSAGE>no data</MESSAGE></RESULT>', b'{"RESULT":{"CODE":"INFO-200"}}'])
def test_explicit_budget_no_data_is_distinct_from_api_failure(data):
    batch,total,code,_=wire.parse_response(data)
    assert batch==[] and total==0 and code=='INFO-200'


@pytest.mark.parametrize('change', ['repeat', 'empty', 'total_drift'])
def test_budget_anomalies_stop_without_invented_coverage(monkeypatch, change):
    def fetch(*a,page=1,**k):
        if page == 1: return [row('A'),row('B')],4,'INFO-000',''
        if change == 'repeat': return [row('A'),row('B')],4,'INFO-000',''
        if change == 'empty': return [],4,'INFO-000',''
        return [row('C'),row('D')],5,'INFO-000',''
    monkeypatch.setattr(budget, 'fetch_budget_page', fetch)
    result=budget.collect_full_budget(2026, DAY, page_size=2)
    assert not result['complete'] and result['fetched']==2
    assert not verified_checkpoint(get_checkpoint('budget', SCOPE))
    # New anomaly rows are still preserved, but never counted as verified coverage.
    assert count() == (4 if change=='total_drift' else 2)


def test_budget_checkpoint_failure_rolls_back_raw_and_revisions(monkeypatch):
    monkeypatch.setattr(budget,'fetch_budget_page', lambda *a,**k: ([row()],1,'INFO-000',''))
    real=budget.save_checkpoint
    def broken(*a,**k):
        if k.get('status')=='COMPLETE': raise RuntimeError('checkpoint unavailable')
        return real(*a,**k)
    monkeypatch.setattr(budget,'save_checkpoint',broken)
    with pytest.raises(RuntimeError): budget.collect_full_budget(2026,DAY)
    assert count()==count('raw_record_revisions')==0
    assert get_checkpoint('budget',SCOPE)['fetched_count']==0
    monkeypatch.setattr(budget,'save_checkpoint',real)
    assert budget.collect_full_budget(2026,DAY)['complete'] and count()==1


def test_budget_resume_wrong_page_size_rejected_before_network(monkeypatch):
    calls=[]
    def fetch(*a,**k):
        calls.append(k); return [row('A'),row('B')],4,'INFO-000',''
    monkeypatch.setattr(budget,'fetch_budget_page',fetch)
    budget.collect_full_budget(2026,DAY,page_size=2,max_pages=1)
    with pytest.raises(ValueError): budget.collect_full_budget(2026,DAY,page_size=4)
    assert len(calls)==1


@pytest.mark.parametrize('kwargs', [dict(fiscal_year=2025), dict(fiscal_year=2025,snapshot_date=DAY),
    dict(fiscal_year=2099,snapshot_date='2099-01-01'), dict(fiscal_year=2026,snapshot_date='2026-02-30')])
def test_invalid_budget_year_date_fails_before_call(monkeypatch, kwargs):
    def forbidden(*a,**k): raise AssertionError('source must not be called')
    monkeypatch.setattr(budget,'fetch_budget_page',forbidden)
    with pytest.raises(ValueError): budget.collect_full_budget(**kwargs)


def test_budget_identity_scope_and_same_snapshot_revision(monkeypatch):
    original=row()
    renamed={**original, 'dbiz_nm':'사업명 변경'}
    key=budget._source_key(original,2026,DAY)
    assert key==budget._source_key(renamed,2026,DAY)
    assert key!=budget._source_key(original,2026,'2026-09-02')
    assert key!=budget._source_key({**original,'wa_laf_cd':'WB'},2026,DAY)
    preserve_raw('budget',key,original); preserve_raw('budget',key,renamed)
    assert count()==1 and count('raw_record_revisions')==2


def test_budget_receipt_proof_rejects_tampering(monkeypatch):
    monkeypatch.setattr(budget,'fetch_budget_page',lambda *a,**k: ([row()],1,'INFO-000',''))
    budget.collect_full_budget(2026,DAY)
    cp=get_checkpoint('budget',SCOPE)
    assert verified_checkpoint(cp)
    assert not verified_checkpoint({**cp, 'page_no':99})
    with db.connect() as conn: conn.execute("UPDATE vnext_collection_items SET payload_sha256='not-an-existing-revision'")
    assert not verified_checkpoint(cp)


def test_budget_legacy_complete_checkpoint_is_replayed(monkeypatch):
    save_checkpoint('budget',SCOPE,status='COMPLETE',page_no=5,fetched_count=1,saved_count=1,source_total=1)
    calls=[]
    def fetch(*a,**k): calls.append(k['page']);return [row()],1,'INFO-000',''
    monkeypatch.setattr(budget,'fetch_budget_page',fetch)
    assert budget.collect_full_budget(2026,DAY)['complete']
    assert calls==[1]


@pytest.mark.parametrize('status',[400,503])
def test_lofin_http_error_never_empty_and_does_not_leak_key(monkeypatch,status):
    def bad(*a,**k):
        raise urllib.error.HTTPError('https://invalid/?Key=FAKE_SECRET',status,'FAKE_SECRET',{},io.BytesIO(response([])))
    monkeypatch.setattr(wire.urllib.request,'urlopen',bad)
    with pytest.raises(wire.LofinVNextApiError) as err: wire._request({'Key':'FAKE_SECRET'},retries=1)
    assert 'FAKE_SECRET' not in str(err.value)
    assert 'HTTP_'+str(status) in str(err.value)


def test_lofin_quota_is_separate_and_fail_closed(monkeypatch):
    db.set_setting('api_calls_total','777')
    db.set_setting('vnext_api_calls_total','123')
    monkeypatch.setenv('LOFIN_VNEXT_API_DAILY_LIMIT','1')
    assert wire._quota_take()==1
    with pytest.raises(wire.LofinVNextApiError): wire._quota_take()
    assert db.get_setting('api_calls_total')=='777' and db.get_setting('vnext_api_calls_total')=='123'


def test_snapshot_plan_explicit_scope_and_live_lock(monkeypatch):
    plan=snapshots.build_snapshot_plan(['2025-12-31',DAY,DAY])
    assert plan['snapshot_count']==2 and not plan['complete_historical_archive']
    with pytest.raises(RuntimeError): snapshots.run_snapshots([DAY])
    assert not snapshots.audit_snapshots([DAY])['all_requested_snapshots_complete']


def test_budget_canary_missing_key_never_calls_source(monkeypatch):
    monkeypatch.setattr(wire,'get_lofin_key',lambda:'')
    monkeypatch.setattr(wire,'fetch_budget_page',lambda *a,**k: (_ for _ in ()).throw(AssertionError('source called')))
    result=snapshots.run_budget_canary(snapshot_date=DAY)
    assert result['status']=='BLOCKED' and not result['live_request_attempted']


@pytest.mark.parametrize('batch,status', [([], 'NO_DATA'),([{'x':'y'}],'SCHEMA_MISMATCH'),([row()],'SCHEMA_PASS')])
def test_budget_canary_schema_no_data_and_sensitive_output(monkeypatch,batch,status):
    monkeypatch.setattr(wire,'get_lofin_key',lambda:'SENSITIVE_KEY')
    calls=[]
    def fetch(*a,**k): calls.append((a,k));return batch,len(batch),'INFO-000','SENSITIVE_MESSAGE'
    monkeypatch.setattr(wire,'fetch_budget_page',fetch)
    report=snapshots.run_budget_canary(snapshot_date=DAY,rows=999)
    assert report['status']==status and not report['coverage_verified']
    assert calls[0][0][2]=='' and calls[0][1]['size']==10 and calls[0][1]['retries']==1
    assert 'SENSITIVE' not in json.dumps(report) and count()==0


@pytest.mark.parametrize('bad', [dict(fyr='2025', dbiz_cd='A'), dict(exe_ymd='20260831',dbiz_cd='A'), dict(unexpected='shape')])
def test_budget_wrong_scope_or_identity_preserved_but_not_complete(monkeypatch,bad):
    monkeypatch.setattr(budget,'fetch_budget_page',lambda *a,**k:([bad],1,'INFO-000',''))
    result=budget.collect_full_budget(2026,DAY)
    assert result['status']=='INCOMPLETE' and not result['complete']
    assert count()==1 and result['fetched']==0
