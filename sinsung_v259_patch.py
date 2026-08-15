"""v2.5.9: reliable 2025 shopping history via full snapshot + local date filtering.

Why:
- getDlvrReqDtlInfoList is known to return a complete page-able dataset without
  date filters in the deployed service.
- Operation-specific registration/change date filters have produced zero rows in
  production, so historical completeness must not depend on those filters.

Strategy:
1) Historical/manual sync: fetch every page without optional date parameters,
   normalize each row, then filter by base_date locally.
2) Ongoing auto sync: after the history build completes, re-read the full
   snapshot every 2 hours and UPSERT matching target items. This is intentionally
   conservative and avoids missed changes caused by unreliable date filters.
3) Respect the daily API safety limit and cap automatic snapshot pages.
"""
import datetime as dt
import math
import time
import urllib.parse

from db import connect, get_setting, set_setting

VERSION = "2.5.9-sinsung-snapshot-history"
HISTORY_START = "2025-01-01"
SHOP_INTERVAL_HOURS = 2
AUTO_MAX_PAGES = 60
AUTO_API_RESERVE = 100
ROWS_PER_PAGE = 999


def _request_snapshot_page(g, page=1, rows=ROWS_PER_PAGE):
    key = g.get_setting("api_key")
    base = g.get_setting("shop_api_base_url").rstrip("/")
    op = g.get_setting("shop_api_operation").strip("/")
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")
    if not op:
        raise RuntimeError("쇼핑몰 납품요구상세 오퍼레이션명이 비어 있습니다.")

    params = {
        "serviceKey": key,
        "pageNo": int(page),
        "numOfRows": int(rows),
        "type": "json",
        "inqryDiv": "1",
    }
    url = f"{base}/{op}?" + urllib.parse.urlencode(params, safe="%")
    return g._request(url, "shop")


def _range_ok(base_date, start_date, end_date):
    text = str(base_date or "")[:10]
    return bool(text) and start_date <= text <= end_date


