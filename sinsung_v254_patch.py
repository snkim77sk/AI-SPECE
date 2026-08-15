"""v2.5.4 patch: use correct shopping API datetime parameters and restart 2025 history build."""
import urllib.parse

from db import get_setting, set_setting

VERSION = "2.5.4-sinsung-shopping-date-fix"


def apply_v254_patch():
    import g2b_sync as g
    import server as s
    import scheduler as scheduler_module
    import sinsung_v253_patch as hist

    def fetch_shop_page(start_date, end_date, page=1, rows=999):
        key = g.get_setting('api_key')
        base = g.get_setting('shop_api_base_url').rstrip('/')
        op = g.get_setting('shop_api_operation').strip('/')
        if not key:
            raise RuntimeError('공공데이터포털 서비스키가 설정되지 않았습니다.')
        if not op:
            raise RuntimeError('종합쇼핑몰 납품요구상세 오퍼레이션명이 비어 있습니다. getDlvrReqDtlInfoList를 사용하세요.')
        params = {
            'serviceKey': key,
            'numOfRows': rows,
            'pageNo': page,
            'type': 'json',
            'inqryDiv': '1',
            # 조달청 OpenAPI 조회기준 시작/종료 일시는 YYYYMMDDHHMM 형식.
            'inqryBgnDt': start_date.replace('-', '') + '0000',
            'inqryEndDt': end_date.replace('-', '') + '2359',
        }
        return g._request(f'{base}/{op}?' + urllib.parse.urlencode(params, safe='%'), 'shop')

    # Replace the live shopping fetcher used by manual sync, auto sync, tests and backfill.
    g.fetch_shop_page = fetch_shop_page

    # v2.5.3 previously marked an empty 2025 build as complete because the date
    # parameters were wrong. Reset only the historical-build state once; keep all
    # already collected real rows intact.
    marker = 'v254_shop_datetime_fix_applied'
    if get_setting(marker, '') != '1':
        set_setting('backfill_scope_start', hist.SCOPE_KEY)
        set_setting('backfill_status', '대기')
        set_setting('backfill_progress', '0')
        set_setting('backfill_cursor', '')
        set_setting('backfill_total_saved', '0')
        set_setting('backfill_message', 'API 날짜조건 수정 완료 · 2025-01-01부터 다시 구축해 주세요.')
        set_setting('last_shop_error', '')
        set_setting(marker, '1')

    # Keep all active references on the corrected 2025 history builder.
    g.backfill_three_years = hist.backfill_from_2025
    s.backfill_three_years = hist.backfill_from_2025
    scheduler_module.backfill_three_years = hist.backfill_from_2025

    original_settings_html = s.settings_html

    def settings_html(msg='', error=False):
        page = original_settings_html(msg, error)
        page = page.replace('2025-01-01부터 구축 시작', '2025-01-01부터 구축 시작')
        # Add a visible diagnostic so an empty API response is not mistaken for a completed history build.
        note = '<div class="notice"><b>과거자료 수집 기준:</b> 2025-01-01 ~ 현재 · 조달청 조회일시 파라미터(inqryBgnDt/inqryEndDt) 적용 · 원본 0건이면 정상 완료로 판단하지 말고 쇼핑몰 API 연결 테스트를 확인하세요.</div>'
        marker_html = '<hr><h3>수동 동기화</h3>'
        if marker_html in page and note not in page:
            page = page.replace(marker_html, note + marker_html, 1)
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
