import datetime as dt
import threading
import time

from budget_sync import get_lofin_key, sync_budget_snapshot
from db import get_setting, set_setting
from g2b_sync import ApiQuotaReached, api_usage, sync_bids_period, sync_services_period, sync_shopping_period

_started = False
_lock = threading.Lock()
_run_lock = threading.Lock()

AUTO_HOURS = 2
AUTO_DAYS = 14
AUTO_MAX_CATCHUP_DAYS = 30
AUTO_API_RESERVE = 100


def _truth(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _parse_dt(value):
    try:
        return dt.datetime.fromisoformat(str(value or ''))
    except Exception:
        return None


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
    # Only one automatic run may exist in the process. Manual/auto collision is
    # additionally coordinated through the persistent manual_sync_active flag.
    if not _run_lock.acquire(blocking=False):
        return False
    try:
        # Keep the operating policy fixed even if an old setting survived from a
        # previous release or was accidentally edited in the UI.
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

        success, reason = _run_source(
            '쇼핑몰', 'shop', sync_shopping_period,
            start.isoformat(), end.isoformat(), messages,
        )
        if not success:
            errors += 1
            quota_hits += 1 if reason == 'quota' else 0

        success, reason = _run_source(
            '물품입찰', 'bid', sync_bids_period,
            bid_start.isoformat(), end.isoformat(), messages,
        )
        if not success:
            errors += 1
            quota_hits += 1 if reason == 'quota' else 0

        success, reason = _run_source(
            '용역', 'bid', sync_services_period,
            bid_start.isoformat(), end.isoformat(), messages,
        )
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


def _run_budget_auto():
    # 지방재정365 예산은 일일 스냅샷 데이터이므로 하루 한 번만 수집한다.
    # 나라장터 2시간 자동수집과 별도 설정으로 유지한다.
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
        set_setting('scheduler_started_at', dt.datetime.now().isoformat(timespec='seconds'))
        threading.Thread(target=_worker, name='g2b-auto-sync', daemon=True).start()
