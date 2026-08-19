"""Integrate 지방교육재정알리미 education-office budgets into the existing monitor."""
import datetime as dt
import os

VERSION = "2.6.0-education-budget-ui"


def apply_education_budget():
    import education_budget_sync as edu
    import scheduler as scheduler_module
    import server as s

    if getattr(s, "_education_budget_applied", False):
        return

    edu.ensure_schema()
    original_page = s.budgets_html
    original_post = s.Handler.do_POST
    original_budget_auto = scheduler_module._run_budget_auto

    def _region_value(qs):
        if "region" in qs:
            value = (qs.get("region") or [""])[0]
        else:
            value = s.get_setting("default_region", "인천광역시")
        value = str(value or "").strip()
        return "" if value in ("", "__ALL__", "전국") else value

    def _source_counts(qs):
        try:
            year = int((qs.get("year") or [str(s.TODAY.year)])[0])
        except Exception:
            year = s.TODAY.year
        region = _region_value(qs)
        clauses = ["fiscal_year=?", "is_sample=0"]
        vals = [year]
        if region:
            clauses.append("region=?")
            vals.append(region)
        where = " AND ".join(clauses)
        local_n = s.scalar(
            f"SELECT COUNT(*) FROM budget_items WHERE {where} AND source NOT LIKE ?",
            vals + ["지방교육재정알리미%"],
        )
        edu_n = s.scalar(
            f"SELECT COUNT(*) FROM budget_items WHERE {where} AND source LIKE ?",
            vals + ["지방교육재정알리미%"],
        )
        return year, region, int(local_n or 0), int(edu_n or 0)

    def budgets_html(qs):
        page = original_page(qs)
        year, region, local_n, edu_n = _source_counts(qs)
        configured = edu.configured()
        env_key = bool(os.getenv("EDUINFO_API_KEY"))
        request_type = edu.get_request_type()
        status = s.get_setting("education_budget_sync_status", "대기")
        last = s.get_setting("last_education_budget_sync", "-") or "-"
        result = s.get_setting(
            "last_education_budget_sync_result",
            "교육청 예산 실데이터를 아직 수집하지 않았습니다.",
        )
        auto_checked = " checked" if s.get_setting("education_budget_auto_sync_enabled", "1") == "1" else ""
        key_placeholder = (
            "서버 환경변수(EDUINFO_API_KEY)로 설정됨" if env_key
            else ("인증키 저장됨 · 변경할 때만 입력" if configured else "지방교육재정알리미 OpenAPI 인증키 입력")
        )

        summary = (
            '<div class="notice" style="margin:12px 0">'
            f'<b>예산 통합원:</b> 지방재정365 {local_n:,}건 · 교육청 {edu_n:,}건'
            f' · {year}년 · {s.esc(region or "전국")}<br>'
            '교육청 데이터는 지방교육재정알리미 OpenAPI에서 별도 수집하여 같은 예산 DB에 합산합니다.'
            '</div>'
        )
        marker = '<div class="notice"><b>수집 상태:</b>'
        pos = page.find(marker)
        if pos >= 0:
            end = page.find("</div>", pos)
            if end >= 0:
                page = page[:end + 6] + summary + page[end + 6:]

        panel = f'''
<section class="panel" style="margin:16px 0;border:1px solid #cbd5e1;border-radius:10px;padding:16px">
  <h3>교육청 예산 연동 · 지방교육재정알리미</h3>
  <div class="notice"><b>상태:</b> {s.esc(status)} · 최근 수집 {s.esc(last)}<br>{s.esc(result)}</div>
  <form method="post" action="/education-budget-settings" class="settings">
    {s.csrf_input('/education-budget-settings')}
    <label>교육청 OpenAPI 인증키
      <input type="password" name="eduinfo_api_key" placeholder="{s.esc(key_placeholder)}"{' disabled' if env_key else ''}>
    </label>
    <label>OpenAPI 서비스명(requestType)
      <input name="eduinfo_request_type" value="{s.esc(request_type)}" placeholder="예: opclTotal">
    </label>
    <label style="display:flex;align-items:center;gap:8px">
      <input type="checkbox" name="education_budget_auto_sync_enabled" value="1"{auto_checked}> 매일 1회 교육청 예산 자동수집
    </label>
    <button class="primary" type="submit">교육청 연동 설정 저장</button>
  </form>
  <div class="actions" style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
    <form method="post" action="/education-budget-api-test">
      {s.csrf_input('/education-budget-api-test')}
      <input type="hidden" name="year" value="{year}">
      <button class="btn" type="submit">교육청 API 연결 테스트</button>
    </form>
    <form method="post" action="/sync-education-budget">
      {s.csrf_input('/sync-education-budget')}
      <input type="hidden" name="year" value="{year}">
      <button class="primary" type="submit">교육청 예산 수집 시작</button>
    </form>
  </div>
  <small>지방교육재정알리미 인증키는 지방재정365 및 나라장터 서비스키와 별도입니다. 데이터셋별 requestType이 다르므로 서비스명도 별도 저장합니다.</small>
</section>
'''
        # Insert after the consolidated admin zone when present; otherwise before the final page section closes.
        admin_marker = '<section class="panel" style="margin:16px 0"><h3>예산 데이터 관리</h3>'
        admin_pos = page.find(admin_marker)
        if admin_pos >= 0:
            admin_end = page.find("</section>", admin_pos)
            if admin_end >= 0:
                insert_at = admin_end + len("</section>")
                return page[:insert_at] + panel + page[insert_at:]
        close = page.rfind("</section>")
        if close >= 0:
            return page[:close] + panel + page[close:]
        return page + panel

    def do_POST(self):
        u = s.urlparse(self.path)
        if u.path not in ("/education-budget-settings", "/education-budget-api-test", "/sync-education-budget"):
            return original_post(self)
        try:
            form = self.parse_post()
            if self.require_auth(u.path):
                return
            if not s.valid_csrf(u.path, form):
                return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)

            if u.path == "/education-budget-settings":
                key = (form.get("eduinfo_api_key") or [""])[0].strip()
                request_type = (form.get("eduinfo_request_type") or [edu.DEFAULT_REQUEST_TYPE])[0].strip()
                if key and not os.getenv("EDUINFO_API_KEY"):
                    s.set_setting("eduinfo_api_key", key)
                if request_type and not os.getenv("EDUINFO_REQUEST_TYPE"):
                    s.set_setting("eduinfo_request_type", request_type)
                s.set_setting(
                    "education_budget_auto_sync_enabled",
                    "1" if "education_budget_auto_sync_enabled" in form else "0",
                )
                return self.redirect("/budgets?msg=" + s.quote("교육청 예산 연동 설정을 저장했습니다."))

            try:
                year = int((form.get("year") or [str(s.TODAY.year)])[0])
            except Exception:
                year = s.TODAY.year

            if u.path == "/education-budget-api-test":
                try:
                    n, total, code, message = edu.test_api(year)
                    msg = f"교육청 API 연결 성공: 첫 페이지 {n:,}건 / 전체 {total:,}건 · {code} {message}".strip()
                    err = 0
                except Exception as exc:
                    msg = f"교육청 API 연결 실패: {exc}"
                    err = 1
                return self.redirect(f"/budgets?year={year}&error={err}&msg=" + s.quote(msg))

            if not edu.configured():
                return self.redirect(
                    f"/budgets?year={year}&error=1&msg=" +
                    s.quote("지방교육재정알리미 OpenAPI 인증키를 먼저 저장해 주세요.")
                )
            started = edu.start_background_sync(year)
            msg = "교육청 예산 수집을 시작했습니다." if started else "교육청 예산 수집이 이미 진행 중입니다."
            return self.redirect(f"/budgets?year={year}&msg=" + s.quote(msg))
        except Exception as exc:
            return self.redirect("/budgets?error=1&msg=" + s.quote(str(exc)))

    def run_budget_auto_with_education():
        original_budget_auto()
        today = dt.date.today()
        today_text = today.isoformat()
        if s.get_setting("education_budget_auto_sync_enabled", "0") != "1" or not edu.configured():
            return
        if s.get_setting("last_education_budget_auto_attempt_date", "") == today_text:
            return
        s.set_setting("last_education_budget_auto_attempt_date", today_text)
        s.set_setting("education_budget_sync_status", "수집중")
        try:
            edu.sync_education_budget(today.year)
            s.set_setting("education_budget_sync_status", "완료")
        except Exception as exc:
            s.set_setting("education_budget_sync_status", "오류")
            s.set_setting("last_education_budget_sync_result", f"교육청 예산 자동수집 실패: {exc}")

    s.budgets_html = budgets_html
    s.Handler.do_POST = do_POST
    scheduler_module._run_budget_auto = run_budget_auto_with_education
    s._education_budget_applied = True
    return s
