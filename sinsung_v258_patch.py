"""v2.5.8: shopping auto sync every 2 hours with a 6-hour change overlap.

The initial 2025-01-01 historical build remains manual. After that build is
complete, shopping changes are collected every 2 hours using exact HHMM change
datetime parameters and a 6-hour overlap. Existing source-key UPSERT behavior
prevents duplicate rows while recovering delayed updates safely.
"""
import datetime as dt
import math
import time
import urllib.parse

from db import get_setting, set_setting

VERSION = "2.5.8-sinsung-shopping-2h"
SHOP_INTERVAL_HOURS = 2
SHOP_OVERLAP_HOURS = 6


def _request_changed_datetime(g, start_dt, end_dt, page=1, rows=999):
    key = g.get_setting("api_key")
    base = g.get_setting("shop_api_base_url").rstrip("/")
    op = g.get_setting("shop_api_operation").strip("/")
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    if not op:
        raise RuntimeError("쇼핑몰 납품요구상세 오퍼레이션명이 비어 있습니다.")

    params = {
        "serviceKey": key,
        "numOfRows": int(rows),
        "pageNo": int(page),
        "type": "json",
        "inqryDiv": "1",
        "chgDtBgnDt": start_dt.strftime("%Y%m%d%H%M"),
        "chgDtEndDt": end_dt.strftime("%Y%m%d%H%M"),
    }
    url = f"{base}/{op}?" + urllib.parse.urlencode(params, safe="%")
    return g._request(url, "shop")


def _sync_changed_hours(start_dt, end_dt, max_pages=1000):
    import g2b_sync as g

    start_text = start_dt.strftime("%Y-%m-%d %H:%M")
    end_text = end_dt.strftime("%Y-%m-%d %H:%M")
    with g.SHOP_LOCK:
        log_id = g.new_sync_log("SHOPPING-2H", start_text, end_text)
        processed = seen = matched = skipped = 0
        try:
            page = 1
            total = None
            rows_per_page = 999
            while page <= max_pages:
                items, total = _request_changed_datetime(g, start_dt, end_dt, page, rows_per_page)
                if page == 1 and total and math.ceil(total / rows_per_page) > max_pages:
                    raise g.IncompleteSyncError(
                        f"변경일시 원본 {total:,}건으로 페이지 한도 {max_pages:,}를 초과합니다."
                    )
                if not items:
                    break
                saved_now, matched_now, skipped_now = g.upsert_shop(items, target_only=True)
                processed += saved_now
                matched += matched_now
                skipped += skipped_now
                if page == 1:
                    set_setting(
                        "last_shop_first_fields",
                        ", ".join(sorted(str(k) for k in items[0].keys())) if items else "",
                    )
                seen += len(items)
                set_setting("last_shop_raw_count", str(seen))
                set_setting("last_shop_matched_count", str(matched))
                set_setting("last_shop_saved_count", str(processed))
                set_setting("last_shop_skipped_count", str(skipped))
                if total is not None and seen >= total:
                    break
                page += 1
                time.sleep(0.15)

            if total and seen < total:
                raise g.IncompleteSyncError(f"변경일시 원본 {total:,}건 중 {seen:,}건만 수집했습니다.")

            now_text = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result = (
                f"2시간 자동수집 · {start_text} ~ {end_text} · 최근 {SHOP_OVERLAP_HOURS}시간 중첩 · "
                f"원본 {seen:,}건 / 세부품명번호 대상 {matched:,}건 / 저장·갱신 {processed:,}건 / "
                f"필수값 누락 {skipped:,}건"
            )
            set_setting("last_sync", now_text)
            set_setting("last_shop_error", "")
            set_setting("last_sync_result", result)
            g.finish_sync_log(log_id, "OK", processed, result)
            return processed, seen
        except g.ApiQuotaReached as exc:
            g.finish_sync_log(log_id, "PAUSED", processed, str(exc))
            raise
        except Exception as exc:
            set_setting("last_shop_error", str(exc))
            g.finish_sync_log(log_id, "ERROR", processed, str(exc))
            raise


