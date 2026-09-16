"""Independent safety expectations for PR8's unmodified 1bad48bd source.

Synthetic inputs only. Failures record unmet safety invariants or design gaps, not
patched implementations. Run with the pinned source first on PYTHONPATH.
"""
import io
import json
import socket
import urllib.error

import pytest
import db
import analysis_vnext
import award_projection
import award_vnext
import bid_vnext
import classification_vnext
import contract_projection
import g2b_vnext_canary
import g2b_vnext_pipeline
import historical_vnext
import lofin_vnext_http
import vnext_http
import vnext_paging
import vnext_store


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'audit.sqlite3'))
    monkeypatch.setenv('G2B_SERVICE_KEY', '')
    monkeypatch.setenv('LOFIN_API_KEY', '')
    monkeypatch.setenv('G2B_AUTO_SYNC', '0')
    def blocked(*args, **kwargs):
        raise AssertionError('LIVE_NETWORK_DISABLED_FOR_REVALIDATION')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    db.init_db()
    vnext_store.ensure_foundation()


def rows(sql, values=()):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, values).fetchall()]


def notice(no='A'):
    payload = {'bidNtceNo': no, 'bidNtceOrd': '000', 'bidNtceNm': 'LED replacement service'}
    vnext_store.preserve_raw('bid_notice_service', no+'|000', payload)
    return payload


def final(no='A', execution='1'):
    payload = {'bidNtceNo': no, 'bidNtceOrd': '000', 'bidClsfcNo': execution,
               'rbidNo': '0', 'bidwinnrNm': 'SYNTHETIC_VENDOR',
               'bidwinnrBizno': '1111111111', 'sucsfbidAmt': '100', 'sucsfbidRate': '88.1'}
    key = award_vnext._raw_source_key(payload)
    vnext_store.preserve_raw('award_result_service', key, payload)
    award_projection.project_final_award_row(payload, raw_source_key=key)
    return key


def opening(no='A'):
    return {'bidNtceNo': no, 'bidNtceOrd': '000', 'bidClsfcNo': '1', 'rbidNo': '0',
            'progrsDivCdNm': '개찰완료', 'opengCorpInfo': 'SYNTHETIC_FIRST^1111111111^REP^100^88.1'}


def contract(no='A', contract_no='C1'):
    return {'dcsnCntrctNo': contract_no, 'bidNtceNo': no, 'bidNtceOrd': '000',
            'thtmCntrctAmt': '100',
            'corpList': '[1^단독^^SYNTHETIC_VENDOR^REP^KR^100^^CONTACT^1111111111]'}


def summary(key='A|000|1|0'):
    return rows('SELECT * FROM award_results WHERE source_key=?', (key,))[0]


@pytest.mark.parametrize('wire', ['json', 'xml'])
def test_missing_g2b_total_is_not_invented_from_page_length(wire):
    if wire == 'json':
        raw = json.dumps({'response': {'header': {'resultCode': '00'},
                          'body': {'items': [{'bidNtceNo': 'A'}, {'bidNtceNo': 'B'}]}}}).encode()
    else:
        raw = b'<response><header><resultCode>00</resultCode></header><body><items><item><bidNtceNo>A</bidNtceNo></item><item><bidNtceNo>B</bidNtceNo></item></items></body></response>'
    batch, total = vnext_http.parse_response(raw)
    assert not vnext_paging.source_page_complete(len(batch), 2, len(batch), total), {'parsed_total': total}


def test_missing_lofin_json_total_is_not_invented_from_page_length():
    raw = json.dumps({'QWGJK': [{'head': [{'RESULT': {'CODE': 'INFO-000'}}]},
                               {'row': [{'dbiz_cd': 'A'}, {'dbiz_cd': 'B'}]}]}).encode()
    batch, total, *_ = lofin_vnext_http.parse_response(raw)
    assert not vnext_paging.source_page_complete(len(batch), 2, len(batch), total), {'parsed_total': total}


