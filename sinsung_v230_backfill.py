"""SINSUNG G2B DATA VIEW 2.3 historical backfill.

Builds real G2B history from 2025-01-01 without changing the verified 2.2
realtime collector. The backfill reuses collector_v200's request/parser/UPSERT
primitives but keeps its own progress and log state so historical work does not
overwrite realtime automatic-collection status.
"""
import datetime as dt
import math
import threading
import time

import collector_v200 as collector
from db import finish_sync_log, get_setting, new_sync_log, set_setting

VERSION = "2.3"
HISTORY_START = dt.date(2025, 1, 1)
CHUNK_DAYS = 7
BACKFILL_API_RESERVE = 200
AUTO_PRIORITY_MINUTES = 10
INIT_MARKER = "v230_history_initialized"

_SOURCE_ORDER = ("shop", "bids", "services")
_SOURCE_LABEL = {"shop": "쇼핑몰", "bids": "물품입찰", "services": "용역"}
_SOURCE_KIND = {"shop": "shop", "bids": "bid", "services": "bid"}
_ACTIVE_STATUSES = {
    "준비", "재개대기", "실행중", "자동수집 대기", "수동수집 대기", "호출한도 대기", "중단됨"
}

_thread = None
_thread_lock = threading.Lock()


class BackfillQuotaPause(RuntimeError):
    pass


def _truth(value):
    return str(value or "").lower() in ("1", "true", "yes", "on")


def _parse_date(value, default=None):
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except Exception:
        return default


def _parse_dt(value):
    try:
        return dt.datetime.fromisoformat(str(value or ""))
    except Exception:
        return None


def _int_setting(key, default=0):
    try:
        return int(float(get_setting(key, str(default)) or default))
    except Exception:
        return int(default)


def initialize_backfill_v230():
    """Prepare 2.3 history settings once; never deletes existing real data."""
    if get_setting(INIT_MARKER, "") == "1":
        return False

    status = get_setting("backfill_status", "")
    if status in ("", "비활성"):
        set_setting("backfill_status", "대기")
        set_setting("backfill_progress", "0")
        set_setting("backfill_message", "2025-01-01부터 현재까지 G2B 과거자료 구축 대기")

    set_setting("backfill_start_date", HISTORY_START.isoformat())
    set_setting("backfill_chunk_days", str(CHUNK_DAYS))
    set_setting("backfill_api_reserve", str(BACKFILL_API_RESERVE))
    set_setting("backfill_stop_requested", "0")
    set_setting("backfill_auto_resume", "0")
    set_setting(INIT_MARKER, "1")
    return True


def is_backfill_active():
    return get_setting("backfill_status", "") in _ACTIVE_STATUSES and get_setting("backfill_auto_resume", "0") == "1"


def _quota_ready(kind):
    used, limit = collector.api_usage(kind)
    reserve = max(100, _int_setting("backfill_api_reserve", BACKFILL_API_RESERVE))
    threshold = max(1, limit - reserve)
    return used < threshold, used, limit, reserve


def _quota_guard(kind):
    ok, used, limit, reserve = _quota_ready(kind)
    if not ok:
        raise BackfillQuotaPause(
            f"{kind.upper()} API {used:,}/{limit:,}회 · 실시간 자동수집용 {reserve:,}회 여유 유지"
        )


def _auto_priority_reason():
    if not _truth(get_setting("auto_sync_enabled", "1")):
        return ""
    if get_setting("last_auto_sync_status", "") == "수집중":
        return "2시간 자동수집 진행 중"

    now = dt.datetime.now()
    next_due = _parse_dt(get_setting("next_auto_sync_due", ""))
    if next_due and next_due <= now + dt.timedelta(minutes=AUTO_PRIORITY_MINUTES):
        return "2시간 자동수집 예정시간 우선"

    last = _parse_dt(get_setting("last_auto_sync", ""))
    if not last or (now - last).total_seconds() >= 2 * 3600:
        return "2시간 자동수집 실행 대기"
    return ""


def _tomorrow_resume_text():
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    return f"{tomorrow.isoformat()}T00:05:00"


