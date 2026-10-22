"""One bounded smart-scope replay slice inside the existing shopping worker.

Replays the *complete* shopping source (not a keyword-filtered response), retaining
existing active-state reconciliation. Separate receipts avoid resetting January
or other normal collection checkpoints. Never starts a thread/process.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import shopping_vnext
from db import get_setting, set_setting
from vnext_source_guard import operational_recent_source_context
from smartled_vnext import START_DATE

CURSOR_KEY = 'smartled_shopping_replay_next_date_v1'


def collect_next_day(*, today=None):
    today = today or dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    latest = today - dt.timedelta(days=1)
    raw = str(get_setting(CURSOR_KEY, START_DATE) or START_DATE)
    try:
        day = max(dt.date.fromisoformat(START_DATE), dt.date.fromisoformat(raw))
    except ValueError:
        raise ValueError('SMARTLED_REPLAY_CURSOR_INVALID') from None
    recheck = day > latest
    if recheck:
        # After the initial baseline, rotate a bounded seven-day overlap for
        # corrections. Never restart an unfinished page; only completed receipts
        # may start a new reconciliation generation.
        floor = max(dt.date.fromisoformat(START_DATE), latest - dt.timedelta(days=6))
        saved = str(get_setting('smartled_shopping_recheck_next_date_v1', floor.isoformat()))
        day = dt.date.fromisoformat(saved)
        if day < floor or day > latest:
            day = floor
    iso = day.isoformat()
    resume = True
    if recheck:
        from vnext_store import get_checkpoint
        cp = get_checkpoint('shopping_delivery', f'smartled-v1:{iso}:{iso}', schema_prepared=True)
        resume = not cp or str(cp.get('status') or '') != 'COMPLETE' 
    # Two 250-row pages per worker cycle; durable partial checkpoint resumes.
    # The shared shopping quota/memory guard continues to apply.
    with operational_recent_source_context(collection_date=iso, max_requests=4):
        result = shopping_vnext.collect_all(iso, iso, page_size=250, max_pages=2,
            resume=resume, smartled_replay=True)
    if result.get('complete'):
        set_setting('smartled_shopping_recheck_next_date_v1' if recheck else CURSOR_KEY, (day + dt.timedelta(days=1)).isoformat())
    return {'status': 'COMPLETE' if result.get('complete') else 'PARTIAL',
            'date': iso, 'source_kind': 'SHOPPING_DELIVERY', **result}


def run_slice():
    """Keep separate truthful smart-scope state; never mask ordinary results."""
    from smartled_contract_collection import OPERATIONAL_HOLDS
    try:
        result = collect_next_day()
    except OPERATIONAL_HOLDS:
        raise
    except Exception as exc:
        # Preserve the cursor/partial receipts. Do not log URLs, keys or payloads.
        result = {'status': 'UNVERIFIED', 'error_type': type(exc).__name__}
    set_setting('smartled_shopping_replay_status', str(result.get('status', 'UNVERIFIED')))
    set_setting('smartled_shopping_replay_last_date', str(result.get('date', '')))
    return result
