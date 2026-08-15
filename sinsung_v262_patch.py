"""v2.6.2: surface non-standard API error envelopes and restore the working source.

Two findings from the v2.6.1 parameter probe:

1. getSpcifyPrdlstPrcureInfoList answers with

       {"nkoneps.com.response.ResponseError":
           {"header": {"resultCode": "08", "resultMsg": "필수값 입력 오류"}}}

   g2b_sync._parse_response only looks at data["response"]["header"] and
   data["header"], so this envelope produced no header, defaulted resultCode to
   "00" and was reported as a plain "0건". Every real error of this shape has
   been invisible.

2. The stored collection diagnostic shows getDlvrReqDtlInfoList returning
   12,987 raw rows, of which 453 matched the 12 detail-item codes and were
   saved. The delivery-detail operation and the classification codes are both
   correct. v2.6.0 switched away from that source based on the "0건" symptom
   caused by finding 1.

This patch therefore fixes the parser and points shopping collection back at
getDlvrReqDtlInfoList, keeping monthly chunking and the resumable backfill.
"""
import datetime as dt
import time

from db import get_setting, set_setting

VERSION = "2.6.2-sinsung-error-envelope-fix"

HISTORY_START = "2025-01-01"
ROWS_PER_PAGE = 999
API_RESERVE = 100


# ── 1. 비표준 오류 봉투 인식 ────────────────────────────────────────────────

def _find_result_header(data, depth=0):
    """응답 어디에 있든 resultCode/resultMsg 헤더를 찾는다.

    조달청 게이트웨이는 정상 응답과 오류 응답의 최상위 키가 다르다.
    - 정상: {"response": {"header": {...}, "body": {...}}}
    - 오류: {"nkoneps.com.response.ResponseError": {"header": {...}}}
    """
    if not isinstance(data, dict) or depth > 3:
        return None
    if "resultCode" in data or "resultCd" in data:
        return data
    for value in data.values():
        if isinstance(value, dict):
            found = _find_result_header(value, depth + 1)
            if found:
                return found
    return None


def apply_parse_fix(g):
    original_parse = g._parse_response

    def parse_with_envelope(raw):
        import json

        stripped = raw.strip() if isinstance(raw, (bytes, bytearray)) else raw
        # JSON 응답만 추가 검사한다. XML 경로는 원본 로직이 이미 처리한다.
        if isinstance(stripped, (bytes, bytearray)) and stripped[:1] in (b"{", b"["):
            try:
                data = json.loads(stripped.decode("utf-8-sig"))
            except Exception:
                data = None
            if isinstance(data, dict):
                header = _find_result_header(data)
                if header:
                    code = str(header.get("resultCode", header.get("resultCd", "00")) or "00")
                    msg = str(header.get("resultMsg", header.get("resultMessage", "")) or "")
                    if code not in ("00", "0", ""):
                        set_setting("last_api_error_envelope", f"resultCode={code} · {msg}")
                        if code == "22":
                            raise g.ApiQuotaReached(f"API 일일 호출 제한 {code}: {msg}")
                        if code == "23":
                            raise g.ApiRateLimited(f"API 초당 호출 제한 {code}: {msg}")
                        raise RuntimeError(f"API 오류 {code}: {msg}")
        return original_parse(raw)

    g._parse_response = parse_with_envelope
    return original_parse


# ── 2. 정상 동작하던 납품요구상세 경로로 복귀 ──────────────────────────────