def _wait_until_ready(kind):
    """Give realtime/manual work and the API reserve priority over history."""
    while True:
        if get_setting("backfill_stop_requested", "0") == "1":
            return False

        if _truth(get_setting("manual_sync_active", "0")):
            set_setting("backfill_status", "수동수집 대기")
            set_setting("backfill_message", "수동 실데이터 수집이 끝난 뒤 과거 구축을 자동 재개합니다.")
            set_setting("backfill_next_resume", "수동수집 종료 후")
            time.sleep(15)
            continue

        reason = _auto_priority_reason()
        if reason:
            set_setting("backfill_status", "자동수집 대기")
            set_setting("backfill_message", reason + " · 완료 후 과거 구축 자동 재개")
            set_setting("backfill_next_resume", get_setting("next_auto_sync_due", "") or "자동수집 완료 후")
            time.sleep(20)
            continue

        ok, used, limit, reserve = _quota_ready(kind)
        if not ok:
            set_setting("backfill_status", "호출한도 대기")
            set_setting(
                "backfill_message",
                f"{kind.upper()} API {used:,}/{limit:,}회 · 실시간 자동수집용 {reserve:,}회 여유를 남기고 다음 날 자동 재개",
            )
            set_setting("backfill_next_resume", _tomorrow_resume_text())
            time.sleep(60)
            continue

        set_setting("backfill_status", "실행중")
        set_setting("backfill_next_resume", "")
        return True


def _sync_shop_history(start_date, end_date, max_pages=500):
    """Historical shopping sync without touching realtime last_sync fields."""
    with collector.SHOP_LOCK:
        log_id = new_sync_log("HISTORY_SHOPPING", start_date, end_date)
        saved = raw_count = matched = skipped = 0
        page = 1
        total = None
        try:
            while page <= max_pages:
                _quota_guard("shop")
                items, total = collector.fetch_shop_page(start_date, end_date, page=page, rows=999)
                if page == 1 and total and math.ceil(total / 999) > max_pages:
                    raise collector.IncompleteSyncError(
                        f"{start_date}~{end_date} 원본 {total:,}건으로 과거구축 페이지 한도 초과"
                    )
                if not items:
                    break
                saved_now, matched_now, skipped_now = collector.upsert_shop(items, target_only=True)
                saved += saved_now
                matched += matched_now
                skipped += skipped_now
                raw_count += len(items)
                if total is not None and raw_count >= total:
                    break
                page += 1
                time.sleep(0.12)
            if total and raw_count < total:
                raise collector.IncompleteSyncError(
                    f"{start_date}~{end_date}: 원본 {total:,}건 중 {raw_count:,}건만 조회"
                )
            msg = f"원본 {raw_count:,}건 / 대상 {matched:,}건 / 저장·갱신 {saved:,}건 / 누락 {skipped:,}건"
            finish_sync_log(log_id, "OK", saved, msg)
            return saved, msg
        except BackfillQuotaPause as exc:
            finish_sync_log(log_id, "PAUSED", saved, str(exc))
            raise
        except Exception as exc:
            finish_sync_log(log_id, "ERROR", saved, str(exc))
            raise


def _sync_bid_history(start_date, end_date, service=False, max_pages=500):
    operation = "getBidPblancListInfoServc" if service else "getBidPblancListInfoThng"
    terms = collector.SERVICE_TARGETS if service else collector.BID_TARGETS
    business_type = "용역" if service else "물품"
    log_type = "HISTORY_SERVICES" if service else "HISTORY_BIDS"
    with collector.BID_LOCK:
        log_id = new_sync_log(log_type, start_date, end_date)
        saved = raw_count = 0
        page = 1
        total = None
        try:
            while page <= max_pages:
                _quota_guard("bid")
                items, total = collector.fetch_bid_page(
                    start_date, end_date, page=page, rows=999, operation=operation
                )
                if page == 1 and total and math.ceil(total / 999) > max_pages:
                    raise collector.IncompleteSyncError(
                        f"{log_type} 원본 {total:,}건으로 과거구축 페이지 한도 초과"
                    )
                if not items:
                    break
                saved += collector.upsert_bids(
                    items, target_terms=terms, business_type_override=business_type
                )
                raw_count += len(items)
                if total is not None and raw_count >= total:
                    break
                page += 1
                time.sleep(0.12)
            if total and raw_count < total:
                raise collector.IncompleteSyncError(
                    f"{start_date}~{end_date}: 원본 {total:,}건 중 {raw_count:,}건만 조회"
                )
            msg = f"원본 {raw_count:,}건 / 저장·갱신 {saved:,}건"
            finish_sync_log(log_id, "OK", saved, msg)
            return saved, msg
        except BackfillQuotaPause as exc:
            finish_sync_log(log_id, "PAUSED", saved, str(exc))
            raise
        except Exception as exc:
            finish_sync_log(log_id, "ERROR", saved, str(exc))
            raise


