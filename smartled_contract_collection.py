"""Bounded smartLED goods-contract work in the already admitted shopping worker.

No thread/process, no broad unfiltered contract crawl. Product-name discovery is
explicitly partial coverage; complete detail snapshots prove actual smart items.
"""
import datetime as dt
import json
from zoneinfo import ZoneInfo

from db import get_setting, set_setting
import smartled_contract_vnext as api
import smartled_contract_store_vnext as store
from vnext_source_guard import smartled_contract_source_context

from memory_guard import MemoryPressureError
from vnext_http import VNextQuotaReached, VNextRateLimited
from vnext_source_guard import VNextSourceAccessError, VNextSourceTransportAttestationError
from g2b_dynamic_source_budget import GateStateUnavailable
OPERATIONAL_HOLDS = (MemoryPressureError, VNextQuotaReached, VNextRateLimited,
    VNextSourceAccessError, VNextSourceTransportAttestationError, GateStateUnavailable)

PREFIX = 'smartled_contract_'


def _cursor(name):
    raw = get_setting(PREFIX + name, '')
    if not raw:
        return {'day': '2026-10-01', 'page': 1}
    value = json.loads(raw)
    day = dt.date.fromisoformat(value['day'])
    if day < api.START_DATE or not 1 <= int(value['page']) <= 100000:
        raise ValueError('SMARTLED_CONTRACT_CURSOR_INVALID')
    return value


def _next_cursor(state, rows, total):
    page = int(state['page'])
    if not isinstance(total, int) or isinstance(total, bool) or total < 0 or len(rows) > 100:
        raise api.ContractResponseError('CONTRACT_SCAN_PAGE_INVALID')
    if 'total' in state and state['total'] != total:
        return json.dumps({'day': state['day'], 'page': 1}, separators=(',', ':'))
    fetched = (page - 1) * 100 + len(rows)
    if fetched > total or (len(rows) < 100 and fetched < total):
        raise api.ContractResponseError('CONTRACT_SCAN_COUNT_MISMATCH')
    if fetched == total:
        next_day = dt.date.fromisoformat(state['day']) + dt.timedelta(days=1)
        value = {'day': next_day.isoformat(), 'page': 1}
    else:
        value = {**state, 'page': page + 1, 'total': total}
    return json.dumps(value, separators=(',', ':'))


def _scan(name, operation, latest):
    latest_name = name + '_latest'
    latest_text = latest.isoformat()
    recent = (_cursor(latest_name) if get_setting(PREFIX + latest_name, '')
              else {'day': latest_text, 'page': 1})
    # Preserve an unfinished date across midnight. Resetting it to each new D-1
    # would silently skip all pages after the first daily request allowance.
    if recent['day'] <= latest_text:
        name, state = latest_name, recent
    else:
        state = _cursor(name)
    if state['day'] > latest.isoformat():
        # A completed sweep is rechecked, never advertised as entire G2B coverage.
        state = {'day': '2026-10-01', 'page': 1}
    with smartled_contract_source_context(collection_date=state['day'], max_requests=1):
        rows, total = api.fetch_page(operation, start_date=state['day'], end_date=state['day'],
            page=state['page'], rows=100, product_name='스마트LED' if operation == api.SEARCH else '')
    if not isinstance(rows, list) or len(rows) > 100:
        raise api.ContractResponseError('CONTRACT_SCAN_PAGE_INVALID')
    keys = [api.contract_key(row) for row in rows]
    cursor = _next_cursor(state, rows, total)
    if operation == api.DELETIONS:
        for row in rows:
            event = api.deletion_event(row)
            store.mark_source_deleted(event)
    elif operation == api.CHANGES:
        # Unknown changed contracts are discovered by the repeated product-name
        # sweep. Known matches must be refreshed even when smart wording vanishes.
        store.enqueue_page_and_cursor([key for key in keys if store.contains(key)], PREFIX + name, cursor, reason='CHANGE')
    else:
        store.enqueue_page_and_cursor(keys, PREFIX + name, cursor, reason='NEW')
    if operation == api.DELETIONS:
        set_setting(PREFIX + name, cursor)
    return {'date': state['day'], 'page': state['page'], 'observed': len(rows)}


def _header(key):
    rows, total = api.fetch_page(api.SUMMARY, unified_no=key, rows=100)
    if total != 1 or len(rows) != 1 or api.contract_key(rows[0]) != key:
        raise api.ContractResponseError('CONTRACT_CURRENT_HEADER_AMBIGUOUS')
    return rows[0]


def _refresh_one(latest):
    job = store.next_pending()
    if not job:
        return {'status': 'IDLE'}
    key = job['unified_no']
    try:
        with smartled_contract_source_context(collection_date=latest.isoformat(), unified_no=key, max_requests=6):
            before = _header(key)
            before_start = api._day(str(before.get('cntrctCnclsDate',''))) < api.START_DATE
            snapshot = None if before_start else api.refresh_snapshot(before, page_size=100, max_pages=4)
            after = _header(key)
            if before != after:
                raise api.ContractResponseError('CONTRACT_CHANGED_DURING_REFRESH')
        if before_start:
            store.mark_out_of_scope(key, queue_revision=job['revision'])
        else:
            store.save_snapshot(snapshot, queue_revision=job['revision'])
        return {'status': 'SAVED', 'contract_no': key}
    except OPERATIONAL_HOLDS:
        raise
    except Exception as exc:
        store.mark_failed(key, type(exc).__name__, queue_revision=job['revision'])
        return {'status': 'UNVERIFIED', 'error_type': type(exc).__name__}


def run_slice(*, today=None):
    """At most 3 discovery/history requests + 6 single-contract refresh requests."""
    today = today or dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    latest = today - dt.timedelta(days=1)
    if latest < api.START_DATE:
        return {'status': 'NOT_STARTED'}
    try:
        # Drain first so a full queue can resume rather than deadlocking discovery.
        refreshed = _refresh_one(latest)
        if refreshed.get('status') == 'UNVERIFIED':
            set_setting(PREFIX + 'status', 'UNVERIFIED')
            return refreshed
        scans = {}
        for name, operation in (('search_cursor',api.SEARCH), ('changes_cursor',api.CHANGES), ('deletions_cursor',api.DELETIONS)):
            scans[name] = _scan(name, operation, latest)
        result = {'status': 'PARTIAL_COVERAGE', 'refresh': refreshed, 'scans': scans}
    except OPERATIONAL_HOLDS:
        raise
    except Exception as exc:
        result = {'status': 'UNVERIFIED', 'error_type': type(exc).__name__}
    set_setting(PREFIX + 'status', result['status'])
    return result