def _collect_snapshot_range(start_date, end_date, *, max_pages=2000, progress=None, auto=False):
    """Fetch the whole delivery-detail snapshot and filter requested dates locally."""
    import g2b_sync as g

    try:
        sdate = dt.date.fromisoformat(start_date)
        edate = dt.date.fromisoformat(end_date)
    except Exception as exc:
        raise ValueError("수집 시작일/종료일 형식이 올바르지 않습니다.") from exc
    if sdate > edate:
        raise ValueError("수집 시작일이 종료일보다 늦습니다.")

    log_type = "SHOPPING-SNAPSHOT-AUTO" if auto else "SHOPPING-SNAPSHOT"
    log_id = g.new_sync_log(log_type, start_date, end_date)

    raw_seen = 0
    local_eligible = 0
    processed = 0
    matched = 0
    skipped = 0
    total = None
    pages_needed = None
    min_date = ""
    max_date = ""
    year_2025_raw = 0
    year_2025_target = 0

    with g.SHOP_LOCK:
        try:
            page = 1
            while page <= max_pages:
                items, total = _request_snapshot_page(g, page, ROWS_PER_PAGE)

                if page == 1:
                    total = int(total or 0)
                    if total <= 0 or not items:
                        raise RuntimeError(
                            "쇼핑몰 납품요구상세 전체조회 원본이 0건입니다. "
                            "서비스키 활용승인 또는 getDlvrReqDtlInfoList 응답을 확인해 주세요."
                        )
                    pages_needed = max(1, math.ceil(total / ROWS_PER_PAGE))
                    if pages_needed > max_pages:
                        raise g.IncompleteSyncError(
                            f"전체 원본 {total:,}건으로 {pages_needed:,}페이지가 필요해 "
                            f"허용 페이지 {max_pages:,}를 초과합니다."
                        )

                    if auto:
                        used, limit = g.api_usage("shop")
                        remaining_pages = max(0, pages_needed - 1)
                        safe_remaining = max(0, limit - AUTO_API_RESERVE - used)
                        if remaining_pages > safe_remaining:
                            raise g.ApiQuotaReached(
                                f"2시간 자동수집 보류: 전체 {pages_needed:,}페이지 필요, "
                                f"오늘 쇼핑몰 API {used:,}/{limit:,}회 사용. "
                                f"안전여유 {AUTO_API_RESERVE:,}회를 유지합니다."
                            )

                if not items:
                    break

                if page == 1:
                    set_setting(
                        "last_shop_first_fields",
                        ", ".join(sorted(str(k) for k in items[0].keys())) if items else "",
                    )

                filtered = []
                for raw in items:
                    x = g.normalize_shop_item(raw)
                    bdate = str(x.get("base_date") or "")[:10]
                    if bdate:
                        min_date = bdate if not min_date or bdate < min_date else min_date
                        max_date = bdate if not max_date or bdate > max_date else max_date
                    if _range_ok(bdate, start_date, end_date):
                        filtered.append(raw)
                        if bdate.startswith("2025-"):
                            year_2025_raw += 1
                            if (
                                str(x.get("detail_item_no") or "") in g.SHOP_DETAIL_ITEM_NOS
                                and str(x.get("item_id") or "")
                            ):
                                year_2025_target += 1

                raw_seen += len(items)
                local_eligible += len(filtered)

                if filtered:
                    saved_now, matched_now, skipped_now = g.upsert_shop(filtered, target_only=True)
                    processed += saved_now
                    matched += matched_now
                    skipped += skipped_now

                set_setting("last_shop_raw_count", str(raw_seen))
                set_setting("last_shop_matched_count", str(matched))
                set_setting("last_shop_saved_count", str(processed))
                set_setting("last_shop_skipped_count", str(skipped))
                set_setting("shop_snapshot_total", str(total or raw_seen))
                set_setting("shop_snapshot_pages", str(pages_needed or page))
                set_setting("shop_snapshot_min_date", min_date)
                set_setting("shop_snapshot_max_date", max_date)
                set_setting("shop_snapshot_local_eligible", str(local_eligible))
                set_setting("shop_snapshot_2025_raw", str(year_2025_raw))
                set_setting("shop_snapshot_2025_target", str(year_2025_target))

                if progress and pages_needed:
                    pct = min(99, int(page / max(1, pages_needed) * 100))
                    progress(pct, processed, raw_seen, local_eligible)

                if total is not None and raw_seen >= int(total):
                    break

                page += 1
                time.sleep(0.12)

            if total and raw_seen < int(total):
                raise g.IncompleteSyncError(
                    f"전체 원본 {int(total):,}건 중 {raw_seen:,}건만 수집했습니다."
                )
            if local_eligible <= 0:
                raise RuntimeError(
                    f"API 전체 원본 {raw_seen:,}건은 조회됐지만 {start_date}~{end_date} "
                    "범위의 납품요구/계약일 데이터가 0건입니다. "
                    f"API 원본 날짜범위는 {min_date or '-'} ~ {max_date or '-'} 입니다."
                )

            covers_2025 = start_date <= "2025-12-31" and end_date >= "2025-01-01"
            if covers_2025 and year_2025_raw <= 0:
                raise RuntimeError(
                    f"API 전체 원본 {raw_seen:,}건은 조회됐지만 2025년 원본이 0건입니다. "
                    f"원본 날짜범위는 {min_date or '-'} ~ {max_date or '-'} 입니다. "
                    "이 상태에서는 2025-01-01 과거구축을 완료로 처리하지 않습니다."
                )

            now_text = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result = (
                f"{start_date} ~ {end_date} · 전체스냅샷 로컬필터 · "
                f"API 원본 {raw_seen:,}건 / 기간대상 {local_eligible:,}건 / "
                f"2025년 원본 {year_2025_raw:,}건 / 2025년 대상 {year_2025_target:,}건 / "
                f"세부품명번호 대상 {matched:,}건 / 저장·갱신 {processed:,}건 / "
                f"원본 날짜범위 {min_date or '-'} ~ {max_date or '-'}"
            )
            set_setting("last_sync", now_text)
            set_setting("last_shop_error", "")
            set_setting("last_sync_result", result)
            set_setting("shop_snapshot_last_result", result)
            g.finish_sync_log(log_id, "OK", processed, result)

            return {
                "processed": processed,
                "matched": matched,
                "skipped": skipped,
                "raw": raw_seen,
                "eligible": local_eligible,
                "total": int(total or raw_seen),
                "pages": int(pages_needed or 1),
                "min_date": min_date,
                "max_date": max_date,
                "year_2025_raw": year_2025_raw,
                "year_2025_target": year_2025_target,
            }

        except g.ApiQuotaReached as exc:
            g.finish_sync_log(log_id, "PAUSED", processed, str(exc))
            raise
        except Exception as exc:
            set_setting("last_shop_error", str(exc))
            g.finish_sync_log(log_id, "ERROR", processed, str(exc))
            raise


def sync_shopping_period_snapshot(start_date, end_date, max_pages=2000):
    """Manual date-range sync: API full snapshot, local inclusive date filtering."""
    info = _collect_snapshot_range(start_date, end_date, max_pages=max_pages, auto=False)
    return int(info["processed"])