def _target_end():
    return _parse_date(get_setting("backfill_target_end", ""), dt.date.today())


def _cursor_date():
    return _parse_date(get_setting("backfill_cursor_date", ""), HISTORY_START)


def _cursor_source():
    value = get_setting("backfill_cursor_source", "shop")
    return value if value in _SOURCE_ORDER else "shop"


def _progress_text(cursor_date=None, source=None):
    start = HISTORY_START
    target = _target_end()
    cursor_date = cursor_date or _cursor_date()
    source = source or _cursor_source()
    if cursor_date > target:
        return "100"
    total_days = max(1, (target - start).days + 1)
    completed_days = max(0, (cursor_date - start).days)
    chunk_end = min(cursor_date + dt.timedelta(days=CHUNK_DAYS - 1), target)
    chunk_days = max(1, (chunk_end - cursor_date).days + 1)
    source_fraction = _SOURCE_ORDER.index(source) / len(_SOURCE_ORDER)
    done = completed_days + chunk_days * source_fraction
    return f"{min(99.9, max(0.0, done * 100.0 / total_days)):.1f}"


def _set_cursor_after(source, chunk_start, chunk_end):
    if source == "shop":
        next_date, next_source = chunk_start, "bids"
    elif source == "bids":
        next_date, next_source = chunk_start, "services"
    else:
        next_date, next_source = chunk_end + dt.timedelta(days=1), "shop"
    set_setting("backfill_cursor_date", next_date.isoformat())
    set_setting("backfill_cursor_source", next_source)
    set_setting("backfill_progress", _progress_text(next_date, next_source))


def _run_current_source(source, start_date, end_date):
    if source == "shop":
        return _sync_shop_history(start_date, end_date)
    if source == "bids":
        return _sync_bid_history(start_date, end_date, service=False)
    return _sync_bid_history(start_date, end_date, service=True)


def _reset_new_job(target_end):
    set_setting("backfill_target_end", target_end.isoformat())
    set_setting("backfill_cursor_date", HISTORY_START.isoformat())
    set_setting("backfill_cursor_source", "shop")
    set_setting("backfill_progress", "0")
    set_setting("backfill_saved_shop", "0")
    set_setting("backfill_saved_bids", "0")
    set_setting("backfill_saved_services", "0")
    set_setting("backfill_completed_steps", "0")
    set_setting("backfill_started_at", "")
    set_setting("backfill_finished_at", "")
    set_setting("backfill_last_chunk", "")
    set_setting("backfill_last_error", "")


