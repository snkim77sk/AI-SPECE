"""2.1 UI status for automatic collection."""
import re

VERSION = "2.1"


def apply_v210_ui():
    import server as s

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)

        notice = f"""
<div class="notice" style="margin:12px 0 16px">
  <b>2.1 자동수집 운영</b><br>
  검증된 2.0 단일 수집엔진으로 <b>쇼핑몰 · 물품입찰 · 용역을 2시간마다 자동 갱신</b>합니다.<br>
  자동수집: {s.esc(s.get_setting('auto_sync_enabled','0'))} · 주기: {s.esc(s.get_setting('auto_sync_hours','2'))}시간 · 최근 재조회: {s.esc(s.get_setting('auto_sync_days','14'))}일<br>
  최근 상태: {s.esc(s.get_setting('last_auto_sync_status','대기'))} · 최근 완료: {s.esc(s.get_setting('last_auto_sync_finished') or '-')}<br>
  {s.esc(s.get_setting('last_auto_sync_result') or '자동수집 대기')}<br>
  과거자료 구축은 아직 비활성화 상태입니다.
</div>"""
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "2.1 자동수집 운영" not in page:
            page = page.replace(marker, notice + marker, 1)

        page = re.sub(
            r'<button class="btn danger-lite"[^>]*>최근 3년 구축 시작</button>',
            '<button class="btn danger-lite" type="button" disabled>과거 구축 · 후속 안정버전에서 활성화</button>',
            page,
            count=1,
        )
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