def test_missing_total_real_parser_to_collector_does_not_lose_second_page(monkeypatch):
    pages = {1: [{'bidNtceNo': 'A'}, {'bidNtceNo': 'B'}], 2: [{'bidNtceNo': 'C'}]}
    calls = []
    def fetch(*args, page=1, **kwargs):
        calls.append(page)
        return vnext_http.parse_response(json.dumps({'response': {'header': {'resultCode': '00'}, 'body': {'items': pages[page]}}}).encode())
    monkeypatch.setattr(bid_vnext, 'fetch_page', fetch)
    result = bid_vnext.collect_all('service', '2026-09-01', '2026-09-01', page_size=2, max_pages=3)
    saved = rows("SELECT source_key FROM raw_records WHERE dataset='bid_notice_service'")
    assert len(saved) == 3, {'calls': calls, 'raw_rows': len(saved), 'result': result}


@pytest.mark.parametrize('wire', [b'{"error":"temporary failure"}', b'<html><body>Service unavailable</body></html>'])
def test_unrecognized_response_never_becomes_valid_empty_success(wire):
    with pytest.raises(Exception):
        vnext_http.parse_response(wire)


def test_http_503_json_body_does_not_turn_into_success(monkeypatch):
    monkeypatch.setattr(vnext_http, '_quota_take', lambda kind: (1, 100))
    def error(*args, **kwargs):
        raise urllib.error.HTTPError('https://example.invalid', 503, 'Service unavailable', {}, io.BytesIO(b'{"error":"temporary"}'))
    monkeypatch.setattr(vnext_http.urllib.request, 'urlopen', error)
    with pytest.raises(Exception):
        vnext_http.request('https://example.invalid', 'opening', retries=1)


def test_empty_page_cannot_override_positive_incomplete_total():
    assert not vnext_paging.source_page_complete(0, 2, 2, 100)


def test_checkpoint_write_failure_does_not_double_count_retried_page(monkeypatch):
    pages = {1: [{'bidNtceNo': 'A'}, {'bidNtceNo': 'B'}], 2: [{'bidNtceNo': 'C'}, {'bidNtceNo': 'D'}]}
    monkeypatch.setattr(bid_vnext, 'fetch_page', lambda *a, page=1, **k: (pages[page], 4))
    original = bid_vnext.save_checkpoint
    state = {'failed': False}
    def checkpoint(dataset, scope, **kw):
        if kw.get('status') == 'RUNNING' and kw.get('fetched_count') == 2 and not state['failed']:
            state['failed'] = True
            raise RuntimeError('SYNTHETIC_CHECKPOINT_WRITE_FAILURE')
        return original(dataset, scope, **kw)
    monkeypatch.setattr(bid_vnext, 'save_checkpoint', checkpoint)
    with pytest.raises(RuntimeError):
        bid_vnext.collect_all('service', '2026-09-01', '2026-09-01', page_size=2)
    result = bid_vnext.collect_all('service', '2026-09-01', '2026-09-01', page_size=2)
    actual = len(rows("SELECT * FROM raw_records WHERE dataset='bid_notice_service'"))
    assert not result['complete'] or actual == 4, {'actual_raw': actual, 'reported': result}


def test_resume_page_size_change_cannot_skip_rows_and_report_complete(monkeypatch):
    source = [{'bidNtceNo': str(i)} for i in range(6)]
    monkeypatch.setattr(bid_vnext, 'fetch_page', lambda *a, page=1, rows=2, **k: (source[(page-1)*rows:page*rows], 6))
    bid_vnext.collect_all('service', '2026-09-01', '2026-09-01', page_size=2, max_pages=1)
    try:
        result = bid_vnext.collect_all('service', '2026-09-01', '2026-09-01', page_size=4, max_pages=3)
    except ValueError:
        return
    actual = len(rows("SELECT * FROM raw_records WHERE dataset='bid_notice_service'"))
    assert not result['complete'] or actual == 6, {'actual_raw': actual, 'reported': result}


def test_repeated_page_cannot_count_as_full_source_coverage(monkeypatch):
    batch = [{'bidNtceNo': 'A'}, {'bidNtceNo': 'B'}]
    monkeypatch.setattr(bid_vnext, 'fetch_page', lambda *a, **k: (batch, 4))
    result = bid_vnext.collect_all('service', '2026-09-01', '2026-09-01', page_size=2, max_pages=3)
    actual = len(rows("SELECT * FROM raw_records WHERE dataset='bid_notice_service'"))
    assert not result['complete'] or actual == 4, {'actual_raw': actual, 'reported': result}


