"""2.0 UI guardrails for the clean collector phase."""
import re

VERSION = "2.0"


def apply_v200_ui():
    import server as s

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)

        notice = f"""
<div class="notice" style="margin:12px 0 16px">
  <b>2.0 클린 수집엔진 검증 단계</b><br>
  {s.esc(s.get_setting("v200_reset_summary", "데이터 초기화 대기"))}<br>
  자동수집은 OFF 상태입니다. 먼저 <b>쇼핑몰 API 연결 테스트 → 최근 7~14일 수동수집</b>을 확인해 주세요.<br>
  요청형식: {s.esc(s.get_setting("last_shop_request_profile", "-"))}
  · resultCode: {s.esc(s.get_setting("last_api_result_code", "-"))}
</div>"""
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "2.0 클린 수집엔진 검증 단계" not in page:
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
