"""SmartLED view of agency shopping orders; no network or DDL on HTTP reads.

Official classification: PPS notice 2504180012 (2025-04-18).
General goods contracts are a different source and are not represented as covered.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from db import backend_name, connect
from procurement_read_vnext import _shopping_filter_parts, _normalize_shopping_output

START_DATE = '2026-10-01'
CLASSIFICATION_URL = 'https://www.pps.go.kr/kor/bbs/view.do?bbsSn=2504180012&key=00641'
SOURCE_URL = 'https://www.data.go.kr/data/15129471/openapi.do'
GENERAL_CONTRACT_SOURCE_URL = 'https://www.data.go.kr/data/15129427/openapi.do'
CODES = {
    '3911980101': ('LAMP', '스마트LED램프'),
    '3911980201': ('FIXTURE', '스마트LED다운라이트'),
    '3911980202': ('FIXTURE', '스마트LED실내조명등'),
    '3911980301': ('FIXTURE', '스마트LED가로등기구'),
    '3911980302': ('FIXTURE', '스마트LED보안등기구'),
    '3911980303': ('FIXTURE', '스마트LED투광등기구'),
    '3911980304': ('FIXTURE', '스마트LED터널등기구'),
    '3911980401': ('CONTROL', '스마트LED조명제어시스템'),
}
KINDS = {'FIXTURE': '등기구', 'LAMP': '램프', 'CONTROL': '제어시스템'}


def date_range(start='', end='', *, today=None):
    today = today or dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    start = dt.date.fromisoformat(str(start or START_DATE)).isoformat()
    end = dt.date.fromisoformat(str(end or today.isoformat())).isoformat()
    if start < START_DATE or end < start or end > today.isoformat():
        raise ValueError('SMARTLED_DATE_RANGE_INVALID')
    return start, end


def shopping_rows(*, start_date=START_DATE, end_date='', query='', region='',
                  kind='FIXTURE', limit=100, offset=0):
    """Exact code matching, bounded SQL page. Never classify a title as evidence.

    The date is source delivery-request receipt date, not contract signature date.
    Separate kinds prevent control-system totals being reported as fixture sales.
    """
    start, end = date_range(start_date, end_date)
    if kind not in KINDS:
        raise ValueError('SMARTLED_KIND_INVALID')
    limit = max(1, min(int(limit), 200))
    offset = int(offset)
    if offset < 0 or offset > 50000:
        raise ValueError('SMARTLED_PAGE_LIMIT')
    where, params = _shopping_filter_parts(categories=({'FIXTURE': 'LIGHTING', 'LAMP': 'SMART_LAMP', 'CONTROL': 'SMART_CONTROL'}[kind],), query=query,
        region=region, start_date=start, end_date=end)
    if not where:
        return {'rows': [], 'has_more': False, 'kind': kind, 'start_date': start, 'end_date': end}
    codes = [code for code, (group, _) in CODES.items() if group == kind]
    where.append("UPPER(COALESCE(is_final_delivery_request,'')) NOT IN ('N','NO','0')")
    where.append('detail_item_no IN (' + ','.join('?' for _ in codes) + ')')
    params.extend(codes)
    params.extend([limit + 1, offset])
    with connect() as conn:
        if backend_name() == 'POSTGRESQL':
            conn.execute("SELECT set_config('statement_timeout', ?, true), "
                         "set_config('work_mem', '4MB', true)", ('3500ms',))
        rows = conn.execute('SELECT * FROM shopping_records WHERE ' + ' AND '.join(where)
            + ' ORDER BY source_date DESC,updated_at DESC,source_key DESC LIMIT ? OFFSET ?',
            tuple(params)).fetchall()
    more = len(rows) > limit
    result = _normalize_shopping_output(rows[:limit])
    for row in result:
        row['smartled_kind'], row['smartled_name'] = CODES[row['detail_item_no']]
        row['smartled_evidence'] = '조달청 세부품명번호 ' + row['detail_item_no']
    return {'rows': result, 'has_more': more, 'kind': kind, 'start_date': start, 'end_date': end}
