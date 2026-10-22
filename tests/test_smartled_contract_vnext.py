import copy
from urllib.parse import parse_qs, urlsplit

import pytest
import smartled_contract_vnext as contract


def header(**extra):
    return dict(untyCntrctNo='U1', dcsnCntrctNo='R26TA0000000100',
        cntrctCnclsDate='2026-10-01', cntrctNm='교실 조명 구매',
        thtmCntrctAmt='1,000', totCntrctAmt='9000', **extra)


def item(code='39119802', amount='100', **extra):
    return dict(untyCntrctNo='U1', prdctClsfcNo=code, prdctIdntNo='P1',
        krnPrdctNm='제품', prdctQty='2', qtyUprcAmt='50', prdctAmt=amount, **extra)


def test_search_uses_contract_date_boundary_not_registration():
    params = contract.request_params(contract.SEARCH, start_date='2026-10-01', end_date='2026-10-22')
    assert params['inqryDiv'] == '1'
    assert params['inqryBgnDate'] == '20261001'
    assert 'inqryBgnDt' not in params
    assert 'cntrctNm' not in params


@pytest.mark.parametrize('operation', [contract.SUMMARY, contract.DETAIL, contract.CHANGES, contract.DELETIONS])
def test_other_date_query_modes(operation):
    params = contract.request_params(operation, start_date='2026-10-01', end_date='2026-10-01')
    assert params['inqryBgnDt'] == '202610010000'
    assert params['inqryEndDt'] == '202610012359'
    params = contract.request_params(operation, unified_no='U1')
    assert params['inqryDiv'] == '2' and params['untyCntrctNo'] == 'U1'
    assert 'inqryBgnDt' not in params


@pytest.mark.parametrize('kwargs', [
    dict(start_date='2026-09-30', end_date='2026-10-01'),
    dict(start_date='2026-10-02', end_date='2026-10-01'),
    dict(start_date='2026-10-01', end_date='2026-11-02'),
    dict(start_date='2026-10-01', end_date='2026-10-01', rows=101),
])
def test_invalid_query_bounds(kwargs):
    with pytest.raises(ValueError):
        contract.request_params(contract.SEARCH, **kwargs)


def test_identity_unified_required_decided_optional():
    h = header()
    del h['dcsnCntrctNo']
    assert contract.normalize_snapshot(h, [], complete=True, expected_count=0)['unified_contract_no'] == 'U1'
    h.pop('untyCntrctNo')
    with pytest.raises(contract.ContractResponseError, match='MISSING_UNIFIED'):
        contract.normalize_snapshot(h, [], complete=True, expected_count=0)


def test_details_separate_totals_preserve_identical_rows():
    rows = [item(), item(), item('39119801', '30'), item('39119804', '70'), item('39111603', '600')]
    snapshot = contract.normalize_snapshot(header(), rows, complete=True, expected_count=5)
    assert len(snapshot['items']) == 4
    assert [r['source_position'] for r in snapshot['items']] == [1, 2, 3, 4]
    assert snapshot['item_subtotals'] == {'FIXTURE': '200', 'LAMP': '30', 'CONTROL': '70'}
    assert snapshot['contract_amount'] == '1000'
    assert snapshot['long_term_total_amount'] == '9000'
    assert len(snapshot['source_payload_sha256']) == 64


def test_title_and_old_or_ten_digit_codes_do_not_prove_smart():
    h = header()
    h['cntrctNm'] = '스마트 LED 사업'
    snapshot = contract.normalize_snapshot(h, [item('39112102'), item('3911980201')], complete=True, expected_count=2)
    assert snapshot['smartled_match'] is False
    assert snapshot['items'] == []
    # Explicit complete no-match is returned, so storage can retire old evidence.
    assert snapshot['source_state'] == 'CURRENT'


def test_missing_amount_is_unknown_not_zero_or_whole_contract():
    rows = [item(amount=''), item()]
    snapshot = contract.normalize_snapshot(header(), rows, complete=True, expected_count=2)
    assert snapshot['item_subtotals']['FIXTURE'] is None
    h = header(); h['thtmCntrctAmt'] = ''
    assert contract.normalize_snapshot(h, [], complete=True, expected_count=0)['contract_amount'] is None


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity', True, 'bad'])
def test_invalid_numeric_values_fail_closed(value):
    with pytest.raises(contract.ContractResponseError, match='INVALID_CONTRACT_NUMBER'):
        contract.normalize_snapshot(header(), [item(amount=value)], complete=True, expected_count=1)


def test_joint_parties_do_not_drop_members_or_retain_contacts():
    h = header()
    h['corpList'] = '[1^공동^분담^업체A^대표A^한국^60^채권A^담당A^1234567890],[2^공동^분담^업체B^대표B^한국^40^채권B^담당B^2345678901]'
    h['dminsttList'] = '[1^A1^기관A^자치^부서A^직원A^01000000000],[2^B1^기관B^자치^부서B^직원B^01011111111]'
    snap = contract.normalize_snapshot(h, [], complete=True, expected_count=0)
    assert [x['name'] for x in snap['vendors']] == ['업체A', '업체B']
    assert snap['agencies'] == [{'code': 'A1', 'name': '기관A'}, {'code': 'B1', 'name': '기관B'}]
    assert '직원A' not in str(snap) and '01000000000' not in str(snap)


def test_malformed_party_lists_fail_closed():
    with pytest.raises(contract.ContractResponseError):
        contract.vendors('[1^업체]')
    with pytest.raises(contract.ContractResponseError):
        contract.agencies('not a list')


