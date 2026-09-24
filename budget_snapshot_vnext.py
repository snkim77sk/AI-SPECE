"""Explicit LOFIN/QWGJK snapshot planning, audit and bounded sanitized canary.

The first live budget validation is one recent snapshot with a hard page budget.
Wider live snapshot collection additionally requires successful recent small-
validation evidence from the same runtime commit and an approved-historical source
execution context. That wider context is intentionally unavailable while bulk HOLD
is active.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import budget_vnext
import lofin_vnext_http
import vnext_stability
from vnext_collection import verified_checkpoint
from vnext_live_gate import require_canary_approval, require_small_validation_approval
from vnext_source_guard import (
    APPROVED_HISTORICAL,
    SMALL_VALIDATION,
    require_source_request_mode,
)
from vnext_store import get_checkpoint

MAX_VALIDATION_PAGES = 2


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
        dataset='budget', scope=unit['scope'],
        fetch=lambda page, size: budget_vnext.fetch_page(
            year, snapshot, page=page, size=size
        ),
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
        fresh = vnext_stability.stability_fresh_checkpoint(cp)
        records.append({**unit,
                        'status': cp['status'] if cp else 'NOT_STARTED',
                        'receipt_complete': receipt_complete,
                        'stability_verified': stable,
                        'stability_fresh': fresh,
                        'stability_verified_at_utc': vnext_stability.stability_verified_at(cp),
                        'complete': fresh,
                        'fetched_count': cp['fetched_count'] if cp else 0,
                        'source_total': cp['source_total'] if cp and cp['source_total'] >= 0 else None})
    return {**plan, 'records': records,
            'all_receipts_complete': all(row['receipt_complete'] for row in records),
            'all_stability_verified': all(row['stability_verified'] for row in records),
            'all_requested_snapshots_complete': all(row['complete'] for row in records)}


def run_snapshots(snapshot_dates, *, allow_live=False, canary_approval=None,
                  small_validation_approval=None, validation_mode=False,
                  page_size=1000, max_pages_per_snapshot=1):
    """Collect each requested snapshot, then replay-verify its receipt pages."""
    if allow_live is not True:
        raise RuntimeError('budget live collection locked until canary verification')
    canary = require_canary_approval(canary_approval)
    plan = build_snapshot_plan(snapshot_dates)
    pages = int(max_pages_per_snapshot)
    if pages < 1:
        raise ValueError('explicit positive page budget required')
    if validation_mode:
        if len(plan['scopes']) != 1 or pages > MAX_VALIDATION_PAGES:
            raise RuntimeError('SMALL_VALIDATION_BOUNDS_REQUIRED')
        require_source_request_mode(SMALL_VALIDATION)
        expansion = {'mode': 'small_validation', 'max_pages_per_snapshot': pages}
    else:
        expansion = require_small_validation_approval(small_validation_approval)
        require_source_request_mode(APPROVED_HISTORICAL)

    results = []
    for unit in plan['scopes']:
        before = audit_snapshots([unit['snapshot_date']])['records'][0]
        if before['complete']:
            results.append({**before, 'action': 'SKIPPED_FRESH_STABLE_COMPLETE'})
            continue
        cp = get_checkpoint('budget', unit['scope'])
        if verified_checkpoint(cp):
            stability = _verify_budget_unit(unit)
            after = audit_snapshots([unit['snapshot_date']])['records'][0]
            results.append({**after, 'action': 'VERIFIED_SOURCE_STABILITY',
                            'stability': stability})
            if not after['complete']:
                break
            continue
        result = budget_vnext.collect_full_budget(
            unit['fiscal_year'], unit['snapshot_date'],
            page_size=page_size, max_pages=pages, resume=True,
        )
        cp = get_checkpoint('budget', unit['scope'])
        stability = _verify_budget_unit(unit) if verified_checkpoint(cp) else None
        after = audit_snapshots([unit['snapshot_date']])['records'][0]
        results.append({**after, 'action': 'COLLECTED', 'collector_result': result,
                        'stability': stability})
        if not after['complete']:
            break
    return {'approval': canary, 'expansion_approval': expansion,
            'results': results, 'audit': audit_snapshots(snapshot_dates)}


def run_budget_canary(*, snapshot_date=None, rows=10):
    """One logical page and one HTTP attempt; no RAW or serving-table writes."""
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
        return {**report, 'status': 'ERROR', 'reason': type(exc).__name__}
