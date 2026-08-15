import datetime as dt
import threading
import time

from budget_sync import get_lofin_key, sync_budget_snapshot
from db import get_setting, set_setting
from g2b_sync import ApiQuotaReached, sync_bids_period, sync_services_period, sync_shopping_period

_started = False
_lock = threading.Lock()

AUTO_DEFAULT_HOURS = 2
AUTO_DEFAULT_DAYS = 14


def _truth(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _due(last_text, hours):
    if not last_text:
        return True
    try:
        last = dt.datetime.fromisoformat(last_text)
        return (dt.datetime.now() - last).total_seconds() >= hours * 3600
    except Exception:
        return True


def _run_procurement_auto():
    hours = max(1, int(float(get_setting('auto_sync_hours', str(AUTO_DEFAULT_HOURS)) or AUTO_DEFAULT_HOURS)))
    days = max(1, min(90, int(float(get_setting('auto_sync_days', str(AUTO_DEFAULT_DAYS)) or AUTO_DEFAULT_DAYS))))
    if not _due(get_setting('last_auto_sync', ''), hours):
        return False

    started = dt.datetime.now()
    set_setting('last_auto_sync_started', started.isoformat(timespec='seconds'))
    set_setting('last_auto_sync_status', '수집중')

    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    # BidPublicInfoService is queried in at most a 28-day window.
    bid_start = max(start, end - dt.timedelta(days=27))

    messages = []
    errors = 0

    try:
        n = sync_shopping_period(start.isoformat(), end.isoformat())
        messages.append(f'쇼핑몰 {n:,}건 저장·갱신')
    except ApiQuotaReached as exc:
        errors += 1
        messages.append(f'쇼핑몰 호출한도: {exc}')
    except Exception as exc:
        errors += 1
        messages.append(f'쇼핑몰 오류: {exc}')

    try:
        n = sync_bids_period(bid_start.isoformat(), end.isoformat())
        messages.append(f'물품입찰 {n:,}건 저장·갱신')
    except ApiQuotaReached as exc:
        errors += 1
        messages.append(f'물품입찰 호출한도: {exc}')
    except Exception as exc:
        errors += 1
        messages.append(f'물품입찰 오류: {exc}')

    try:
        n = sync_services_period(bid_start.isoformat(), end.isoformat())
        messages.append(f'용역 {n:,}건 저장·갱신')
    except ApiQuotaReached as exc:
        errors += 1
        messages.append(f'용역 호출한도: {exc}')
    except Exception as exc:
        errors += 1
        messages.append(f'용역 오류: {exc}')

    finished = dt.datetime.now()
    # Mark the attempt even on partial error so a failing API is not retried
    # every minute. The next normal retry is two hours later.
    set_setting('last_auto_sync', finished.isoformat(timespec='seconds'))
    set_setting('last_auto_sync_finished', finished.isoformat(timespec='seconds'))
    set_setting('last_auto_sync_status', '완료' if errors == 0 else '부분오류')
    set_setting('last_auto_sync_result', '자동수집 · ' + ' / '.join(messages))
    return True


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
            if _truth(get_setting('auto_sync_enabled', '0')) and get_setting('api_key'):
                _run_procurement_auto()
            _run_budget_auto()
        except Exception as exc:
            set_setting('last_auto_sync_status', '오류')
            set_setting('last_auto_sync_result', f'자동수집 스케줄러 오류: {exc}')
        time.sleep(60)


def start_scheduler():
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_worker, name='g2b-auto-sync', daemon=True).start()