def build_history_from_2025(progress=None):
    """One-time reliable build from 2025-01-01 without API date-filter dependence."""
    today = dt.date.today().isoformat()

    set_setting("backfill_status", "실행중")
    set_setting("backfill_progress", "0")
    set_setting("backfill_cursor", "")
    set_setting("backfill_total_saved", "0")
    set_setting(
        "backfill_message",
        "전체 납품요구상세를 페이지별로 조회한 뒤 프로그램 내부에서 "
        "2025-01-01 이후 자료만 선별 중입니다.",
    )

    try:
        def report(pct, saved, raw, eligible):
            set_setting("backfill_progress", str(pct))
            set_setting("backfill_total_saved", str(saved))
            set_setting(
                "backfill_message",
                f"전체스냅샷 {pct}% · API 원본 {raw:,}건 확인 · "
                f"2025-01-01 이후 {eligible:,}건 · 저장·갱신 {saved:,}건",
            )
            if progress:
                progress(pct, saved)

        info = _collect_snapshot_range(
            HISTORY_START, today, max_pages=2000, progress=report, auto=False
        )

        set_setting("backfill_status", "완료")
        set_setting("backfill_progress", "100")
        set_setting("backfill_total_saved", str(info["processed"]))
        set_setting("backfill_cursor", "")
        set_setting("shop_history_build_completed", "1")
        set_setting("shop_history_build_completed_at", dt.datetime.now().isoformat(timespec="seconds"))
        set_setting(
            "backfill_message",
            f"2025-01-01 ~ {today} 구축 완료 · API 원본 {info['raw']:,}건 / "
            f"기간대상 {info['eligible']:,}건 / 2025년 원본 {info['year_2025_raw']:,}건 / "
            f"2025년 대상 {info['year_2025_target']:,}건 / "
            f"세부품명번호 저장·갱신 {info['processed']:,}건 · "
            f"API 원본 날짜범위 {info['min_date'] or '-'} ~ {info['max_date'] or '-'} · "
            "이후 2시간마다 전체 스냅샷을 재확인합니다.",
        )
        return int(info["processed"])

    except Exception as exc:
        set_setting("backfill_status", "오류")
        set_setting("backfill_progress", "0")
        set_setting("backfill_message", str(exc))
        raise


def _test_snapshot():
    import g2b_sync as g

    items, total = _request_snapshot_page(g, 1, 100)
    if int(total or 0) <= 0 or not items:
        raise RuntimeError("쇼핑몰 납품요구상세 전체조회 응답이 0건입니다.")

    dates = []
    for raw in items:
        x = g.normalize_shop_item(raw)
        b = str(x.get("base_date") or "")[:10]
        if b:
            dates.append(b)

    set_setting("shop_snapshot_test_total", str(int(total or 0)))
    set_setting("shop_snapshot_test_sample_min", min(dates) if dates else "")
    set_setting("shop_snapshot_test_sample_max", max(dates) if dates else "")
    set_setting(
        "shop_snapshot_test_result",
        f"전체조회 연결 성공 · 첫 페이지 {len(items):,}건 / 전체 {int(total or 0):,}건 · "
        f"첫 페이지 날짜범위 {(min(dates) if dates else '-')} ~ {(max(dates) if dates else '-')}",
    )
    return len(items), int(total or 0)