def test_ambiguous_final_execution_retires_previous_contract_assignment():
    notice(); key = final()
    payload = contract()
    contract_projection.project_contract_row(payload, raw_source_key='CONTRACT_RAW_1')
    final(execution='2')
    result = contract_projection.project_contract_row(payload, raw_source_key='CONTRACT_RAW_1')
    assert result['award_summary_linked'] is False
    active = rows("SELECT * FROM lifecycle_links WHERE link_type='RESULTED_IN_CONTRACT' AND confidence>0")
    assert summary(key)['contract_no'] == '' and not active


def test_unresolved_corrected_contract_clears_previous_facts():
    notice(); key = final()
    contract_projection.project_contract_row(contract(), raw_source_key='CONTRACT_RAW_1')
    result = contract_projection.project_contract_row(contract('MISSING_NOTICE'), raw_source_key='CONTRACT_RAW_1')
    assert result['reason'] == 'notice_unresolved'
    assert summary(key)['contract_no'] == ''


def test_contract_remap_retires_old_notice_relationship():
    notice('A'); final('A'); notice('B'); final('B')
    contract_projection.project_contract_row(contract('A'), raw_source_key='CONTRACT_RAW_1')
    contract_projection.project_contract_row(contract('B'), raw_source_key='CONTRACT_RAW_1')
    old = rows("SELECT * FROM lifecycle_links WHERE from_type='bid_notice' AND from_key='A|000' AND to_key='C1' AND confidence>0")
    assert not old


def test_multiple_contracts_are_not_silently_reduced_to_last_one():
    notice(); key = final()
    contract_projection.project_contract_row(contract(contract_no='C1'), raw_source_key='CONTRACT_RAW_1')
    contract_projection.project_contract_row(contract(contract_no='C2'), raw_source_key='CONTRACT_RAW_2')
    state = summary(key)
    # A single summary field must not silently select the last of several contracts.
    assert state['contract_no'] == '', {'chosen_contract': state['contract_no']}


def test_malformed_joint_party_cannot_be_silently_discarded_into_single_party():
    notice(); final()
    payload = contract()
    payload['corpList'] += ',[MALFORMED_SECOND_PARTY]'
    result = contract_projection.project_contract_row(payload, raw_source_key='CONTRACT_RAW_1')
    assert not result['single_party_projected']


def test_analysis_does_not_present_stale_opening_as_current_fact():
    notice(); classification_vnext.classify_dataset('bid_notice_service')
    payload = opening(); key = award_vnext._raw_source_key(payload)
    vnext_store.preserve_raw('opening_result_service', key, payload)
    award_projection.normalize_dataset('opening_result_service')
    vnext_store.preserve_raw('opening_result_service', key, dict(payload, opengCorpInfo='낙찰예정자 다수^detail'))
    result = analysis_vnext.service_lifecycle_rows()
    assert not result or result[0]['first_rank_vendor'] == '' or result[0].get('opening_stale') is True


def test_retired_execution_link_is_not_used_for_current_analysis():
    notice(); key = final(); classification_vnext.classify_dataset('bid_notice_service')
    with db.connect() as conn:
        conn.execute("UPDATE lifecycle_links SET confidence=0 WHERE link_type='HAS_AWARD_EXECUTION'")
    result = analysis_vnext.service_lifecycle_rows()
    assert not result or result[0]['award_summary_key'] == ''


def test_normalize_limit_repeated_calls_make_forward_progress():
    for no in ('A', 'B'):
        payload = opening(no)
        vnext_store.preserve_raw('opening_result_service', award_vnext._raw_source_key(payload), payload)
    award_projection.normalize_dataset('opening_result_service', limit=1)
    award_projection.normalize_dataset('opening_result_service', limit=1)
    remaining = rows("SELECT source_key FROM raw_records WHERE dataset='opening_result_service' AND normalized_at=''")
    assert not remaining, remaining