def _collect_delivery_range(start_date, end_date, log_type="SHOPPING", progress=None):
    """getDlvrReqDtlInfoList를 월 단위로 조회한다.

    g2b_sync의 fetch_shop_page / upsert_shop / month_chunks 를 그대로 사용한다.
    이 함수들은 v2.6.0 패치에서 교체되지 않았으므로 원본 동작이 보존돼 있다.
    """
    import g2b_sync as g

    try:
        dt.date.fromisoformat(start_date)
        dt.date.fromisoformat(end_date)
    except Exception as exc:
        raise ValueError("수집 시작일/종료일 형식이 올바르지 않습니다.") from exc
    if start_date > end_date:
        raise ValueError("수집 시작일이 종료일보다 늦습니다.")

    chunks = g.month_chunks(start_date, end_date)
    log_id = g.new_sync_log(log_type, start_date, end_date)

    total_raw = total_saved = total_matched = total_skipped = 0
    per_chunk = []

    try:
        with g.SHOP_LOCK:
            for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
                cs = chunk_start.isoformat()
                ce = chunk_end.isoformat()
                page = 1
                chunk_raw = chunk_saved = 0
                total = None

                set_setting("last_shop_chunk", f"{cs} ~ {ce} ({index}/{len(chunks)})")

                while True:
                    used, limit = g.api_usage("shop")
                    if used >= max(1, limit - API_RESERVE):
                        raise g.ApiQuotaReached(
                            f"쇼핑몰 API 안전여유 {API_RESERVE}회를 남기기 위해 중단합니다. "
                            f"오늘 {used:,}/{limit:,}회 사용."
                        )

                    items, total = g.fetch_shop_page(cs, ce, page=page, rows=ROWS_PER_PAGE)
                    total = int(total or 0)
                    if not items:
                        break

                    if total_raw == 0:
                        set_setting("last_shop_first_fields",
                                    ",".join(sorted(str(k) for k in items[0].keys())))

                    saved, matched, skipped = g.upsert_shop(items, target_only=True)
                    n = len(items)
                    chunk_raw += n
                    chunk_saved += saved
                    total_raw += n
                    total_saved += saved
                    total_matched += matched
                    total_skipped += skipped

                    set_setting("last_shop_raw_count", str(total_raw))
                    set_setting("last_shop_matched_count", str(total_matched))
                    set_setting("last_shop_saved_count", str(total_saved))
                    set_setting("last_shop_skipped_count", str(total_skipped))

                    if progress:
                        pct = min(99, int(index / max(1, len(chunks)) * 100))
                        progress(pct, total_saved)

                    if total <= chunk_raw:
                        break
                    page += 1
                    time.sleep(0.12)

                per_chunk.append(f"{cs[:7]}:원본{chunk_raw:,}/저장{chunk_saved:,}")
                set_setting("v262_chunk_diag", " | ".join(per_chunk[-24:]))

        result = (
            f"{start_date} ~ {end_date} · 납품요구상세({len(chunks)}개월) · "
            f"원본 {total_raw:,}건 / 대상 {total_matched:,}건 / 저장·갱신 {total_saved:,}건 / "
            f"필수값 누락 {total_skipped:,}건"
        )
        set_setting("last_sync", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        set_setting("last_sync_result", result)
        set_setting("last_shop_error", "")
        set_setting("last_shop_chunk", "")
        g.finish_sync_log(log_id, "OK", total_saved, result)
        return {"raw": total_raw, "saved": total_saved, "matched": total_matched,
                "skipped": total_skipped, "diag": " | ".join(per_chunk)}

    except g.ApiQuotaReached as exc:
        g.finish_sync_log(log_id, "PAUSED", total_saved, str(exc))
        raise
    except Exception as exc:
        set_setting("last_shop_error", str(exc))
        g.finish_sync_log(log_id, "ERROR", total_saved, str(exc))
        raise


def sync_shopping_period_delivery(start_date, end_date, max_pages=2000):
    return int(_collect_delivery_range(start_date, end_date)["saved"])


def build_history_delivery(progress=None):
    """2025-01-01부터 오늘까지 구축. 호출한도에 걸리면 다음 실행 때 이어서 진행."""
    today = dt.date.today().isoformat()
    cursor = get_setting("v262_cursor", "") or HISTORY_START
    try:
        dt.date.fromisoformat(cursor)
    except Exception:
        cursor = HISTORY_START

    set_setting("backfill_status", "실행중")
    set_setting("backfill_message", f"납품요구상세 방식으로 {cursor} 이후 자료를 구축 중입니다.")

    def report(pct, saved):
        set_setting("backfill_progress", str(pct))
        set_setting("backfill_total_saved", str(saved))
        set_setting("backfill_message", f"납품요구상세 구축 {pct}% · 저장·갱신 {saved:,}건")
        if progress:
            progress(pct, saved)

    try:
        info = _collect_delivery_range(cursor, today, log_type="SHOPPING-HISTORY", progress=report)
        set_setting("backfill_status", "완료")
        set_setting("backfill_progress", "100")
        set_setting("backfill_total_saved", str(info["saved"]))
        set_setting("v262_cursor", "")
        set_setting("shop_history_build_completed", "1")
        set_setting("shop_history_build_completed_at", dt.datetime.now().isoformat(timespec="seconds"))
        set_setting("backfill_message",
                    f"{cursor} ~ {today} 구축 완료 · 원본 {info['raw']:,}건 / 대상 {info['matched']:,}건 / "
                    f"저장·갱신 {info['saved']:,}건 · 이후 2시간마다 최근분 자동수집")
        return int(info["saved"])
    except Exception as exc:
        import g2b_sync as g
        if isinstance(exc, g.ApiQuotaReached):
            chunk = get_setting("last_shop_chunk", "")
            if chunk:
                set_setting("v262_cursor", chunk.split(" ~ ")[0].strip())
            set_setting("backfill_status", "호출한도 대기")
            set_setting("backfill_message", f"{exc} 다음 실행 시 중단 지점부터 재개합니다.")
            return int(float(get_setting("backfill_total_saved", "0") or 0))
        set_setting("backfill_status", "오류")
        set_setting("backfill_message", str(exc))
        raise


def test_shopping_delivery():
    """납품요구상세 연결 테스트. 최근 14일을 조회한다."""
    import g2b_sync as g
    end = dt.date.today()
    start = end - dt.timedelta(days=14)
    items, total = g.fetch_shop_page(start.isoformat(), end.isoformat(), 1, 10)
    total = int(total or 0)
    set_setting("shop_specific_test_result",
                f"연결 성공 · getDlvrReqDtlInfoList · 최근 14일 원본 {total:,}건")
    if not items and total <= 0:
        raise RuntimeError(
            f"납품요구상세 조회가 0건입니다({start} ~ {end}). "
            f"직전 API 오류: {get_setting('last_api_error_envelope', '없음')}"
        )
    return len(items), total


def _collect_recent(hours=6):
    """2시간 주기 최근분 갱신. 최근 며칠을 다시 훑어 UPSERT로 변경분을 반영한다."""
    days = max(1, min(90, int(float(get_setting("auto_sync_days", "14") or 14))))
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    return _collect_delivery_range(start.isoformat(), end.isoformat(), log_type="SHOPPING-2H")


def apply_v262_patch():
    import g2b_sync as g
    import scheduler as sch
    import server as s

    # 1) 오류 봉투 인식
    apply_parse_fix(g)

    # 2) 쇼핑몰 수집 원천 복귀
    g.sync_shopping_period = sync_shopping_period_delivery
    s.sync_shopping_period = sync_shopping_period_delivery
    g.backfill_three_years = build_history_delivery
    s.backfill_three_years = build_history_delivery
    sch.backfill_three_years = build_history_delivery
    g.test_shopping_api = test_shopping_delivery
    s.test_shopping_api = test_shopping_delivery

    # v2.6.1 진단은 수동 실행용으로 남겨두되 자동 경로에서는 쓰지 않는다.
    set_setting("v261_active", "0")

    # 3) 스케줄러: 2시간마다 쇼핑몰 최근분 + 입찰/용역
    def worker():
        while True:
            try:
                enabled = str(get_setting("auto_sync_enabled", "0")).lower() in ("1", "true", "yes", "on")
                if enabled and get_setting("api_key"):
                    now = dt.datetime.now()
                    hours = max(1, int(float(get_setting("auto_sync_hours", "2") or 2)))

                    last_shop = get_setting("last_shop_2h_attempt", "")
                    due_shop = True
                    if last_shop:
                        try:
                            due_shop = (now - dt.datetime.fromisoformat(last_shop)).total_seconds() >= hours * 3600
                        except Exception:
                            due_shop = True
                    if due_shop and get_setting("backfill_status", "") == "완료":
                        set_setting("last_shop_2h_attempt", now.isoformat(timespec="seconds"))
                        try:
                            info = _collect_recent()
                            set_setting("last_shop_2h_result",
                                        f"최근분 자동수집 · 원본 {info['raw']:,}건 / 저장·갱신 {info['saved']:,}건")
                        except Exception as exc:
                            set_setting("last_shop_2h_result", f"최근분 자동수집 실패: {exc}")

                    last_bs = get_setting("last_bidservice_auto_sync", "")
                    due_bs = True
                    if last_bs:
                        try:
                            due_bs = (now - dt.datetime.fromisoformat(last_bs)).total_seconds() >= hours * 3600
                        except Exception:
                            due_bs = True
                    if due_bs:
                        today = now.date()
                        days = max(1, min(90, int(float(get_setting("auto_sync_days", "14") or 14))))
                        bstart = max(today - dt.timedelta(days=days), today - dt.timedelta(days=27))
                        messages = []
                        try:
                            n = sch.sync_bids_period(bstart.isoformat(), today.isoformat())
                            messages.append(f"물품입찰 {n:,}건")
                        except Exception as exc:
                            messages.append(f"입찰 오류: {exc}")
                        try:
                            n = sch.sync_services_period(bstart.isoformat(), today.isoformat())
                            messages.append(f"용역 {n:,}건")
                        except Exception as exc:
                            messages.append(f"용역 오류: {exc}")
                        set_setting("last_bidservice_auto_sync", now.isoformat(timespec="seconds"))
                        set_setting("last_auto_bidservice_result", " / ".join(messages))

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

    # 4) 설정 화면 안내
    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        envelope = get_setting("last_api_error_envelope", "") or "없음"
        chunk = get_setting("v262_chunk_diag", "") or "-"
        block = (
            '<div class="notice"><b>수집 원천 (v2.6.2)</b><br>'
            '오퍼레이션: getDlvrReqDtlInfoList · 월 단위 분할 조회<br>'
            f'마지막 API 오류코드: {s.esc(envelope)}<br>'
            f'월별 수집: {s.esc(chunk)}<br>'
            '<small>v2.6.0이 사용하던 getSpcifyPrdlstPrcureInfoList는 resultCode 08(필수값 입력 오류)을 '
            '반환합니다. 그 오류가 비표준 응답 봉투에 담겨 있어 이전에는 0건으로만 보였습니다. '
            '이제 오류코드를 그대로 표시하며, 실제로 12,987건을 반환하던 납품요구상세 조회로 되돌렸습니다.</small></div>'
        )
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "수집 원천 (v2.6.2)" not in page:
            page = page.replace(marker, block + marker, 1)
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
