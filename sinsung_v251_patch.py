"""v2.5.1 stability patch: explicit nationwide sentinel + visible budget API connection box."""
import datetime as dt
import os
import urllib.parse

VERSION = "2.5.1-sinsung-budget-region-stable"
ALL_REGION = "__ALL__"


def _normalize_region(value):
    text = str(value or "").strip()
    if text in ("", ALL_REGION, "전국"):
        return ""
    return text


def apply_v251_patch():
    import server as s
    import sinsung_budget_monitor as bm

    # Keep blank query values if an older link still contains region=.
    def parse_qs_stable(qs, **kwargs):
        kwargs.setdefault("keep_blank_values", True)
        return urllib.parse.parse_qs(qs, **kwargs)

    s.parse_qs = parse_qs_stable

    # New forms use an explicit sentinel instead of an empty value. This prevents
    # reverse proxies/frameworks from dropping region= and falling back to the
    # configured default region (e.g. 인천광역시).
    def regopts(region):
        current = _normalize_region(region)
        options = [
            f'<option value="{ALL_REGION}"{" selected" if current == "" else ""}>전국</option>'
        ]
        for r in s.REGIONS:
            if not r:
                continue
            selected = " selected" if current == r else ""
            options.append(f'<option value="{s.esc(r)}"{selected}>{s.esc(r)}</option>')
        return "".join(options)

    s.regopts = regopts

    def date_params(qs, days=14):
        end = (qs.get("end") or [s.TODAY.isoformat()])[0]
        start = (qs.get("start") or [(s.TODAY - dt.timedelta(days=days)).isoformat()])[0]
        if "region" in qs:
            region = _normalize_region((qs.get("region") or [ALL_REGION])[0])
        else:
            region = _normalize_region(s.get_setting("default_region", "인천광역시"))
        return start, end, region

    s.date_params = date_params

    # Preserve 전국 across links/tabs/CSV URLs as __ALL__ while keeping the
    # internal SQL filter value as an empty string (= no regional restriction).
    def link(path, **kw):
        params = dict(kw)
        if "region" in params and _normalize_region(params.get("region")) == "":
            params["region"] = ALL_REGION
        return path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")

    def urlencode_stable(query, doseq=False, **kwargs):
        if isinstance(query, dict):
            query = dict(query)
            if "region" in query and _normalize_region(query.get("region")) == "":
                query["region"] = ALL_REGION
        return urllib.parse.urlencode(query, doseq=doseq, **kwargs)

    s.link = link
    s.urlencode = urlencode_stable

    # The budget monitor reads region directly from qs, so normalize the same
    # explicit nationwide sentinel there as well.
    def budget_filters(server, qs):
        try:
            year = int((qs.get("year") or [str(server.TODAY.year)])[0])
        except Exception:
            year = server.TODAY.year
        if "region" in qs:
            region = _normalize_region((qs.get("region") or [ALL_REGION])[0])
        else:
            region = _normalize_region(server.get_setting("default_region", "인천광역시"))
        category = (qs.get("category") or [""])[0].strip()
        status = (qs.get("status") or ["all"])[0].strip() or "all"
        q = (qs.get("q") or [""])[0].strip()
        return year, region, category, status, q

    bm._budget_filters = budget_filters

    # Put the API-key connection control at the top of the budget page so it is
    # visible without scrolling. The existing lower settings block remains as a
    # secondary control and uses the same secure storage/POST endpoint.
    original_budgets_html = s.budgets_html

    def budgets_html(qs):
        page = original_budgets_html(qs)
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
            status_text = "예산 API 미연결 · 인증키를 입력해 주세요"

        auto_checked = " checked" if s.get_setting("budget_auto_sync_enabled", "1") == "1" else ""
        try:
            year = int((qs.get("year") or [str(s.TODAY.year)])[0])
        except Exception:
            year = s.TODAY.year
        today = dt.date.today().isoformat()
        box = f'''
<div class="notice" style="margin-bottom:14px">
  <b>{s.esc(status_text)}</b>
  <form method="post" action="/budget-settings" style="display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin-top:10px">
    {s.csrf_input('/budget-settings')}
    <label style="min-width:360px;flex:1">예산 API 인증키
      <input type="password" name="lofin_api_key" placeholder="{s.esc(placeholder)}"{disabled}>
    </label>
    <label style="display:flex;align-items:center;gap:6px;white-space:nowrap"><input type="checkbox" name="budget_auto_sync_enabled" value="1"{auto_checked}> 매일 1회 자동수집</label>
    <button class="primary" type="submit">인증키 저장</button>
  </form>
  <form method="post" action="/budget-api-test" style="margin-top:8px">
    {s.csrf_input('/budget-api-test')}
    <input type="hidden" name="year" value="{year}">
    <input type="hidden" name="snapshot_date" value="{today}">
    <button class="btn" type="submit">API 연결 테스트</button>
  </form>
</div>'''
        marker = '<section class="card page">'
        if marker in page:
            return page.replace(marker, marker + box, 1)
        return page

    s.budgets_html = budgets_html
    return s
