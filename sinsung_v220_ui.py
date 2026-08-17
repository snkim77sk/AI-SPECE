"""2.2 automatic collection stability status UI."""
import re

VERSION = "2.2"


def apply_v220_ui():
    import server as s

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

        notice = f"""
<div class="notice" style="margin:12px 0 16px">
  <b>2.2 자동수집 안정화</b><br>
  쇼핑몰 · 물품입찰 · 용역을 <b>2시간마다</b> 동일한 단일 수집엔진으로 갱신합니다.<br>
  상태: {s.esc(s.get_setting('last_auto_sync_status','대기'))}
  · 현재 작업: {s.esc(current)}
  · 수동수집: {s.esc(manual)}<br>
  최근 시작: {s.esc(s.get_setting('last_auto_sync_started') or '-')}
  · 최근 완료: {s.esc(s.get_setting('last_auto_sync_finished') or '-')}
  · 다음 예정: {s.esc(next_due)}<br>
  Scheduler heartbeat: {s.esc(heartbeat)}
  · 연속 오류: {s.esc(failures)}회
  · 최근 재조회: {s.esc(effective_days)}일<br>
  API 안전여유: 종류별 {s.esc(reserve)}회
  · 장시간 서버 중단 시 최대 30일까지 자동 보충조회<br>
  {s.esc(s.get_setting('last_auto_sync_result') or '자동수집 대기')}<br>
  자동수집과 수동수집은 동시에 실행되지 않으며, 과거자료 구축은 아직 비활성화 상태입니다.
</div>"""
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "2.2 자동수집 안정화" not in page:
            page = page.replace(marker, notice + marker, 1)

        # 2.2 operating cadence is intentionally fixed to two hours. Keep the
        # existing form field visible but prevent accidental edits.
        page = re.sub(
            r'(<input type="number" min="1" name="auto_sync_hours" value="[^"]*")',
            r'\1 readonly title="2.2 안정화판은 2시간 고정"',
            page,
            count=1,
        )

        page = re.sub(
            r'<button class="btn danger-lite"[^>]*>최근 3년 구축 시작</button>',
            '<button class="btn danger-lite" type="button" disabled>과거 구축 · 후속 검증버전에서 활성화</button>',
            page,
            count=1,
        )
        return page

    s.settings_html = settings_html
    return s
