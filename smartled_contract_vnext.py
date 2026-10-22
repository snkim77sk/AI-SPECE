"""Bounded goods-contract adapter for the smartLED collection integration.

Official schema/manual: https://www.data.go.kr/data/15129427/openapi.do
https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=FILE_000000003641732&fileDetailSn=1
Smart families: PPS notice 2504180012 (2025-04-18), effective 2025-04-17.

This module does NOT grant source access, install a scheduler, write DB rows, or
claim live validation. Requests still pass through vnext_http's source guard.
Callers must atomically replace a contract snapshot only after a complete refresh;
never append detail rows by product ID or commit a partial result as current.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, localcontext
from urllib.parse import urlencode

from db import get_service_key
from vnext_http import request as _request

START_DATE = dt.date(2026, 10, 1)
BASE_URL = 'https://apis.data.go.kr/1230000/ao/CntrctInfoService'
SEARCH = 'getCntrctInfoListThngPPSSrch'
DETAIL = 'getCntrctInfoListThngDetail'
SUMMARY = 'getCntrctInfoListThng'
CHANGES = 'getCntrctInfoListThngChgHstry'
DELETIONS = 'getCntrctInfoListThngDltHstry'
OPERATIONS = frozenset({SEARCH, DETAIL, SUMMARY, CHANGES, DELETIONS})
SMART_FAMILIES = {
    '39119801': 'LAMP', '39119802': 'FIXTURE',
    '39119803': 'FIXTURE', '39119804': 'CONTROL',
}
MAX_PAGE_SIZE = 100
MAX_DETAIL_ROWS = 1000
MAX_TEXT = 4096
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


class ContractResponseError(ValueError):
    """A refresh cannot safely be promoted to current-state storage."""


def _text(row, name):
    value = row.get(name)
    if value is None:
        return ''
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ContractResponseError('INVALID_CONTRACT_FIELD:' + name)
    text = str(value).strip()
    if len(text) > MAX_TEXT:
        raise ContractResponseError('CONTRACT_FIELD_TOO_LONG:' + name)
    return text


def _day(value):
    text = str(value or '').strip()
    if re.fullmatch(r'\d{8}', text):
        text = text[:4] + '-' + text[4:6] + '-' + text[6:]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
        raise ContractResponseError('INVALID_CONTRACT_DATE')
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ContractResponseError('INVALID_CONTRACT_DATE') from None


def _number(value):
    if value is None or str(value).strip() == '':
        return None
    if isinstance(value, bool):
        raise ContractResponseError('INVALID_CONTRACT_NUMBER')
    try:
        raw = str(value).replace(',', '').strip()
        if len(raw) > 128:
            raise InvalidOperation
        result = Decimal(raw)
        if (not result.is_finite() or abs(result.adjusted()) > 40
                or not -12 <= result.as_tuple().exponent <= 30):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise ContractResponseError('INVALID_CONTRACT_NUMBER') from None


def _decimal_text(value):
    if value is None:
        return None
    if not value.is_finite() or abs(value.adjusted()) > 44 or not -12 <= value.as_tuple().exponent <= 34:
        raise ContractResponseError('INVALID_CONTRACT_NUMBER')
    return format(value, 'f')


def contract_key(row):
    if not isinstance(row, dict):
        raise ContractResponseError('CONTRACT_OBJECT_REQUIRED')
    value = _text(row, 'untyCntrctNo')
    if not value:
        raise ContractResponseError('MISSING_UNIFIED_CONTRACT_ID')
    return value


def request_params(operation, *, start_date=None, end_date=None,
                   unified_no='', page=1, rows=MAX_PAGE_SIZE, product_name=''):
    """Build a narrow query; SEARCH dates mean signature date, not registration.

    Goods history endpoints use their documented registration/change/deletion
    timestamps. A date slice is at most 31 days; ongoing overlap is caller-owned.
    """
    if operation not in OPERATIONS:
        raise ValueError('UNSUPPORTED_SMARTLED_CONTRACT_OPERATION')
    if isinstance(page, bool) or int(page) < 1:
        raise ValueError('INVALID_CONTRACT_PAGE')
    size = int(rows)
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise ValueError('INVALID_CONTRACT_PAGE_SIZE')
    params = {'pageNo': int(page), 'numOfRows': size, 'type': 'json'}
    # Official PPSSrch exposes a product-classification NAME text filter, not a
    # numeric classification filter. Matching/tokenization has not been live-
    # validated; filtered results must never be reported as exhaustive coverage.
    if product_name:
        if operation != SEARCH or product_name != '스마트LED':
            raise ValueError('SMARTLED_PRODUCT_NAME_FILTER_INVALID')
        params['prdctClsfcNoNm'] = product_name
    if unified_no:
        if operation == SEARCH or start_date is not None or end_date is not None:
            raise ValueError('CONTRACT_QUERY_MODE_CONFLICT')
        key = contract_key({'untyCntrctNo': unified_no})
        params.update(inqryDiv='2', untyCntrctNo=key)
        return params
    begin, end = _day(start_date), _day(end_date)
    if begin < START_DATE or end < begin or (end - begin).days > 30:
        raise ValueError('CONTRACT_DATE_RANGE_OUTSIDE_SCOPE')
    params['inqryDiv'] = '1'
    if operation == SEARCH:
        params.update(inqryBgnDate=begin.strftime('%Y%m%d'),
                      inqryEndDate=end.strftime('%Y%m%d'))
    else:
        params.update(inqryBgnDt=begin.strftime('%Y%m%d') + '0000',
                      inqryEndDt=end.strftime('%Y%m%d') + '2359')
    return params


def fetch_page(operation, **kwargs):
    params = request_params(operation, **kwargs)
    key = get_service_key('')
    if not key:
        raise RuntimeError('SMARTLED_CONTRACT_SERVICE_KEY_UNAVAILABLE')
    params['serviceKey'] = key
    # No transport fallback or source-context creation here. Existing guard denies
    # this path until a specifically reviewed integration permits it.
    return _request(BASE_URL + '/' + operation + '?' + urlencode(params), 'contract')


def _tuples(value, width, label):
    if not value:
        return []
    if not isinstance(value, str) or len(value) > MAX_TEXT * 8:
        raise ContractResponseError('INVALID_' + label)
    text = value.strip()
    if not text.startswith('[') or not text.endswith(']'):
        raise ContractResponseError('INVALID_' + label)
    chunks = re.split(r'\]\s*,?\s*\[', text[1:-1])
    if len(chunks) > 100:
        raise ContractResponseError('TOO_MANY_' + label)
    result = []
    for chunk in chunks:
        parts = [part.strip() for part in chunk.split('^')]
        if len(parts) != width:
            raise ContractResponseError('INVALID_' + label)
        result.append(parts)
    return result


def vendors(value):
    # Do not retain representatives, employees, creditor names or phone numbers.
    return [{'name': p[3], 'business_no': p[9], 'share': p[6]}
            for p in _tuples(value, 10, 'CONTRACT_VENDORS')]


def agencies(value):
    return [{'code': p[1], 'name': p[2]}
            for p in _tuples(value, 7, 'CONTRACT_AGENCIES')]


def normalize_snapshot(header, detail_rows, *, complete, expected_count):
    """Return a complete replacement value, never a detail-row upsert list.

    Repeated identical details are retained with response-position provenance.
    A no-match snapshot is a valid current result that must retire an old match.
    Header amount and matched item subtotals are deliberately separate.
    """
    key = contract_key(header)
    contract_date = _day(_text(header, 'cntrctCnclsDate'))
    if contract_date < START_DATE:
        raise ContractResponseError('CONTRACT_BEFORE_SMARTLED_START')
    if not isinstance(detail_rows, list) or len(detail_rows) > MAX_DETAIL_ROWS:
        raise ContractResponseError('CONTRACT_DETAIL_BOUND_EXCEEDED')
    if (not complete or isinstance(expected_count, bool)
            or not isinstance(expected_count, int) or expected_count < 0
            or len(detail_rows) != expected_count):
        raise ContractResponseError('CONTRACT_DETAIL_INCOMPLETE')
    decided = _text(header, 'dcsnCntrctNo')
    matched = []
    totals = {kind: Decimal(0) for kind in ('FIXTURE', 'LAMP', 'CONTROL')}
    unknown = set()
    for position, row in enumerate(detail_rows, 1):
        if contract_key(row) != key:
            raise ContractResponseError('CONTRACT_DETAIL_IDENTITY_MISMATCH')
        detail_decided = _text(row, 'dcsnCntrctNo')
        if decided and detail_decided and detail_decided != decided:
            raise ContractResponseError('CONTRACT_DETAIL_REVISION_MISMATCH')
        code = _text(row, 'prdctClsfcNo')
        # Official detail schema uses exactly EIGHT digits. Ten-digit detailed
        # codes from shopping records must not silently be conflated with it.
        kind = SMART_FAMILIES.get(code)
        if kind is None:
            continue
        amount = _number(row.get('prdctAmt'))
        if amount is None:
            unknown.add(kind)
        else:
            with localcontext() as context:
                context.prec = 64
                totals[kind] += amount
        matched.append({
            'source_position': position, 'kind': kind, 'classification_no': code,
            'classification_name': _text(row, 'prdctClsfcNoNm'),
            'item_id': _text(row, 'prdctIdntNo'),
            'item_name': _text(row, 'krnPrdctNm'),
            'quantity': _decimal_text(_number(row.get('prdctQty'))),
            'unit_amount': _decimal_text(_number(row.get('qtyUprcAmt'))),
            'item_amount': _decimal_text(amount),
        })
    digest = hashlib.sha256()
    size = 0
    try:
        encoded = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).iterencode(
                {'header': header, 'detail_rows': detail_rows})
        for chunk in encoded:
            chunk = chunk.encode('utf-8')
            size += len(chunk)
            if size > MAX_SNAPSHOT_BYTES:
                raise ContractResponseError('CONTRACT_SNAPSHOT_BYTES_EXCEEDED')
            digest.update(chunk)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContractResponseError):
            raise
        raise ContractResponseError('INVALID_CONTRACT_PAYLOAD') from None
    return {
        'unified_contract_no': key, 'decided_contract_no': decided,
        'contract_date': contract_date.isoformat(),
        'contract_name': _text(header, 'cntrctNm'),
        'source_state': 'CURRENT', 'smartled_match': bool(matched),
        'vendors': vendors(header.get('corpList')),
        'agencies': agencies(header.get('dminsttList')),
        'contract_amount': _decimal_text(_number(header.get('thtmCntrctAmt'))),
        'long_term_total_amount': _decimal_text(_number(header.get('totCntrctAmt'))),
        'items': matched, 'source_detail_count': expected_count,
        'item_subtotals': {kind: None if kind in unknown else _decimal_text(total)
                          for kind, total in totals.items()},
        'source_payload_sha256': digest.hexdigest(),
    }


def deletion_event(row):
    key = contract_key(row)
    if _text(row, 'chgDivNm') != 'DEL' or not _text(row, 'dltDt'):
        raise ContractResponseError('INVALID_CONTRACT_DELETION_EVENT')
    deleted_at = _text(row, 'dltDt')
    try:
        if re.fullmatch(r'\d{14}', deleted_at):
            dt.datetime.strptime(deleted_at, '%Y%m%d%H%M%S')
        else:
            dt.datetime.fromisoformat(deleted_at.replace('Z', '+00:00'))
    except ValueError:
        raise ContractResponseError('INVALID_CONTRACT_DELETION_DATE') from None
    return {'unified_contract_no': key, 'source_state': 'SOURCE_DELETED',
            'source_deleted_at': deleted_at,
            'legal_cancellation_confirmed': False}


def refresh_snapshot(header, *, fetch_detail=None, page_size=100, max_pages=4):
    """Read a bounded complete detail set; callers commit only the returned value.

    A pending/oversized/changed-total result raises rather than retiring existing
    data. There is deliberately no in-memory partial snapshot returned as current.
    """
    key = contract_key(header)
    if _day(_text(header, 'cntrctCnclsDate')) < START_DATE:
        raise ContractResponseError('CONTRACT_BEFORE_SMARTLED_START')
    if not 1 <= int(page_size) <= MAX_PAGE_SIZE or not 1 <= int(max_pages) <= 10:
        raise ValueError('CONTRACT_REFRESH_BOUNDS_INVALID')
    if int(page_size) * int(max_pages) > MAX_DETAIL_ROWS:
        raise ValueError('CONTRACT_REFRESH_BOUNDS_INVALID')
    fetch_detail = fetch_detail or (lambda number, page, size: fetch_page(
        DETAIL, unified_no=number, page=page, rows=size))
    all_rows = []
    expected = None
    for page in range(1, int(max_pages) + 1):
        rows, total = fetch_detail(key, page, int(page_size))
        if (not isinstance(rows, list) or len(rows) > int(page_size)
                or isinstance(total, bool) or not isinstance(total, int) or total < 0):
            raise ContractResponseError('INVALID_CONTRACT_DETAIL_PAGE')
        if expected is None:
            expected = total
        if total != expected:
            raise ContractResponseError('CONTRACT_DETAIL_TOTAL_CHANGED')
        if expected > int(page_size) * int(max_pages):
            raise ContractResponseError('CONTRACT_DETAIL_BOUND_EXCEEDED')
        all_rows.extend(rows)
        if len(all_rows) > expected:
            raise ContractResponseError('CONTRACT_DETAIL_TOTAL_EXCEEDED')
        if len(all_rows) == expected:
            return normalize_snapshot(header, all_rows, complete=True, expected_count=expected)
        if len(rows) < int(page_size):
            raise ContractResponseError('CONTRACT_DETAIL_EARLY_END')
    raise ContractResponseError('CONTRACT_DETAIL_INCOMPLETE')
