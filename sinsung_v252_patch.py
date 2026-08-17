"""v2.5.2 patch: surface the budget API key controls on the main settings page."""
import datetime as dt
import os

VERSION = "2.5.2-sinsung-settings-budget-key"


def apply_v252_patch():
    import server as s
    import sinsung_budget_monitor as bm

    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)

        env_key = bool(os.getenv("LOFIN_API_KEY"))
        configured = bm.budget_api_configured()
        if env_key:
            placeholder = "서버 환경변수(LOFIN_API_KEY)로 설정됨"
            disabled = " disabled"
            status_text = "예산 API 연결됨"
        elif configured:
            placeholder = "인증키 저장됨 · 변경할 때만 새 키 입력"
            disabled = ""
            status_text = "예산 API 인증키 저장됨"
        else:
            placeholder = "공공데이터포털 지방재정365 서비스키 입력"
            disabled = ""
            status_text = "예산 API 미연결"

        auto_checked = " checked" if s.get_setting("budget_auto_sync_enabled", "1") == "1" else ""
        year = s.TODAY.year
        today = dt.date.today().isoformat()

        box = f'''
<section class="panel" style="margin:14px 0 18px;padding:16px;border:1px solid #cbd5e1;border-radius:10px">
  <h3 style="margin-top:0">예산 API 설정</h3>
  <p><b>{s.esc(status_text)}</b> · 지방재정365 세부사업별 세출현황 실데이터 수집용</p>
  <form method="post" action="/budget-settings" class="settings">
    {s.csrf_input('/budget-settings')}
    <label>예산 API 인증키
      <input type="password" name="lofin_api_key" placeholder="{s.esc(placeholder)}"{disabled}>
    </label>
    <label style="display:flex;align-items:center;gap:8px;margin-top:8px">
      <input type="checkbox" name="budget_auto_sync_enabled" value="1"{auto_checked}> 매일 1회 현재연도 예산 자동수집
    </label>
    <button class="primary" type="submit" style="margin-top:10px">예산 인증키 저장</button>
  </form>
  <form method="post" action="/budget-api-test" style="margin-top:8px">
    {s.csrf_input('/budget-api-test')}
    <input type="hidden" name="year" value="{year}">
    <input type="hidden" name="snapshot_date" value="{today}">
    <button class="btn" type="submit">예산 API 연결 테스트</button>
  </form>
  <small>나라장터 공공데이터포털 서비스키와 별도로 저장됩니다. 저장된 키는 화면에 다시 표시하지 않습니다.</small>
</section>
'''

        marker = '<form method="post" action="/settings">'
        if marker in page:
            page = page.replace(marker, box + marker, 1)
        else:
            fallback = '<section class="card settings">'
            if fallback in page:
                page = page.replace(fallback, fallback + box, 1)
        return page

    s.settings_html = settings_html
    return s
