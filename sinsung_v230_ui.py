"""2.3 history backfill controls and status UI."""
import re

from sinsung_v230_backfill import (
    request_backfill_stop,
    start_backfill_thread,
)

VERSION = "2.3"


def apply_v230_ui():
    import server as s

    original_settings_html = s.settings_html
    original_do_post = s.Handler.do_POST

    # Replace the legacy disabled implementation used by server.py.
    s.start_backfill_thread = start_backfill_thread

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)

        status = s.get_setting("backfill_status", "대기") or "대기"
        progress = s.get_setting("backfill_progress", "0") or "0"
        current = s.get_setting("backfill_current_source", "") or "-"
        cursor = s.get_setting("backfill_cursor_date", "2025-01-01") or "2025-01-01"
        source = s.get_setting("backfill_cursor_source", "shop") or "shop"
        target = s.get_setting("backfill_target_end", "") or "오늘"
        next_resume = s.get_setting("backfill_next_resume", "") or "-"
        started = s.get_setting("backfill_started_at", "") or "-"
        finished = s.get_setting("backfill_finished_at", "") or "-"
        saved_shop = s.get_setting("backfill_saved_shop", "0") or "0"
        saved_bids = s.get_setting("backfill_saved_bids", "0") or "0"
        saved_services = s.get_setting("backfill_saved_services", "0") or "0"
        last_chunk = s.get_setting("backfill_last_chunk", "") or "-"
        message = s.get_setting("backfill_message", "2025 과거자료 구축 대기")
        reserve = s.get_setting("backfill_api_reserve", "200") or "200"

        notice = f"""
<div class="notice" style="margin:12px 0 16px">
  <b>2.3 · 2025 과거자료 구축</b><br>
  범위: <b>2025-01-01 ~ {s.esc(target)}</b> · 7일 단위 체크포인트 · 쇼핑몰 → 물품입찰 → 용역 순차 구축<br>
  상태: <b>{s.esc(status)}</b> · 진행률: <b>{s.esc(progress)}%</b> · 현재 작업: {s.esc(current)}<br>
  다음 위치: {s.esc(cursor)} / {s.esc(source)} · 최근 구간: {s.esc(last_chunk)}<br>
  저장·갱신 누적: 쇼핑몰 {s.esc(saved_shop)}건 · 물품입찰 {s.esc(saved_bids)}건 · 용역 {s.esc(saved_services)}건<br>
  시작: {s.esc(started)} · 완료: {s.esc(finished)} · 다음 재개: {s.esc(next_resume)}<br>
  API 보호: 종류별 <b>{s.esc(reserve)}회</b>를 실시간 자동수집용으로 남기며, 2시간 자동수집이 가까우면 과거 구축이 먼저 양보합니다.<br>
  {s.esc(message)}
</div>"""
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "2.3 · 2025 과거자료 구축" not in page:
            page = page.replace(marker, notice + marker, 1)

        # Re-enable the existing /backfill form with the explicit 2025 policy.
        page = re.sub(
            r'<button class="btn danger-lite" type="button" disabled>과거 구축 · 후속 검증버전에서 활성화</button>',
            '<button class="btn danger-lite" onclick="return confirm(\'2025-01-01부터 현재까지 쇼핑몰·물품입찰·용역 과거자료 구축을 시작/재개할까요?\')">2025-01-01 과거자료 구축 시작/재개</button>',
            page,
            count=1,
        )
        page = page.replace("3년 구축 상태:", "2025 과거 구축 상태:")

        stop_form = f"""
<form method="post" action="/backfill-stop" style="margin-top:8px">
  {s.csrf_input('/backfill-stop')}
  <button class="btn" onclick="return confirm('현재 구간 처리가 끝난 뒤 과거자료 구축을 중지할까요?')">과거자료 구축 중지</button>
</form>"""
        progress_marker = '<div class="progress">'
        if progress_marker in page and 'action="/backfill-stop"' not in page:
            page = page.replace(progress_marker, stop_form + progress_marker, 1)
        return page

    def do_POST(self):
        path = s.urlparse(self.path).path
        if path in ("/backfill", "/backfill-stop"):
            try:
                form = self.parse_post()
                if self.require_auth(path):
                    return
                if not s.valid_csrf(path, form):
                    return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)

                if path == "/backfill":
                    ok = start_backfill_thread()
                    if ok:
                        text = "2025-01-01 과거자료 구축을 백그라운드에서 시작/재개했습니다. 2시간 자동수집은 그대로 유지됩니다."
                        return self.redirect("/settings?msg=" + s.quote(text))
                    text = s.get_setting("backfill_message", "과거자료 구축을 시작하지 못했습니다.")
                    return self.redirect("/settings?error=1&msg=" + s.quote(text))

                ok = request_backfill_stop()
                text = "과거자료 구축 중지를 요청했습니다." if ok else "현재 실행 중인 과거자료 구축이 없습니다."
                return self.redirect("/settings?msg=" + s.quote(text))
            except Exception as exc:
                return self.redirect("/settings?error=1&msg=" + s.quote(f"과거자료 구축 제어 오류: {exc}"))
        return original_do_post(self)

    s.settings_html = settings_html
    s.Handler.do_POST = do_POST
    s.APP_VERSION = VERSION
    return s