def test_manual_pipeline_does_not_project_partial_raw(monkeypatch):
    calls = []
    def fetch(name):
        return lambda *a, **k: calls.append(name) or {'complete': False}
    monkeypatch.setattr(g2b_vnext_pipeline.bid_vnext, 'collect_all', fetch('notice'))
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, 'collect_service_opening', fetch('opening'))
    monkeypatch.setattr(g2b_vnext_pipeline.award_vnext, 'collect_service_awards', fetch('award'))
    monkeypatch.setattr(g2b_vnext_pipeline.contract_vnext, 'collect_all', fetch('contract'))
    monkeypatch.setattr(g2b_vnext_pipeline.award_projection, 'normalize_dataset', fetch('normalize'))
    monkeypatch.setattr(g2b_vnext_pipeline.contract_projection, 'normalize_contracts', fetch('link'))
    monkeypatch.setattr(g2b_vnext_pipeline.classification_vnext, 'classify_all', fetch('classify'))
    g2b_vnext_pipeline.collect_service_lifecycle('2026-09-01', '2026-09-01', max_pages=1)
    assert 'normalize' not in calls and 'classify' not in calls, calls


def test_canary_nonempty_but_wrong_schema_is_not_verification(monkeypatch):
    fake = lambda *a, **k: ([{'unexpected_field': 'synthetic'}], 1)
    monkeypatch.setattr(g2b_vnext_canary.bid_vnext, 'fetch_page', fake)
    monkeypatch.setattr(g2b_vnext_canary.award_vnext, 'fetch_page', fake)
    monkeypatch.setattr(g2b_vnext_canary.contract_vnext, 'fetch_page', fake)
    monkeypatch.setattr(g2b_vnext_canary.shopping_vnext, 'fetch_page', fake)
    assert g2b_vnext_canary.run_canary(lookback_days=1)['status'] != 'CONCLUSIVE'


def test_control_explicit_zero_total_full_page_stays_running():
    raw = b'{"response":{"header":{"resultCode":"00"},"body":{"items":[{"x":1},{"x":2}],"totalCount":0}}}'
    batch, total = vnext_http.parse_response(raw)
    assert total == 0
    assert not vnext_paging.source_page_complete(len(batch), 2, len(batch), total)


def test_control_valid_total_collects_every_nonlighting_row(monkeypatch):
    pages = {1: [{'bidNtceNo': 'A', 'bidNtceNm': 'Cleaning'}, {'bidNtceNo': 'B', 'bidNtceNm': 'LED replacement'}], 2: [{'bidNtceNo': 'C', 'bidNtceNm': 'Design'}]}
    monkeypatch.setattr(bid_vnext, 'fetch_page', lambda *a, page=1, **k: (pages[page], 3))
    result = bid_vnext.collect_all('service', '2026-09-01', '2026-09-01', page_size=2)
    assert result['complete'] and len(rows("SELECT * FROM raw_records WHERE dataset='bid_notice_service'")) == 3


def test_control_single_to_multiple_opening_replaces_first_rank():
    payload = opening(); key = award_vnext._raw_source_key(payload)
    vnext_store.preserve_raw('opening_result_service', key, payload)
    award_projection.normalize_dataset('opening_result_service')
    vnext_store.preserve_raw('opening_result_service', key, dict(payload, opengCorpInfo='낙찰예정자 다수^detail'))
    award_projection.normalize_dataset('opening_result_service')
    assert summary(key)['first_rank_vendor'] == ''
    assert len(rows("SELECT * FROM raw_record_revisions WHERE dataset='opening_result_service'")) == 2


def test_control_final_award_revision_clears_own_group():
    key = final()
    payload = {'bidNtceNo': 'A', 'bidNtceOrd': '000', 'bidClsfcNo': '1', 'rbidNo': '0'}
    award_projection.project_final_award_row(payload, raw_source_key=key)
    assert summary(key)['final_vendor'] == '' and summary(key)['final_award_amount'] == 0


def test_control_single_to_joint_contract_clears_single_vendor():
    notice(); final()
    payload = contract()
    contract_projection.project_contract_row(payload, raw_source_key='CONTRACT_RAW_1')
    payload['corpList'] += ',[2^구성사^^SYNTHETIC_SECOND^REP^KR^40^^CONTACT^2222222222]'
    contract_projection.project_contract_row(payload, raw_source_key='CONTRACT_RAW_1')
    assert summary()['contract_vendor'] == '' and summary()['contract_no'] == 'C1'


def test_control_historical_partial_range_refuses_finalize():
    with pytest.raises(RuntimeError, match='incomplete'):
        historical_vnext.finalize_backfill('2026-09-01', '2026-09-01')
