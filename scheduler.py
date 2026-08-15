import datetime as dt
import threading
import time

from budget_sync import get_lofin_key, sync_budget_snapshot
from db import get_setting, set_setting
from g2b_sync import ApiQuotaReached, backfill_three_years, sync_bids_period, sync_services_period, sync_shopping_period

_started = False
_lock = threading.Lock()


def _truth(v):
    return str(v).lower() in ('1','true','yes','on')


def _worker():
    while True:
        try:
            if _truth(get_setting('auto_sync_enabled')) and get_setting('api_key'):
                hours = max(1, int(float(get_setting('auto_sync_hours','3') or 3)))
                days = max(1, min(90, int(float(get_setting('auto_sync_days','14') or 14))))
                last = get_setting('last_auto_sync')
                due = True
                if last:
                    try:
                        due = (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() >= hours*3600
                    except Exception:
                        due = True
                if due:
                    end = dt.date.today()
                    start = end - dt.timedelta(days=days)
                    messages = []
                    try:
                        n = sync_shopping_period(start.isoformat(), end.isoformat())
                        messages.append(f'쇼핑몰 {n:,}건')
                    except ApiQuotaReached as e:
                        messages.append(f'쇼핑몰 호출한도: {e}')
                    except Exception as e:
                        messages.append(f'쇼핑몰 오류: {e}')
                    bstart = max(start, end-dt.timedelta(days=27))
                    try:
                        n = sync_bids_period(bstart.isoformat(), end.isoformat())
                        messages.append(f'물품입찰 {n:,}건')
                    except ApiQuotaReached as e:
                        messages.append(f'입찰 호출한도: {e}')
                    except Exception as e:
                        messages.append(f'입찰 오류: {e}')
                    try:
                        n = sync_services_period(bstart.isoformat(), end.isoformat())
                        messages.append(f'용역 {n:,}건')
                    except ApiQuotaReached as e:
                        messages.append(f'용역 호출한도: {e}')
                    except Exception as e:
                        messages.append(f'용역 오류: {e}')
                    set_setting('last_auto_sync', dt.datetime.now().isoformat(timespec='seconds'))
                    set_setting('last_sync_result', '자동수집 · ' + ' / '.join(messages))

                # 3년 구축이 호출한도로 멈췄고 쇼핑몰 호출카운터 날짜가 오늘이 아니면 자동 재개
                if get_setting('backfill_status') == '호출한도 대기':
                    quota_day = get_setting('api_calls_shop_date', '')
                    if quota_day != dt.date.today().isoformat():
                        try:
                            backfill_three_years()
                        except Exception:
                            pass

            # 지방재정365 예산은 일일 스냅샷 데이터이므로 하루 한 번만 수집한다.
            # 나라장터 자동수집 설정과 분리되어 있으며 지방재정365 인증키가 있을 때만 실행한다.
            today = dt.date.today()
            today_text = today.isoformat()
            if _truth(get_setting('budget_auto_sync_enabled', '1')) and get_lofin_key():
                if get_setting('last_budget_auto_attempt_date', '') != today_text:
                    set_setting('last_budget_auto_attempt_date', today_text)
                    set_setting('budget_sync_status', '수집중')
                    try:
                        sync_budget_snapshot(today.year, today_text)
                        set_setting('budget_sync_status', '완료')
                    except Exception as e:
                        set_setting('budget_sync_status', '오류')
                        set_setting('last_budget_sync_result', f'예산 자동수집 실패: {e}')
        except Exception:
            pass
        time.sleep(60)


def start_scheduler():
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_worker, name='g2b-auto-sync', daemon=True).start()
