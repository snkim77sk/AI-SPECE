"""v2.5.2 patch: budget API settings UI + 지방재정365 required region parameter fix."""
import datetime as dt
import os

VERSION = "2.5.3-sinsung-budget-required-region"

# 지방재정365 QWGJK(세부사업별 세출현황)는 광역자치단체 코드(wa_laf_cd)를
# 요구한다. 현재 운영 기본지역인 인천광역시는 2800000이다.
# 2026년 행정구역 통합으로 광주/전남은 별도 확인이 필요하므로 2026년 이후
# 오래된 코드를 자동으로 보내지 않는다.
_WIDE_REGION_CODES = {
    "서울": "1100000", "서울특별시": "1100000",
    "부산": "2600000", "부산광역시": "2600000",
    "대구": "2700000", "대구광역시": "2700000",
    "인천": "2800000", "인천광역시": "2800000",
    "대전": "3000000", "대전광역시": "3000000",
    "울산": "3100000", "울산광역시": "3100000",
    "세종": "3600000", "세종특별자치시": "3600000",
    "경기": "4100000", "경기도": "4100000",
    "충북": "4300000", "충청북도": "4300000",
    "충남": "4400000", "충청남도": "4400000",
    "경북": "4700000", "경상북도": "4700000",
    "경남": "4800000", "경상남도": "4800000",
    "제주": "5000000", "제주특별자치도": "5000000",
    "강원": "5100000", "강원도": "5100000", "강원특별자치도": "5100000",
    "전북": "5200000", "전라북도": "5200000", "전북특별자치도": "5200000",
}


def _install_budget_required_region_fix():
    """Inject QWGJK's required wa_laf_cd into every budget API request.

    Both the exact-date collector and the year-search fallback eventually call
    budget_sync._request(), so patching at this layer fixes API test, manual
    collection and automatic collection without changing their public signatures.
    """
    import budget_sync as bs

    if getattr(bs, "_required_region_fix_applied", False):
        return

    original_request = bs._request

    def resolve_region_code(params):
        explicit = str(os.getenv("LOFIN_WIDE_REGION_CODE", "") or "").strip()
        if explicit:
            return explicit

        region = str(
            bs.get_setting("budget_sync_region", "")
            or bs.get_setting("default_region", "인천광역시")
            or ""
        ).strip()
        fiscal_year = int((params or {}).get("fyr") or dt.date.today().year)

        # 광주/전남은 2026-07-01 통합 전 연도 조회에서만 구 코드 사용.
        if region in ("광주", "광주광역시"):
            if fiscal_year <= 2025:
                return "2900000"
            raise bs.LofinApiError(
                "2026년 이후 광주/전남 통합지역의 지방재정365 광역코드 확인이 필요합니다. "
                "현재 기본지역을 인천광역시 등 지원 지역으로 설정하거나 LOFIN_WIDE_REGION_CODE를 설정해 주세요."
            )
        if region in ("전남", "전라남도"):
            if fiscal_year <= 2025:
                return "4600000"
            raise bs.LofinApiError(
                "2026년 이후 광주/전남 통합지역의 지방재정365 광역코드 확인이 필요합니다. "
                "현재 기본지역을 인천광역시 등 지원 지역으로 설정하거나 LOFIN_WIDE_REGION_CODE를 설정해 주세요."
            )

        code = _WIDE_REGION_CODES.get(region, "")
        if not code:
            raise bs.LofinApiError(
                f"지방재정365 세부사업별 세출현황 조회에 필요한 광역자치단체코드를 찾을 수 없습니다: {region or '지역 미설정'}"
            )
        return code

    def request_with_region(params, retries=3, timeout=45):
        fixed = dict(params or {})
        if not str(fixed.get("wa_laf_cd") or "").strip():
            fixed["wa_laf_cd"] = resolve_region_code(fixed)
        return original_request(fixed, retries=retries, timeout=timeout)

    bs._request = request_with_region
    bs.get_budget_region_code = lambda fiscal_year=None: resolve_region_code({"fyr": fiscal_year or dt.date.today().year})
    bs._required_region_fix_applied = True


def apply_v252_patch():
    import server as s
    import sinsung_budget_monitor as bm

    _install_budget_required_region_fix()

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
