import datetime as dt
import threading
import time

from budget_sync import get_lofin_key, sync_budget_snapshot
from db import get_setting, set_setting
from g2b_sync import ApiQuotaReached, api_usage, sync_bids_period, sync_services_period, sync_shopping_period

_started = False
_lock = threading.Lock()
_run_lock = threading.Lock()
_history_lock = threading.Lock()

AUTO_HOURS = 2
AUTO_DAYS = 14
AUTO_MAX_CATCHUP_DAYS = 30
AUTO_API_RESERVE = 100

HISTORY_START = dt.date(2025, 1, 1)
HISTORY_CHUNK_DAYS = 7
HISTORY_API_RESERVE = 200
HISTORY_RETRY_MINUTES = 30
HISTORY_INIT_MARKER = 'v230_history_inplace_initialized'
_HISTORY_SOURCES = ('shop', 'bids', 'services')
_HISTORY_LABELS = {'shop': '쇼핑몰', 'bids': '물품입찰', 'services': '용역'}
_HISTORY_KINDS = {'shop': 'shop', 'bids': 'bid', 'services': 'bid'}
_HISTORY_ACTIVE = {'준비', '실행중', '재개대기', '자동수집 대기', '수동수집 대기', '호출한도 대기', '오류대기'}


def _truth(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _parse_dt(value):
    try:
        return dt.datetime.fromisoformat(str(value or ''))
    except Exception:
        return None


def _parse_date(value, default=None):
    try:
        return dt.date.fromisoformat(str(value or '')[:10])
    except Exception:
        return default


def _int_setting(key, default=0):
    try:
        return int(float(get_setting(key, str(default)) or default))
    except Exception:
        return int(default)


def _due(last_text, hours=AUTO_HOURS):
    last = _parse_dt(last_text)
    if not last:
        return True
    return (dt.datetime.now() - last).total_seconds() >= hours * 3600


def _next_due_text(base=None):
    base = base or dt.datetime.now()
    return (base + dt.timedelta(hours=AUTO_HOURS)).isoformat(timespec='seconds')


def _effective_days(today):
    """Use 14 days normally, but catch up after a longer server outage."""
    days = AUTO_DAYS
    last_success = _parse_dt(get_setting('last_auto_sync_success', ''))
    if last_success:
        gap = max(0, (today - last_success.date()).days)
        days = max(days, min(AUTO_MAX_CATCHUP_DAYS, gap + 2))
    return days


def _quota_ok(kind):
    used, limit = api_usage(kind)
    reserve = max(0, int(float(get_setting('auto_sync_api_reserve', str(AUTO_API_RESERVE)) or AUTO_API_RESERVE)))
    threshold = max(1, limit - reserve)
    return used < threshold, used, limit, reserve


def _set_source(source):
    set_setting('last_auto_sync_current_source', source)
    set_setting('scheduler_heartbeat', dt.datetime.now().isoformat(timespec='seconds'))


def _run_source(label, kind, func, start_date, end_date, messages):
    ok, used, limit, reserve = _quota_ok(kind)
    if not ok:
        messages.append(f'{label} 건너뜀: API {used:,}/{limit:,}회 · 안전여유 {reserve:,}회 유지')
        return False, 'quota'

    _set_source(label)
    try:
        n = func(start_date, end_date)
        messages.append(f'{label} {n:,}건 저장·갱신')
        return True, ''
    except ApiQuotaReached as exc:
        messages.append(f'{label} 호출한도: {exc}')
        return False, 'quota'
    except Exception as exc:
        messages.append(f'{label} 오류: {exc}')
        return False, 'error'


def _run_procurement_auto():
    if not _run_lock.acquire(blocking=False):
        return False
    try:
        if get_setting('auto_sync_hours', '') != str(AUTO_HOURS):
            set_setting('auto_sync_hours', str(AUTO_HOURS))
        if get_setting('auto_sync_days', '') != str(AUTO_DAYS):
            set_setting('auto_sync_days', str(AUTO_DAYS))

        if not _due(get_setting('last_auto_sync', ''), AUTO_HOURS):
            last = _parse_dt(get_setting('last_auto_sync', ''))
            if last:
                set_setting('next_auto_sync_due', _next_due_text(last))
            return False

        if _truth(get_setting('manual_sync_active', '0')):
            set_setting('last_auto_sync_status', '수동수집 대기')
            set_setting('last_auto_sync_current_source', get_setting('manual_sync_source', '') or '수동수집')
            set_setting('next_auto_sync_due', '수동수집 종료 후 재확인')
            return False

        started = dt.datetime.now()
        set_setting('last_auto_sync_started', started.isoformat(timespec='seconds'))
        set_setting('last_auto_sync_status', '수집중')
        set_setting('last_auto_sync_current_source', '준비')
        set_setting('scheduler_heartbeat', started.isoformat(timespec='seconds'))

        end = dt.date.today()
        effective_days = _effective_days(end)
        start = end - dt.timedelta(days=effective_days - 1)
        bid_start = max(start, end - dt.timedelta(days=27))
        set_setting('last_auto_sync_effective_days', str(effective_days))

        messages = []
        errors = 0
        quota_hits = 0

        success, reason = _run_source('쇼핑몰', 'shop', sync_shopping_period, start.isoformat(), end.isoformat(), messages)
        if not success:
            errors += 1
            quota_hits += 1 if reason == 'quota' else 0

        success, reason = _run_source('물품입찰', 'bid', sync_bids_period, bid_start.isoformat(), end.isoformat(), messages)
        if not success:
            errors += 1
            quota_hits += 1 if reason == 'quota' else 0

        success, reason = _run_source('용역', 'bid', sync_services_period, bid_start.isoformat(), end.isoformat(), messages)
        if not success:
            errors += 1
            quota_hits += 1 if reason == 'quota' else 0

        finished = dt.datetime.now()
        duration = max(0, int((finished - started).total_seconds()))
        set_setting('last_auto_sync', finished.isoformat(timespec='seconds'))
        set_setting('last_auto_sync_finished', finished.isoformat(timespec='seconds'))
        set_setting('last_auto_sync_duration_sec', str(duration))
        set_setting('last_auto_sync_current_source', '')
        set_setting('next_auto_sync_due', _next_due_text(finished))
        set_setting('scheduler_heartbeat', finished.isoformat(timespec='seconds'))

        if errors == 0:
            status = '완료'
            failures = 0
            set_setting('last_auto_sync_success', finished.isoformat(timespec='seconds'))
        else:
            status = '한도대기' if quota_hits == 3 else '부분오류'
            try:
                failures = int(get_setting('last_auto_sync_consecutive_failures', '0') or 0) + 1
            except Exception:
                failures = 1

        set_setting('last_auto_sync_status', status)
        set_setting('last_auto_sync_consecutive_failures', str(failures))
        set_setting('last_auto_sync_result', '자동수집 · ' + ' / '.join(messages))
        return True
    finally:
        _run_lock.release()


def initialize_history_backfill():
    """Initialize 2025 history state inside the already-deployed scheduler module."""
    if get_setting(HISTORY_INIT_MARKER, '') != '1':
        set_setting('backfill_start_date', HISTORY_START.isoformat())
        set_setting('backfill_chunk_days', str(HISTORY_CHUNK_DAYS))
        set_setting('backfill_api_reserve', str(HISTORY_API_RESERVE))
        if get_setting('backfill_status', '') in ('', '비활성'):
            set_setting('backfill_status', '대기')
            set_setting('backfill_progress', '0')
            set_setting('backfill_message', '2025-01-01부터 현재까지 과거자료 구축 대기')
        if not get_setting('backfill_auto_resume', ''):
            set_setting('backfill_auto_resume', '0')
        set_setting(HISTORY_INIT_MARKER, '1')

    if get_setting('backfill_auto_resume', '0') == '1' and get_setting('backfill_status', '') in _HISTORY_ACTIVE:
        set_setting('backfill_status', '재개대기')
        set_setting('backfill_current_source', '')
        set_setting('backfill_message', 'Cafe24 재시작 후 저장된 체크포인트에서 과거자료 구축을 재개합니다.')
    return True


def _history_reset(target_end):
    set_setting('backfill_target_end', target_end.isoformat())
    set_setting('backfill_cursor_date', HISTORY_START.isoformat())
    set_setting('backfill_cursor_source', 'shop')
    set_setting('backfill_progress', '0')
    set_setting('backfill_saved_shop', '0')
    set_setting('backfill_saved_bids', '0')
    set_setting('backfill_saved_services', '0')
    set_setting('backfill_started_at', '')
    set_setting('backfill_finished_at', '')
    set_setting('backfill_last_chunk', '')
    set_setting('backfill_last_error', '')
    set_setting('backfill_retry_after', '')


def start_history_backfill():
    """Called by the existing /backfill button; scheduler worker performs the work."""
    initialize_history_backfill()
    if not get_setting('api_key'):
        set_setting('backfill_message', '공공데이터포털 서비스키가 없어 과거자료 구축을 시작할 수 없습니다.')
        return False

    status = get_setting('backfill_status', '')
    if get_setting('backfill_auto_resume', '0') == '1' and status in _HISTORY_ACTIVE:
        return False

    target = dt.date.today()
    cursor = _parse_date(get_setting('backfill_cursor_date', ''), None)
    saved_target = _parse_date(get_setting('backfill_target_end', ''), None)
    if status == '완료' or cursor is None or saved_target is None:
        _history_reset(target)
    elif saved_target < target:
        set_setting('backfill_target_end', target.isoformat())

    set_setting('backfill_auto_resume', '1')
    set_setting('backfill_status', '준비')
    set_setting('backfill_message', '2025-01-01 과거자료 구축을 시작/재개합니다. 2시간 자동수집이 항상 우선입니다.')
    return True


def _history_progress(cursor, source, target):
    if cursor > target:
        return '100'
    total_days = max(1, (target - HISTORY_START).days + 1)
    completed_days = max(0, (cursor - HISTORY_START).days)
    source_fraction = _HISTORY_SOURCES.index(source) / len(_HISTORY_SOURCES)
    value = min(99.9, (completed_days + source_fraction) * 100.0 / total_days)
    return f'{max(0.0, value):.1f}'


def _history_quota_ready(kind):
    used, limit = api_usage(kind)
    reserve = max(100, _int_setting('backfill_api_reserve', HISTORY_API_RESERVE))
    return used < max(1, limit - reserve), used, limit, reserve


def _history_auto_priority():
    if not _truth(get_setting('auto_sync_enabled', '1')):
        return False
    if get_setting('last_auto_sync_status', '') == '수집중':
        return True
    if _due(get_setting('last_auto_sync', ''), AUTO_HOURS):
        return True
    next_due = _parse_dt(get_setting('next_auto_sync_due', ''))
    return bool(next_due and next_due <= dt.datetime.now() + dt.timedelta(minutes=10))


def _history_run_with_reserve(func, start_date, end_date, reserve):
    """Temporarily lower the hard API ceiling so history can never consume live reserve."""
    original = max(1, _int_setting('api_daily_limit', 900))
    temporary = max(1, original - max(0, reserve))
    set_setting('api_daily_limit', str(temporary))
    try:
        return func(start_date, end_date)
    finally:
        set_setting('api_daily_limit', str(original))


def _run_history_step():
    if get_setting('backfill_auto_resume', '0') != '1':
        return False
    if not _history_lock.acquire(blocking=False):
        return False
    try:
        retry_after = _parse_dt(get_setting('backfill_retry_after', ''))
        if retry_after and retry_after > dt.datetime.now():
            return False

        if _truth(get_setting('manual_sync_active', '0')):
            set_setting('backfill_status', '수동수집 대기')
            set_setting('backfill_message', '수동수집 완료 후 과거자료 구축을 이어갑니다.')
            return False

        if _history_auto_priority():
            set_setting('backfill_status', '자동수집 대기')
            set_setting('backfill_message', '2시간 자동수집이 우선입니다. 자동수집 완료 후 과거자료를 이어갑니다.')
            return False

        target = _parse_date(get_setting('backfill_target_end', ''), dt.date.today())
        cursor = _parse_date(get_setting('backfill_cursor_date', ''), HISTORY_START)
        source = get_setting('backfill_cursor_source', 'shop')
        if source not in _HISTORY_SOURCES:
            source = 'shop'

        if cursor > target:
            set_setting('backfill_status', '완료')
            set_setting('backfill_progress', '100')
            set_setting('backfill_auto_resume', '0')
            set_setting('backfill_finished_at', dt.datetime.now().isoformat(timespec='seconds'))
            set_setting('backfill_current_source', '')
            set_setting('backfill_message', f'2025-01-01 ~ {target.isoformat()} 과거자료 구축 완료')
            return True

        kind = _HISTORY_KINDS[source]
        ready, used, limit, reserve = _history_quota_ready(kind)
        if not ready:
            set_setting('backfill_status', '호출한도 대기')
            set_setting('backfill_message', f'{kind.upper()} API {used:,}/{limit:,}회 · 실시간 자동수집용 {reserve:,}회 여유 유지. 다음 날 자동 재개합니다.')
            return False

        chunk_end = min(cursor + dt.timedelta(days=HISTORY_CHUNK_DAYS - 1), target)
        label = _HISTORY_LABELS[source]
        if not get_setting('backfill_started_at', ''):
            set_setting('backfill_started_at', dt.datetime.now().isoformat(timespec='seconds'))
        set_setting('backfill_status', '실행중')
        set_setting('backfill_current_source', label)
        set_setting('backfill_last_chunk', f'{cursor.isoformat()} ~ {chunk_end.isoformat()} · {label}')
        set_setting('backfill_progress', _history_progress(cursor, source, target))
        set_setting('backfill_message', f'{cursor.isoformat()} ~ {chunk_end.isoformat()} · {label} 과거자료 수집 중')

        func = {'shop': sync_shopping_period, 'bids': sync_bids_period, 'services': sync_services_period}[source]
        try:
            saved = _history_run_with_reserve(func, cursor.isoformat(), chunk_end.isoformat(), reserve)
        except ApiQuotaReached as exc:
            set_setting('backfill_status', '호출한도 대기')
            set_setting('backfill_current_source', '')
            set_setting('backfill_message', f'{exc} · 실시간 자동수집 여유를 남기고 다음 날 재개합니다.')
            return False
        except Exception as exc:
            retry = dt.datetime.now() + dt.timedelta(minutes=HISTORY_RETRY_MINUTES)
            set_setting('backfill_status', '오류대기')
            set_setting('backfill_current_source', '')
            set_setting('backfill_last_error', str(exc))
            set_setting('backfill_retry_after', retry.isoformat(timespec='seconds'))
            set_setting('backfill_message', f'{label} 과거자료 오류: {exc} · {HISTORY_RETRY_MINUTES}분 후 같은 구간 재시도')
            return False

        key = {'shop': 'backfill_saved_shop', 'bids': 'backfill_saved_bids', 'services': 'backfill_saved_services'}[source]
        set_setting(key, str(_int_setting(key, 0) + int(saved or 0)))
        set_setting('backfill_retry_after', '')
        set_setting('backfill_last_error', '')

        if source == 'shop':
            next_cursor, next_source = cursor, 'bids'
        elif source == 'bids':
            next_cursor, next_source = cursor, 'services'
        else:
            next_cursor, next_source = chunk_end + dt.timedelta(days=1), 'shop'

        set_setting('backfill_cursor_date', next_cursor.isoformat())
        set_setting('backfill_cursor_source', next_source)
        set_setting('backfill_progress', _history_progress(next_cursor, next_source, target) if next_cursor <= target else '100')
        set_setting('backfill_current_source', '')
        set_setting('backfill_status', '준비' if next_cursor <= target else '완료')
        set_setting('backfill_message', f'{label} {cursor.isoformat()} ~ {chunk_end.isoformat()} 완료 · {int(saved or 0):,}건 저장·갱신')

        if next_cursor > target:
            set_setting('backfill_auto_resume', '0')
            set_setting('backfill_finished_at', dt.datetime.now().isoformat(timespec='seconds'))
            set_setting('backfill_message', f'2025-01-01 ~ {target.isoformat()} 과거자료 구축 완료')
        return True
    finally:
        _history_lock.release()


def _run_budget_auto():
    today = dt.date.today()
    today_text = today.isoformat()
    if not _truth(get_setting('budget_auto_sync_enabled', '0')) or not get_lofin_key():
        return
    if get_setting('last_budget_auto_attempt_date', '') == today_text:
        return

    set_setting('last_budget_auto_attempt_date', today_text)
    set_setting('budget_sync_status', '수집중')
    try:
        sync_budget_snapshot(today.year, today_text)
        set_setting('budget_sync_status', '완료')
    except Exception as exc:
        set_setting('budget_sync_status', '오류')
        set_setting('last_budget_sync_result', f'예산 자동수집 실패: {exc}')


def _worker():
    while True:
        try:
            set_setting('scheduler_heartbeat', dt.datetime.now().isoformat(timespec='seconds'))
            if _truth(get_setting('auto_sync_enabled', '0')) and get_setting('api_key'):
                _run_procurement_auto()
            _run_budget_auto()
            if get_setting('api_key'):
                _run_history_step()
        except Exception as exc:
            set_setting('last_auto_sync_status', '오류')
            set_setting('last_auto_sync_current_source', '')
            set_setting('last_auto_sync_result', f'자동수집 스케줄러 오류: {exc}')
        time.sleep(60)


def start_scheduler():
    global _started
    with _lock:
        if _started:
            return
        _started = True
        initialize_history_backfill()
        set_setting('scheduler_started_at', dt.datetime.now().isoformat(timespec='seconds'))
        threading.Thread(target=_worker, name='g2b-auto-sync', daemon=True).start()