@pytest.mark.parametrize('rows,expected,complete', [([item()], 2, True), ([item()], 1, False), ([], True, True)])
def test_incomplete_snapshot_cannot_replace_current(rows, expected, complete):
    with pytest.raises(contract.ContractResponseError):
        contract.normalize_snapshot(header(), rows, complete=complete, expected_count=expected)


def test_mixed_identity_and_revision_fail_closed():
    row = item(); row['untyCntrctNo'] = 'U2'
    with pytest.raises(contract.ContractResponseError, match='IDENTITY_MISMATCH'):
        contract.normalize_snapshot(header(), [row], complete=True, expected_count=1)
    row = item(dcsnCntrctNo='R26TA0000000101')
    with pytest.raises(contract.ContractResponseError, match='REVISION_MISMATCH'):
        contract.normalize_snapshot(header(), [row], complete=True, expected_count=1)


def test_contract_date_scope_checked_on_response():
    h = header(); h['cntrctCnclsDate'] = '20260930'
    with pytest.raises(contract.ContractResponseError, match='BEFORE_SMARTLED_START'):
        contract.normalize_snapshot(h, [], complete=True, expected_count=0)


def test_deletion_is_source_deletion_not_legal_cancellation():
    event = contract.deletion_event({'untyCntrctNo': 'U1', 'chgDivNm': 'DEL', 'dltDt': '2026-10-22 10:00:00'})
    assert event['source_state'] == 'SOURCE_DELETED'
    assert event['legal_cancellation_confirmed'] is False
    with pytest.raises(contract.ContractResponseError):
        contract.deletion_event({'untyCntrctNo': 'U1', 'chgDivNm': 'CHG'})


def test_bounded_refresh_preserves_duplicate_rows_and_full_count():
    seen = []
    def fetch(number, page, size):
        seen.append((number, page, size))
        return ([item(), item()] if page == 1 else [item()]), 3
    snap = contract.refresh_snapshot(header(), fetch_detail=fetch, page_size=2)
    assert snap['item_subtotals']['FIXTURE'] == '300'
    assert len(snap['items']) == 3 and seen == [('U1', 1, 2), ('U1', 2, 2)]


@pytest.mark.parametrize('fetch,error', [
    (lambda n,p,s: ([item()], 1001), 'BOUND_EXCEEDED'),
    (lambda n,p,s: ([], 1), 'EARLY_END'),
    (lambda n,p,s: ([item()], 0), 'TOTAL_EXCEEDED'),
    (lambda n,p,s: ([item()], '1'), 'INVALID_CONTRACT_DETAIL_PAGE'),
    (lambda n,p,s: ([item()], 2 if p == 1 else 3), 'TOTAL_CHANGED'),
])
def test_refresh_bad_pages_never_produce_snapshot(fetch, error):
    with pytest.raises(contract.ContractResponseError, match=error):
        contract.refresh_snapshot(header(), fetch_detail=fetch, page_size=1, max_pages=4)


def test_snapshot_byte_cap_prevents_large_retained_payload(monkeypatch):
    monkeypatch.setattr(contract, 'MAX_SNAPSHOT_BYTES', 10)
    with pytest.raises(contract.ContractResponseError, match='BYTES_EXCEEDED'):
        contract.normalize_snapshot(header(), [item()], complete=True, expected_count=1)


def test_fetch_uses_existing_guarded_http_only(monkeypatch):
    monkeypatch.setattr(contract, 'get_service_key', lambda default: 'fixture-key')
    calls = []
    monkeypatch.setattr(contract, '_request', lambda url, kind: calls.append((url, kind)) or ([], 0))
    contract.fetch_page(contract.SEARCH, start_date='2026-10-01', end_date='2026-10-01')
    url, kind = calls[0]
    assert kind == 'contract'
    assert urlsplit(url).path.endswith('/getCntrctInfoListThngPPSSrch')
    assert parse_qs(urlsplit(url).query)['inqryBgnDate'] == ['20261001']


def test_guard_not_bypassed_when_no_contract_context(monkeypatch):
    monkeypatch.setattr(contract, 'get_service_key', lambda default: 'fixture-key')
    from vnext_source_guard import VNextSourceAccessError
    with pytest.raises(VNextSourceAccessError):
        contract.fetch_page(contract.SEARCH, start_date='2026-10-01', end_date='2026-10-01')


def test_optional_product_name_is_exact_scoped_filter_not_numeric_code():
    params = contract.request_params(contract.SEARCH, start_date='2026-10-01', end_date='2026-10-01', product_name='스마트LED')
    assert params['prdctClsfcNoNm'] == '스마트LED'
    assert 'prdctClsfcNo' not in params
    with pytest.raises(ValueError, match='PRODUCT_NAME_FILTER_INVALID'):
        contract.request_params(contract.SEARCH, start_date='2026-10-01', end_date='2026-10-01', product_name='LED')
    with pytest.raises(ValueError, match='PRODUCT_NAME_FILTER_INVALID'):
        contract.request_params(contract.DETAIL, unified_no='U1', product_name='스마트LED')


def test_unknown_date_shapes_and_bad_deletion_timestamp_rejected():
    with pytest.raises(contract.ContractResponseError):
        contract.request_params(contract.SEARCH, start_date='2026-W40-4', end_date='2026-10-01')
    with pytest.raises(contract.ContractResponseError, match='DELETION_DATE'):
        contract.deletion_event({'untyCntrctNo': 'U1', 'chgDivNm': 'DEL', 'dltDt': 'not a timestamp'})


@pytest.mark.parametrize('number', ['1e99999999','1e-99999999','9'*129,'0e99999999'])
def test_reject_numeric_format_allocation_bombs(number):
    with pytest.raises(contract.ContractResponseError):
        contract._number(number)