def _run_worker():
    if not get_setting("backfill_started_at", ""):
        set_setting("backfill_started_at", dt.datetime.now().isoformat(timespec="seconds"))
    set_setting("backfill_status", "실행중")
    set_setting("backfill_message", "2025-01-01 과거자료 구축을 시작했습니다.")

    while True:
        if get_setting("backfill_stop_requested", "0") == "1":
            set_setting("backfill_status", "중지됨")
            set_setting("backfill_auto_resume", "0")
            set_setting("backfill_message", "사용자 요청으로 중지했습니다. 시작/재개 버튼으로 이어서 구축할 수 있습니다.")
            return

        target = _target_end()
        cursor = _cursor_date()
        source = _cursor_source()
        if cursor > target:
            finished = dt.datetime.now().isoformat(timespec="seconds")
            set_setting("backfill_progress", "100")
            set_setting("backfill_status", "완료")
            set_setting("backfill_auto_resume", "0")
            set_setting("backfill_finished_at", finished)
            set_setting("backfill_current_source", "")
            set_setting("backfill_next_resume", "")
            set_setting(
                "backfill_message",
                f"2025-01-01 ~ {target.isoformat()} 과거자료 구축 완료 · 쇼핑몰 {get_setting('backfill_saved_shop','0')}건 / 물품입찰 {get_setting('backfill_saved_bids','0')}건 / 용역 {get_setting('backfill_saved_services','0')}건 저장·갱신",
            )
            return

        kind = _SOURCE_KIND[source]
        if not _wait_until_ready(kind):
            continue

        chunk_end = min(cursor + dt.timedelta(days=CHUNK_DAYS - 1), target)
        label = _SOURCE_LABEL[source]
        set_setting("backfill_current_source", label)
        set_setting("backfill_last_chunk", f"{cursor.isoformat()} ~ {chunk_end.isoformat()} · {label}")
        set_setting("backfill_status", "실행중")
        set_setting("backfill_progress", _progress_text(cursor, source))
        set_setting(
            "backfill_message",
            f"{cursor.isoformat()} ~ {chunk_end.isoformat()} · {label} 과거자료 수집 중",
        )

        try:
            saved, detail = _run_current_source(source, cursor.isoformat(), chunk_end.isoformat())
        except BackfillQuotaPause as exc:
            set_setting("backfill_status", "호출한도 대기")
            set_setting("backfill_message", str(exc) + " · 다음 날 자동 재개")
            set_setting("backfill_next_resume", _tomorrow_resume_text())
            time.sleep(60)
            continue
        except collector.ApiQuotaReached as exc:
            set_setting("backfill_status", "호출한도 대기")
            set_setting("backfill_message", str(exc) + " · 다음 날 자동 재개")
            set_setting("backfill_next_resume", _tomorrow_resume_text())
            time.sleep(60)
            continue
        except Exception as exc:
            set_setting("backfill_status", "오류")
            set_setting("backfill_auto_resume", "0")
            set_setting("backfill_last_error", str(exc))
            set_setting("backfill_message", f"{label} 과거자료 구축 오류: {exc} · 같은 구간부터 재개 가능")
            set_setting("backfill_current_source", "")
            return

        count_key = {
            "shop": "backfill_saved_shop",
            "bids": "backfill_saved_bids",
            "services": "backfill_saved_services",
        }[source]
        set_setting(count_key, str(_int_setting(count_key, 0) + int(saved)))
        set_setting("backfill_completed_steps", str(_int_setting("backfill_completed_steps", 0) + 1))
        set_setting("backfill_last_error", "")
        set_setting("backfill_message", f"{cursor.isoformat()} ~ {chunk_end.isoformat()} · {label} 완료 · {detail}")
        _set_cursor_after(source, cursor, chunk_end)
        set_setting("backfill_current_source", "")
        time.sleep(1.0)


def start_backfill_thread():
    """Start or resume history building. Returns False when already running/unconfigured."""
    global _thread
    initialize_backfill_v230()
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            set_setting("backfill_message", "과거자료 구축이 이미 실행 중입니다.")
            return False
        if not (get_setting("api_key", "") or "").strip():
            set_setting("backfill_status", "오류")
            set_setting("backfill_message", "나라장터 API 인증키가 없어 과거자료 구축을 시작할 수 없습니다.")
            return False

        today = dt.date.today()
        status = get_setting("backfill_status", "")
        cursor = _cursor_date()
        target = _target_end()
        if status == "완료" or cursor > target or cursor < HISTORY_START:
            _reset_new_job(today)
        else:
            # A resumed job extends its target to the current day; the realtime
            # 2-hour collector continues independently during the build.
            if today > target:
                set_setting("backfill_target_end", today.isoformat())

        set_setting("backfill_stop_requested", "0")
        set_setting("backfill_auto_resume", "1")
        set_setting("backfill_status", "재개대기" if get_setting("backfill_started_at", "") else "준비")
        set_setting("backfill_message", "2025-01-01 과거자료 구축 시작 준비")
        _thread = threading.Thread(target=_run_worker, name="g2b-history-2025", daemon=True)
        _thread.start()
        return True


def request_backfill_stop():
    if not is_backfill_active():
        return False
    set_setting("backfill_stop_requested", "1")
    set_setting("backfill_auto_resume", "0")
    set_setting("backfill_status", "중지요청")
    set_setting("backfill_message", "현재 API 페이지 처리가 끝나는 즉시 과거자료 구축을 중지합니다.")
    return True


def resume_backfill_if_needed():
    initialize_backfill_v230()
    status = get_setting("backfill_status", "")
    if get_setting("backfill_auto_resume", "0") != "1":
        return False
    if status in ("완료", "중지됨", "중지요청", "오류", "대기", "비활성"):
        return False
    return start_backfill_thread()


def schedule_resume_after_backend_start(delay=2.0):
    timer = threading.Timer(float(delay), resume_backfill_if_needed)
    timer.daemon = True
    timer.start()
    return timer


# Compatibility name used by legacy server.py. In 2.3 it means the explicit
# 2025-01-01 history build, not the old disabled three-year implementation.
def backfill_three_years(progress=None):
    _run_worker()
