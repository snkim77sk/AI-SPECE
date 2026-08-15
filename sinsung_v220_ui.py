"""2.3 automatic collection stability and 2025 history UI.

Kept in the existing v220 UI module to preserve the Cafe24-deployed file layout.
"""
import re

VERSION = "2.3"


def apply_v220_ui():
    import server as s
    from scheduler import start_history_backfill

    # Keep the original /backfill route in server.py but redirect its worker
    # function to the scheduler-integrated 2025 backfill implementation.
    s.start_backfill_thread = start_history_backfill

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)

        failures = s.get_setting('last_auto_sync_consecutive_failures', '0') or '0'
        current = s.get_setting('last_auto_sync_current_source', '') or '-'
        heartbeat = s.get_setting('scheduler_heartbeat', '') or '-'
        next_due = s.get_setting('next_auto_sync_due', '') or '-'
        effective_days = s.get_setting('last_auto_sync_effective_days', '14') or '14'
        reserve = s.get_setting('auto_sync_api_reserve', '100') or '100'
        manual = '진행중' if s.get_setting('manual_sync_active', '0') == '1' else '대기'

        history_status = s.get_setting('backfill_status', '대기') or '대기'
        history_progress = s.get_setting('backfill_progress', '0') or '0'
        history_current = s.get_setting('backfill_current_source', '') or '-'
        history_cursor = s.get_setting('backfill_cursor_date', '2025-01-01') or '2025-01-01'
        history_target = s.get_setting('backfill_target_end', '') or '오늘'
        history_chunk = s.get_setting('backfill_last_chunk', '') or '-'
        history_shop = s.get_setting('backfill_saved_shop', '0') or '0'
        history_bids = s.get_setting('backfill_saved_bids', '0') or '0'
        history_services = s.get_setting('backfill_saved_services', '0') or '0'
        history_reserve = s.get_setting('backfill_api_reserve', '200') or '200'
        history_msg = s.get_setting('backfill_message', '2025 과거자료 구축 대기') or '2025 과거자료 구축 대기'

        notice = f"""
<div class="notice" style="margin:12px 0 16px">
  <b>2.3 자동수집 안정화 + 2025 과거자료 구축</b><br>
  쇼핑몰 · 물품입찰 · 용역은 <b>2시간마다</b> 기존 단일 수집엔진으로 갱신합니다.<br>
  자동수집 상태: {s.esc(s.get_setting('last_auto_sync_status','대기'))}
  · 현재 작업: {s.esc(current)}
  · 수동수집: {s.esc(manual)}<br>
  최근 완료: {s.esc(s.get_setting('last_auto_sync_finished') or '-')}
  · 다음 예정: {s.esc(next_due)}
  · Scheduler heartbeat: {s.esc(heartbeat)}<br>
  연속 오류: {s.esc(failures)}회 · 최근 재조회: {s.esc(effective_days)}일 · 자동수집 API 안전여유: 종류별 {s.esc(reserve)}회<br><br>
  <b>2025 과거자료</b> · 상태: <b>{s.esc(history_status)}</b> · 진행률: <b>{s.esc(history_progress)}%</b><br>
  범위: 2025-01-01 ~ {s.esc(history_target)} · 다음 날짜: {s.esc(history_cursor)} · 현재 작업: {s.esc(history_current)}<br>
  최근 구간: {s.esc(history_chunk)}<br>
  누적 저장·갱신: 쇼핑몰 {s.esc(history_shop)}건 · 물품입찰 {s.esc(history_bids)}건 · 용역 {s.esc(history_services)}건<br>
  과거구축은 API {s.esc(history_reserve)}회를 실시간 자동수집용으로 남기며, <b>2시간 자동수집이 항상 우선</b>입니다.<br>
  {s.esc(history_msg)}
</div>"""
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "2.3 자동수집 안정화 + 2025 과거자료 구축" not in page:
            page = page.replace(marker, notice + marker, 1)

        page = re.sub(
            r'(<input type="number" min="1" name="auto_sync_hours" value="[^"]*")',
            r'\1 readonly title="2.3 안정화판은 2시간 고정"',
            page,
            count=1,
        )

        # Replace either the original legacy button or the 2.2 disabled button.
        page = re.sub(
            r'<button class="btn danger-lite"[^>]*>(?:최근 3년 구축 시작|과거 구축 · 후속 검증버전에서 활성화)</button>',
            '<button class="btn danger-lite" onclick="return confirm(\'2025-01-01부터 현재까지 쇼핑몰·물품입찰·용역 과거자료 구축을 시작/재개할까요? 2시간 자동수집은 계속 우선 실행됩니다.\')">2025-01-01 과거자료 구축 시작/재개</button>',
            page,
            count=1,
        )
        page = page.replace('3년 구축 상태:', '2025 과거 구축 상태:')
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
