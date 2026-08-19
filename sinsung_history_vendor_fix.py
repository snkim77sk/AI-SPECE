"""Historical shopping backfill + vendor ranking fixes.

Goals:
- Build shopping-contract history from 2025-01-01 to today.
- Preserve the API safety reserve and automatically resume on the next KST day.
- Keep the recent two-hour collector higher priority than historical work.
- Calculate vendor rank against the full market even when a search term is used.
- Normalize the v2.5.1 nationwide sentinel (__ALL__) for every date_params consumer.
"""
import datetime as dt
import threading
import time

VERSION = "2.3.1-history-vendor"
HISTORY_START = dt.date(2025, 1, 1)
HISTORY_CHUNK_DAYS = 7
HISTORY_DAILY_API_BUDGET = 300
KST = dt.timezone(dt.timedelta(hours=9))

_HISTORY_RUN_LOCK = threading.Lock()
_HISTORY_THREAD_LOCK = threading.Lock()
_HISTORY_THREAD = None


def _today_kst():
    return dt.datetime.now(KST).date()


def _normalize_region(value):
    text = str(value or "").strip()
    return "" if text in ("", "__ALL__", "전국") else text


def _parse_date(value, fallback):
    try:
        return dt.date.fromisoformat(str(value or ""))
    except Exception:
        return fallback


def _progress_percent(next_date, today):
    total = max(1, (today - HISTORY_START).days + 1)
    completed = max(0, min(total, (next_date - HISTORY_START).days))
    return max(0, min(100, int(completed * 100 / total)))