def apply_v258_patch():
    import scheduler as sch
    import server as s

    def worker():
        while True:
            try:
                enabled = str(get_setting("auto_sync_enabled", "0")).lower() in ("1", "true", "yes", "on")
                if enabled and get_setting("api_key"):
                    now = dt.datetime.now()
                    today = now.date()

                    # Shopping starts only after the one-time 2025 historical build.
                    if get_setting("backfill_status", "") == "완료":
                        last_text = get_setting("last_shop_2h_attempt", "")
                        due = True
                        if last_text:
                            try:
                                due = (now - dt.datetime.fromisoformat(last_text)).total_seconds() >= SHOP_INTERVAL_HOURS * 3600
                            except Exception:
                                due = True
                        if due:
                            set_setting("last_shop_2h_attempt", now.isoformat(timespec="seconds"))
                            start_dt = now - dt.timedelta(hours=SHOP_OVERLAP_HOURS)
                            try:
                                n, raw = _sync_changed_hours(start_dt, now)
                                set_setting("last_shop_2h_success", now.isoformat(timespec="seconds"))
                                set_setting(
                                    "last_shop_2h_result",
                                    f"2시간 자동수집 완료 · 최근 {SHOP_OVERLAP_HOURS}시간 원본 {raw:,}건 / 저장·갱신 {n:,}건",
                                )
                            except Exception as exc:
                                set_setting("last_shop_2h_result", f"2시간 자동수집 실패: {exc}")
                    elif not get_setting("last_shop_2h_result", ""):
                        set_setting("last_shop_2h_result", "2025-01-01 수동 구축 완료 후 2시간 자동수집이 시작됩니다.")

                    # Bid/service retain the existing configurable interval behavior.
                    hours = max(1, int(float(get_setting("auto_sync_hours", "3") or 3)))
                    last = get_setting("last_bidservice_auto_sync", "")
                    due_bs = True
                    if last:
                        try:
                            due_bs = (now - dt.datetime.fromisoformat(last)).total_seconds() >= hours * 3600
                        except Exception:
                            due_bs = True
                    if due_bs:
                        days = max(1, min(90, int(float(get_setting("auto_sync_days", "14") or 14))))
                        end = today
                        bstart = max(end - dt.timedelta(days=days), end - dt.timedelta(days=27))
                        messages = []
                        try:
                            n = sch.sync_bids_period(bstart.isoformat(), end.isoformat())
                            messages.append(f"물품입찰 {n:,}건")
                        except Exception as exc:
                            messages.append(f"입찰 오류: {exc}")
                        try:
                            n = sch.sync_services_period(bstart.isoformat(), end.isoformat())
                            messages.append(f"용역 {n:,}건")
                        except Exception as exc:
                            messages.append(f"용역 오류: {exc}")
                        set_setting("last_bidservice_auto_sync", now.isoformat(timespec="seconds"))
                        set_setting("last_auto_bidservice_result", " / ".join(messages))

                # Preserve the existing once-daily budget snapshot.
                today = dt.date.today()
                today_text = today.isoformat()
                if str(get_setting("budget_auto_sync_enabled", "1")).lower() in ("1", "true", "yes", "on") and sch.get_lofin_key():
                    if get_setting("last_budget_auto_attempt_date", "") != today_text:
                        set_setting("last_budget_auto_attempt_date", today_text)
                        set_setting("budget_sync_status", "수집중")
                        try:
                            sch.sync_budget_snapshot(today.year, today_text)
                            set_setting("budget_sync_status", "완료")
                        except Exception as exc:
                            set_setting("budget_sync_status", "오류")
                            set_setting("last_budget_sync_result", f"예산 자동수집 실패: {exc}")
            except Exception:
                pass
            time.sleep(60)

    sch._worker = worker

    marker = "v258_shop_2h_initialized"
    if get_setting(marker, "") != "1":
        set_setting("last_shop_2h_attempt", "")
        set_setting("last_shop_2h_success", "")
        set_setting(
            "last_shop_2h_result",
            "2025-01-01 수동 구축 완료 후 쇼핑몰 변경분을 2시간마다 최근 6시간 중첩으로 자동수집합니다.",
        )
        set_setting(marker, "1")

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        page = page.replace("쇼핑몰 매일 자동수집", "쇼핑몰 2시간마다 자동수집")
        page = page.replace("매일 자동 변경분 수집", "2시간마다 자동 변경분 수집")
        page = page.replace("이후 매일 자동 변경분 수집", "이후 2시간마다 자동 변경분 수집")
        result = get_setting("last_shop_2h_result", "-") or "-"
        success = get_setting("last_shop_2h_success", "-") or "-"
        notice = (
            '<div class="notice"><b>쇼핑몰 2시간 자동수집</b><br>'
            f'과거자료 구축 완료 후 2시간마다 실행 · 최근 {SHOP_OVERLAP_HOURS}시간 변경분 중첩 조회<br>'
            f'최근 성공: {s.esc(success)}<br>{s.esc(result)}</div>'
        )
        marker_html = '<hr><h3>수동 동기화</h3>'
        if marker_html in page and "쇼핑몰 2시간 자동수집</b>" not in page:
            page = page.replace(marker_html, notice + marker_html, 1)
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