def apply_v259_patch():
    import g2b_sync as g
    import scheduler as sch
    import server as s

    g.sync_shopping_period = sync_shopping_period_snapshot
    s.sync_shopping_period = sync_shopping_period_snapshot
    g.backfill_three_years = build_history_from_2025
    s.backfill_three_years = build_history_from_2025
    sch.backfill_three_years = build_history_from_2025
    g.test_shopping_api = _test_snapshot
    s.test_shopping_api = _test_snapshot

    def fetch_shop_page(_start_date="", _end_date="", page=1, rows=ROWS_PER_PAGE):
        return _request_snapshot_page(g, page=page, rows=rows)

    g.fetch_shop_page = fetch_shop_page

    def worker():
        while True:
            try:
                enabled = str(get_setting("auto_sync_enabled", "0")).lower() in ("1", "true", "yes", "on")
                if enabled and get_setting("api_key"):
                    now = dt.datetime.now()
                    today = now.date()
                    today_text = today.isoformat()

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
                            try:
                                info = _collect_snapshot_range(
                                    HISTORY_START,
                                    today_text,
                                    max_pages=AUTO_MAX_PAGES,
                                    auto=True,
                                )
                                set_setting("last_shop_2h_success", now.isoformat(timespec="seconds"))
                                set_setting(
                                    "last_shop_2h_result",
                                    f"2시간 전체스냅샷 완료 · API {info['pages']:,}페이지 / "
                                    f"원본 {info['raw']:,}건 / 기간대상 {info['eligible']:,}건 / "
                                    f"저장·갱신 {info['processed']:,}건",
                                )
                            except Exception as exc:
                                set_setting("last_shop_2h_result", f"2시간 전체스냅샷 실패: {exc}")
                    elif not get_setting("last_shop_2h_result", ""):
                        set_setting(
                            "last_shop_2h_result",
                            "2025-01-01 수동 구축 완료 후 2시간 전체스냅샷 자동수집이 시작됩니다.",
                        )

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
                        bstart = max(today - dt.timedelta(days=days), today - dt.timedelta(days=27))
                        messages = []
                        try:
                            n = sch.sync_bids_period(bstart.isoformat(), today_text)
                            messages.append(f"물품입찰 {n:,}건")
                        except Exception as exc:
                            messages.append(f"입찰 오류: {exc}")
                        try:
                            n = sch.sync_services_period(bstart.isoformat(), today_text)
                            messages.append(f"용역 {n:,}건")
                        except Exception as exc:
                            messages.append(f"용역 오류: {exc}")
                        set_setting("last_bidservice_auto_sync", now.isoformat(timespec="seconds"))
                        set_setting("last_auto_bidservice_result", " / ".join(messages))

                today = dt.date.today()
                today_text = today.isoformat()
                if (
                    str(get_setting("budget_auto_sync_enabled", "1")).lower() in ("1", "true", "yes", "on")
                    and sch.get_lofin_key()
                ):
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

    marker = "v259_snapshot_history_initialized"
    if get_setting(marker, "") != "1":
        set_setting("backfill_status", "대기")
        set_setting("backfill_progress", "0")
        set_setting("backfill_cursor", "")
        set_setting("backfill_total_saved", "0")
        set_setting(
            "backfill_message",
            "2025-01-01 수동 구축 대기 · API 날짜조건을 쓰지 않고 전체조회 후 로컬 날짜필터로 구축합니다.",
        )
        set_setting("shop_date_param_mode", "full-snapshot")
        set_setting("shop_date_param_label", "전체조회 + 로컬 날짜필터")
        set_setting("last_shop_2h_attempt", "")
        set_setting("last_shop_2h_success", "")
        set_setting(
            "last_shop_2h_result",
            "2025-01-01 수동 구축 완료 후 2시간마다 전체 스냅샷을 재확인합니다.",
        )
        set_setting(marker, "1")

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        import re

        page = original_settings_html(msg, error)

        page = re.sub(
            r'<div class="notice"><b>과거자료 수집 기준:</b>.*?</div>',
            "",
            page,
            flags=re.S,
        )
        page = re.sub(
            r'<div class="notice"><b>쇼핑몰 API 날짜조건 진단</b>.*?</div>',
            "",
            page,
            flags=re.S,
        )
        page = re.sub(
            r'<div class="notice"><b>쇼핑몰 2시간 자동수집</b>.*?</div>',
            "",
            page,
            flags=re.S,
        )

        page = page.replace("쇼핑몰 API 연결/날짜조건 테스트", "쇼핑몰 API 연결 테스트")
        page = page.replace("2025-01-01부터 구축 시작", "2025-01-01 수동 구축")

        total = get_setting("shop_snapshot_total", "0")
        pages = get_setting("shop_snapshot_pages", "0")
        min_date = get_setting("shop_snapshot_min_date", "-") or "-"
        max_date = get_setting("shop_snapshot_max_date", "-") or "-"
        eligible = get_setting("shop_snapshot_local_eligible", "0")
        y2025_raw = get_setting("shop_snapshot_2025_raw", "0")
        y2025_target = get_setting("shop_snapshot_2025_target", "0")
        last2h = get_setting("last_shop_2h_result", "-") or "-"
        success2h = get_setting("last_shop_2h_success", "-") or "-"

        notice = (
            '<div class="notice"><b>쇼핑몰 수집 안정화 방식</b><br>'
            '과거구축: getDlvrReqDtlInfoList 전체 페이지 조회 → 프로그램 내부에서 '
            '2025-01-01 이후만 선별 저장<br>'
            '자동수집: 과거구축 완료 후 2시간마다 전체 스냅샷 재확인·UPSERT<br>'
            f'최근 전체조회: 원본 {s.esc(total)}건 / {s.esc(pages)}페이지 / '
            f'날짜범위 {s.esc(min_date)} ~ {s.esc(max_date)} / '
            f'2025 이후 기간대상 {s.esc(eligible)}건 / '
            f'2025년 원본 {s.esc(y2025_raw)}건 / 대상 {s.esc(y2025_target)}건<br>'
            f'최근 2시간 자동 성공: {s.esc(success2h)}<br>{s.esc(last2h)}</div>'
        )

        marker_html = "<hr><h3>수동 동기화</h3>"
        if marker_html in page and "쇼핑몰 수집 안정화 방식" not in page:
            page = page.replace(marker_html, notice + marker_html, 1)

        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
