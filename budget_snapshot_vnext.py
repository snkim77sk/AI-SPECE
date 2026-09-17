"""Explicit LOFIN/QWGJK snapshot planning, audit and bounded sanitized canary.

This is not a claim to cover every national/local budget API or all historical
execution dates. Only the operator's exact QWGJK fiscal-year/date scopes are audited.
No business keyword is used. No production scheduler is connected here.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import budget_vnext
import lofin_vnext_http
import vnext_stability
from vnext_collection import verified_checkpoint
from vnext_live_gate import require_canary_approval
from vnext_store import get_checkpoint


def build_snapshot_plan(snapshot_dates):
    dates = sorted({dt.date.fromisoformat(str(day)) for day in snapshot_dates})
    today = dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    if not dates or any(day > today for day in dates):
        raise ValueError('explicit nonempty past/present snapshot dates required')
    return {'dataset': 'budget', 'source': 'LOFIN/QWGJK', 'keyword_filter': None,
            'snapshot_count': len(dates), 'complete_historical_archive': False,
            'scopes': [{'fiscal_year': day.year, 'snapshot_date': day.isoformat(),
                        'scope': f'{day.year}:{day.isoformat()}'} for day in dates]}


def _verify_budget_unit(unit):
    year = int(unit['fiscal_year'])
    snapshot = str(unit['snapshot_date'])
    return vnext_stability.verify_checkpoint_source(
        dataset='budget',
        scope=unit['scope'],
        fetch=lambda page, size: budget_vnext.fetch_budget_page(
            year, snapshot, '', page=page, size=size
        )[:2],
        identity=lambda row: budget_vnext._source_key(row, year, snapshot),
        validate_row=lambda row: budget_vnext._scope_problem(row, year, snapshot),
    )


def audit_snapshots(snapshot_dates):
    plan = build_snapshot_plan(snapshot_dates)
    records = []
    for unit in plan['scopes']:
        cp = get_checkpoint('budget', unit['scope'])
        receipt_complete = verified_checkpoint(cp)
        stable = vnext_stability.stability_verified_checkpoint(cp)
        records.append({**unit,
                        'status': cp['status'] if cp else 'NOT_STARTED',
                        'receipt_complete': receipt_complete,
                        'stability_verified': stable,
                        'complete': stable,
                        'fetched_count': cp['fetched_count'] if cp else 0,
                        'source_total': cp['source_total'] if cp and cp['source_total'] >= 0 else None})
    return {**plan,
            'records': records,
            'all_receipts_complete': all(row['receipt_complete'] for row in records),
            'all_requested_snapshots_complete': all(row['complete'] for row in records)}


def run_snapshots(snapshot_dates, *, allow_live=False, canary_approval=None,
                  page_size=1000, max_pages_per_snapshot=1):
    """Collect each requested snapshot, then replay-verify its receipt pages.

    Live source traffic requires a recent sanitized bounded-canary approval in
    addition to ``allow_live=True``.
    """
    if allow_live is not True:
        raise RuntimeError('budget live collection locked until canary verification')
    approval = require_canary_approval(canary_approval)
    plan = build_snapshot_plan(snapshot_dates)
    if int(max_pages_per_snapshot) < 1:
        raise ValueError('explicit positive page budget required')
    results = []
    for unit in plan['scopes']:
        before = audit_snapshots([unit['snapshot_date']])['records'][0]
        if before['complete']:
            results.append({**before, 'action': 'SKIPPED_STABLE_COMPLETE'})
            continue

        result = budget_vnext.collect_full_budget(
            unit['fiscal_year'], unit['snapshot_date'],
            page_size=page_size, max_pages=int(max_pages_per_snapshot), resume=True,
        )
        cp = get_checkpoint('budget', unit['scope'])
        stability = _verify_budget_unit(unit) if verified_checkpoint(cp) else None
        after = audit_snapshots([unit['snapshot_date']])['records'][0]
        results.append({**after, 'action': 'COLLECTED', 'collector_result': result,
                        'stability': stability})
        if not after['complete']:
            break
    return {'approval': approval, 'results': results, 'audit': audit_snapshots(snapshot_dates)}


def run_budget_canary(*, snapshot_date=None, rows=10):
    """One logical page and one HTTP attempt; no RAW or serving-table writes.

    A parser-valid zero is NO_DATA, not schema PASS. Stats contain no source values.
    The standalone runner must supply a disposable DB for isolated quota metadata.
    """
    day = dt.date.fromisoformat(str(snapshot_date)) if snapshot_date else (
        dt.datetime.now(ZoneInfo('Asia/Seoul')).date() - dt.timedelta(days=1))
    build_snapshot_plan([day])
    report = {'source': 'LOFIN/QWGJK', 'fiscal_year': day.year,
              'snapshot_date': day.isoformat(), 'page': 1, 'page_size': min(max(int(rows), 1), 10),
              'live_request_attempted': False, 'schema_verified': False,
              'coverage_verified': False, 'scope': 'one page only; not whole-source completeness'}
    if not lofin_vnext_http.get_lofin_key():
        return {**report, 'status': 'BLOCKED', 'reason': 'LOFIN_API_KEY_NOT_CONFIGURED'}
    report['live_request_attempted'] = True
    try:
        items, total, code, _ = lofin_vnext_http.fetch_budget_page(
            day.year, day.isoformat(), '', page=1, size=report['page_size'], retries=1)
        fields = ('fyr', 'wa_laf_cd', 'laf_cd', 'dept_cd', 'dbiz_cd', 'acnt_dv_cd', 'dbiz_nm')
        counts = {field: sum(bool(str(item.get(field) or '').strip()) for item in items) for field in fields}
        missing = {field: len(items) - count for field, count in counts.items()}
        wrong_year = sum(str(item.get('fyr', day.year)).strip() != str(day.year) for item in items)
        verified = bool(items) and not any(missing.values()) and not wrong_year
        return {**report, 'status': 'SCHEMA_PASS' if verified else 'SCHEMA_MISMATCH' if items else 'NO_DATA',
                'schema_verified': verified, 'page_rows': len(items), 'source_total': total,
                'api_result_code': code, 'nonempty_field_counts': counts,
                'missing_field_counts': missing, 'wrong_fiscal_year_rows': wrong_year}
    except Exception as exc:
        # Never emit exception URL/body/key. Only class name and local status.
        return {**report, 'status': 'ERROR', 'reason': type(exc).__name__}