def apply_history_vendor_fix():
    import app as legacy_app
    import collector_v200 as collector
    import g2b_sync
    import scheduler as scheduler_module
    import server as s

    if getattr(s, "_history_vendor_fix_applied", False):
        return s

    # ------------------------------------------------------------------
    # 1) Nationwide sentinel must work on every screen, not only shopping.
    #    v220 backend calls legacy_app._install_dynamic_date_defaults later,
    #    so wrap that installer itself rather than patching date_params once.
    # ------------------------------------------------------------------
    if not getattr(legacy_app, "_nationwide_dynamic_dates_wrapped", False):
        original_install_dates = legacy_app._install_dynamic_date_defaults

        def install_dynamic_dates_with_region(server_module):
            original_install_dates(server_module)
            original_date_params = server_module.date_params

            def date_params(qs, days=14):
                start, end, region = original_date_params(qs, days)
                return start, end, _normalize_region(region)

            server_module.date_params = date_params

        legacy_app._install_dynamic_date_defaults = install_dynamic_dates_with_region
        legacy_app._nationwide_dynamic_dates_wrapped = True

    # ---------------------------------------------------------------
    # 2) Resumable historical shopping backfill from 2025-01-01.
    # ---------------------------------------------------------------
    def _history_daily_budget():
        try:
            return max(
                1,
                int(float(collector.get_setting(
                    "history_backfill_daily_api_budget",
                    str(HISTORY_DAILY_API_BUDGET),
                ) or HISTORY_DAILY_API_BUDGET)),
            )
        except Exception:
            return HISTORY_DAILY_API_BUDGET

    def _history_calls_today(today_text):
        if collector.get_setting("backfill_api_calls_date", "") != today_text:
            collector.set_setting("backfill_api_calls_date", today_text)
            collector.set_setting("backfill_api_calls_count", "0")
            return 0
        try:
            return max(0, int(float(collector.get_setting("backfill_api_calls_count", "0") or 0)))
        except Exception:
            return 0

    def _add_history_calls(today_text, count):
        count = max(0, int(count or 0))
        current = _history_calls_today(today_text)
        collector.set_setting("backfill_api_calls_count", str(current + count))
        return current + count

    def backfill_history(progress=None):
        if not _HISTORY_RUN_LOCK.acquire(blocking=False):
            return 0
        try:
            collector.set_setting("backfill_enabled", "1")
            today = _today_kst()
            today_text = today.isoformat()
            cursor = _parse_date(
                collector.get_setting("backfill_next_date", ""),
                HISTORY_START,
            )
            if cursor < HISTORY_START:
                cursor = HISTORY_START

            if cursor > today:
                collector.set_setting("backfill_status", "완료")
                collector.set_setting("backfill_progress", "100")
                collector.set_setting("backfill_enabled", "0")
                collector.set_setting(
                    "backfill_message",
                    f"{HISTORY_START.isoformat()} ~ {today.isoformat()} 과거 조달자료 구축 완료",
                )
                return 0

            collector.set_setting("backfill_status", "실행중")
            collector.set_setting("backfill_started_at", dt.datetime.now(KST).isoformat(timespec="seconds"))
            collector.set_setting("backfill_error", "")
            saved_total = 0

            while cursor <= today:
                # The regular recent collector always has priority. If it starts
                # while one history chunk is running, finish the current chunk,
                # then yield so the scheduler can obtain SHOP_LOCK.
                if collector.get_setting("last_auto_sync_status", "") == "수집중":
                    current = collector.get_setting("last_auto_sync_current_source", "") or "자동수집"
                    collector.set_setting("backfill_status", "자동수집 대기")
                    collector.set_setting(
                        "backfill_message",
                        f"{current} 우선 실행 중 · 다음 구간 {cursor.isoformat()}부터 자동 재개",
                    )
                    return saved_total

                used, limit = collector.api_usage("shop")
                try:
                    reserve = max(
                        0,
                        int(float(collector.get_setting("auto_sync_api_reserve", "100") or 100)),
                    )
                except Exception:
                    reserve = 100
                ceiling = max(1, int(limit) - reserve)
                history_used = _history_calls_today(today_text)
                history_budget = _history_daily_budget()

                # Historical data gets its own daily budget. With the normal
                # 900-call limit this consumes at most 300 calls, leaving room
                # for the two-hour recent collector in addition to the 100-call
                # global safety reserve.
                if history_used >= history_budget:
                    collector.set_setting("backfill_status", "한도대기")
                    collector.set_setting("backfill_last_attempt_date", today_text)
                    collector.set_setting(
                        "backfill_message",
                        f"과거구축 오늘 {history_used:,}/{history_budget:,}회 사용 · "
                        f"최근 자동수집 용량 보존 · 다음 KST 날짜에 {cursor.isoformat()}부터 자동 재개",
                    )
                    return saved_total

                if int(used) >= ceiling:
                    collector.set_setting("backfill_status", "한도대기")
                    collector.set_setting("backfill_last_attempt_date", today_text)
                    collector.set_setting(
                        "backfill_message",
                        f"전체 API {used:,}/{limit:,}회 사용 · 안전여유 {reserve:,}회 보존 · "
                        f"다음 KST 날짜에 {cursor.isoformat()}부터 자동 재개",
                    )
                    return saved_total

                chunk_end = min(cursor + dt.timedelta(days=HISTORY_CHUNK_DAYS - 1), today)
                collector.set_setting("backfill_current_start", cursor.isoformat())
                collector.set_setting("backfill_current_end", chunk_end.isoformat())
                collector.set_setting(
                    "backfill_message",
                    f"수집중 · {cursor.isoformat()} ~ {chunk_end.isoformat()} · "
                    f"과거API {history_used:,}/{history_budget:,} · 전체API {used:,}/{limit:,}",
                )

                # Historical work must not replace the visible 'recent sync'
                # timestamp/result used by the operating dashboard.
                previous_last_sync = collector.get_setting("last_sync", "")
                previous_last_result = collector.get_setting("last_sync_result", "")
                api_before = int(used or 0)
                try:
                    saved = collector.sync_shopping_period(cursor.isoformat(), chunk_end.isoformat())
                except collector.ApiQuotaReached:
                    collector.set_setting("backfill_status", "한도대기")
                    collector.set_setting("backfill_last_attempt_date", today_text)
                    collector.set_setting(
                        "backfill_message",
                        f"API 호출한도 도달 · {cursor.isoformat()}부터 다음 KST 날짜에 자동 재개",
                    )
                    return saved_total
                except Exception as exc:
                    collector.set_setting("backfill_status", "오류")
                    collector.set_setting("backfill_error", str(exc))
                    collector.set_setting("backfill_last_attempt_date", today_text)
                    collector.set_setting(
                        "backfill_message",
                        f"{cursor.isoformat()} ~ {chunk_end.isoformat()} 구축 실패: {exc}",
                    )
                    raise
                finally:
                    try:
                        api_after, _ = collector.api_usage("shop")
                        _add_history_calls(today_text, max(0, int(api_after or 0) - api_before))
                    except Exception:
                        pass
                    collector.set_setting("last_sync", previous_last_sync)
                    collector.set_setting("last_sync_result", previous_last_result)

                saved_total += int(saved or 0)
                next_date = chunk_end + dt.timedelta(days=1)
                pct = _progress_percent(next_date, today)
                collector.set_setting("backfill_last_completed_date", chunk_end.isoformat())
                collector.set_setting("backfill_next_date", next_date.isoformat())
                collector.set_setting("backfill_progress", str(pct))
                collector.set_setting("backfill_saved_last_run", str(saved_total))
                collector.set_setting(
                    "backfill_message",
                    f"{chunk_end.isoformat()}까지 완료 · 이번 실행 저장·갱신 {saved_total:,}건 · {pct}%",
                )
                if callable(progress):
                    try:
                        progress(pct)
                    except Exception:
                        pass
                cursor = next_date
                time.sleep(0.35)

            collector.set_setting("backfill_status", "완료")
            collector.set_setting("backfill_progress", "100")
            collector.set_setting("backfill_enabled", "0")
            collector.set_setting("backfill_finished_at", dt.datetime.now(KST).isoformat(timespec="seconds"))
            collector.set_setting(
                "backfill_message",
                f"{HISTORY_START.isoformat()} ~ {today.isoformat()} 구축 완료 · "
                f"이번 실행 저장·갱신 {saved_total:,}건",
            )
            return saved_total
        finally:
            collector.set_setting("backfill_current_start", "")
            collector.set_setting("backfill_current_end", "")
            _HISTORY_RUN_LOCK.release()

    # Keep every historical entry point on the same implementation. server.py's
    # start_backfill_thread resolves its module global at runtime, so replacing
    # s.backfill_three_years is sufficient for the existing POST /backfill route.
    collector.backfill_three_years = backfill_history
    g2b_sync.backfill_three_years = backfill_history
    s.backfill_three_years = backfill_history

    # ---------------------------------------------------------------
    # 3) Resume history automatically after quota reset / server restart.
    # ---------------------------------------------------------------
    def start_history_background_if_needed():
        global _HISTORY_THREAD
        if collector.get_setting("backfill_enabled", "0") != "1":
            return False
        status = collector.get_setting("backfill_status", "대기") or "대기"
        if status == "완료" or status == "오류":
            return False
        if collector.get_setting("manual_sync_active", "0") == "1":
            return False
        today_text = _today_kst().isoformat()
        if (
            status == "한도대기"
            and collector.get_setting("backfill_last_attempt_date", "") == today_text
        ):
            return False
        with _HISTORY_THREAD_LOCK:
            if _HISTORY_THREAD is not None and _HISTORY_THREAD.is_alive():
                return False
            _HISTORY_THREAD = threading.Thread(
                target=backfill_history,
                name="g2b-history-backfill",
                daemon=True,
            )
            _HISTORY_THREAD.start()
            return True

    original_procurement_auto = scheduler_module._run_procurement_auto

    def procurement_auto_with_history():
        result = original_procurement_auto()
        start_history_background_if_needed()
        return result

    scheduler_module._run_procurement_auto = procurement_auto_with_history

    # This deployment intentionally starts/resumes the requested history build.
    current_status = collector.get_setting("backfill_status", "")
    if current_status in ("", "비활성", "대기", "중단됨", "자동수집 대기", "한도대기"):
        collector.set_setting("backfill_enabled", "1")
        if current_status in ("", "비활성"):
            collector.set_setting("backfill_status", "대기")
            collector.set_setting("backfill_message", "2025-01-01부터 과거 조달자료 자동 구축 대기")

    # ---------------------------------------------------------------
    # 4) Vendor ranking: full-market denominator/rank, search only rows.
    # ---------------------------------------------------------------
    def vendors_html(qs):
        start, end, region = s.date_params(qs, 365)
        region = _normalize_region(region)
        q = (qs.get("q") or [""])[0].strip()

        market_where, market_vals = s.where_shop(start, end, region, "")
        market_total = s.scalar(
            f"SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {market_where}",
            market_vals,
        )
        market_rows = s.qrows(
            f"SELECT vendor_name,COUNT(*) cnt,SUM(quantity) qty,SUM(supply_amount) amount,"
            f"COUNT(DISTINCT demand_org) orgs,COUNT(DISTINCT item_id) products "
            f"FROM shopping_contracts WHERE {market_where} "
            f"GROUP BY vendor_name ORDER BY amount DESC",
            market_vals,
        )
        vendor_count = len(market_rows)
        rank_map = {str(r["vendor_name"]): i for i, r in enumerate(market_rows, 1)}

        if q:
            filtered_where, filtered_vals = s.where_shop(start, end, region, q)
            rows = s.qrows(
                f"SELECT vendor_name,COUNT(*) cnt,SUM(quantity) qty,SUM(supply_amount) amount,"
                f"COUNT(DISTINCT demand_org) orgs,COUNT(DISTINCT item_id) products "
                f"FROM shopping_contracts WHERE {filtered_where} "
                f"GROUP BY vendor_name ORDER BY amount DESC LIMIT 1000",
                filtered_vals,
            )
            result_count = s.scalar(
                f"SELECT COUNT(DISTINCT vendor_name) FROM shopping_contracts WHERE {filtered_where}",
                filtered_vals,
            )
        else:
            rows = market_rows[:1000]
            result_count = vendor_count

        company = s.get_setting("company_name") or "우리회사"
        own_set = set(s.company_names())
        own_ranks = [rank_map[name] for name in own_set if name in rank_map]
        own_rank = min(own_ranks) if own_ranks else None

        tr = []
        for r in rows:
            vendor_name = str(r["vendor_name"] or "")
            market_rank = rank_map.get(vendor_name, "-")
            href = s.link("/vendor", name=vendor_name, start=start, end=end, region=region)
            share = float(r["amount"] or 0) / float(market_total or 1) * 100 if market_total else 0
            cls = ' class="ownrow"' if vendor_name in own_set else ""
            tr.append(
                f'<tr{cls}><td>{market_rank}</td><td><a class="itemid" href="{href}">{s.esc(vendor_name)}</a></td>'
                f'<td class="num">{int(r["cnt"] or 0):,}</td><td class="num">{int(r["orgs"] or 0):,}</td>'
                f'<td class="num">{int(r["products"] or 0):,}</td><td class="num">{s.money(r["amount"])}</td>'
                f'<td class="num">{share:.2f}%</td></tr>'
            )

        search_notice = ""
        if q:
            search_notice = (
                f'<div class="notice">검색 결과 {int(result_count or 0):,}개 업체 · '
                '순위와 시장총액은 검색어와 무관하게 같은 기간·지역의 전체 시장 기준입니다.</div>'
            )

        body = f'''{s.pathbar('/g2b/vendor_rank.php','lighting-sketch / g2b / analytics')}
<section class="card page"><h2>업체별 수주조회</h2>
<form class="filters"><div class="filterline"><strong>기간</strong><input type="date" name="start" value="{s.esc(start)}"><span>~</span><input type="date" name="end" value="{s.esc(end)}"><strong>지역</strong><select name="region">{s.regopts(region)}</select><strong>업체/기관</strong><input class="q" name="q" value="{s.esc(q)}"><button class="primary">검색</button></div></form>
<div class="kpis"><div><span>시장 총액</span><strong>{s.money(market_total)} 원</strong></div><div><span>참여 업체수</span><strong>{vendor_count:,} 사</strong></div><div><span>{s.esc(company)} 전체시장 순위</span><strong>{own_rank if own_rank else '-'} 위</strong></div></div>
{search_notice}<div class="tablewrap"><table><thead><tr><th>전체시장 순위</th><th>업체명</th><th>수주건수</th><th>수요기관수</th><th>제품수</th><th>공급금액</th><th>시장 점유율</th></tr></thead><tbody>{''.join(tr) or '<tr><td colspan="7" class="empty">결과 없음</td></tr>'}</tbody></table></div></section>'''
        return s.base_html(body, "업체별수주조회")

    s.vendors_html = vendors_html

    # ---------------------------------------------------------------
    # 5) Settings UI: reactivate the existing history button and explain resume.
    # ---------------------------------------------------------------
    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        page = page.replace(
            "자동수집과 수동수집은 동시에 실행되지 않으며, 과거자료 구축은 아직 비활성화 상태입니다.",
            "최근 자동수집을 우선하며, 2025-01-01부터 과거자료를 API 안전여유를 남기고 구축합니다. 한도에 도달하면 다음 KST 날짜에 자동 재개합니다.",
            1,
        )
        page = page.replace(
            '<button class="btn danger-lite" type="button" disabled>과거 구축 · 후속 검증버전에서 활성화</button>',
            '<button class="btn danger-lite" onclick="return confirm(\'2025-01-01부터 과거 조달자료 구축을 시작/재개합니다. API 안전여유를 남기고 한도 도달 시 다음 날 자동 재개합니다. 시작할까요?\')">2025-01-01부터 과거 구축/재개</button>',
            1,
        )
        page = page.replace("<b>3년 구축 상태:</b>", "<b>과거 구축 상태:</b>", 1)

        status = s.get_setting("backfill_status", "대기") or "대기"
        next_date = s.get_setting("backfill_next_date", "") or HISTORY_START.isoformat()
        completed = s.get_setting("backfill_last_completed_date", "") or "-"
        history_calls = _history_calls_today(_today_kst().isoformat())
        history_budget = _history_daily_budget()
        history_info = (
            '<div class="notice" style="margin:12px 0">'
            f'<b>과거 조달자료:</b> {s.esc(status)} · 완료구간 {s.esc(completed)} · 다음구간 {s.esc(next_date)}<br>'
            f'과거 구축 API 오늘 {history_calls:,}/{history_budget:,}회 · '
            f'{s.esc(s.get_setting("backfill_message", "2025-01-01부터 자동 구축 대기"))}'
            '</div>'
        )
        marker = '<div class="progress"><div><b>과거 구축 상태:</b>'
        if marker in page and "<b>과거 조달자료:</b>" not in page:
            page = page.replace(marker, history_info + marker, 1)
        return page

    s.settings_html = settings_html
    s._history_vendor_fix_applied = True
    return s
