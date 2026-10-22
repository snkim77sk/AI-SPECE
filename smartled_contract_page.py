"""Read-only, bounded contract tab; never triggers collection or schema creation."""
import html
from decimal import Decimal
from urllib.parse import urlencode
import smartled_vnext as smart
import smartled_contract_store_vnext as store


def render(request, user, *, layout, money, result_server=False):
    e = lambda value: html.escape(str(value if value is not None else ''), quote=True)
    kind = str(request.query_params.get('kind','FIXTURE'))
    start, end = smart.date_range()
    offset, rows, error = 0, [], ''
    q = str(request.query_params.get('q','')).strip()[:100]
    status = '미확인'
    try:
        start,end = smart.date_range(request.query_params.get('start_date',''), request.query_params.get('end_date',''))
        offset = int(request.query_params.get('offset',0))
        if kind not in smart.KINDS or not 0 <= offset <= 50000:
            raise ValueError('INVALID_FILTER')
        if result_server:
            raise RuntimeError('SMARTLED_CONTRACT_SNAPSHOT_NOT_AVAILABLE')
        rows = store.list_current(start_date=start,end_date=end,kind=kind,limit=100,offset=offset,q=q)
        from db import connect
        with connect() as conn:
            record=conn.execute('SELECT value FROM app_settings WHERE key=?',('smartled_contract_status',)).fetchone()
        state=str(record['value']) if record else ''
        status={'PARTIAL_COVERAGE':'부분 범위 수집·재확인 중','UNVERIFIED':'원천 확인 실패 / 재시도 필요'}.get(state,'실원천 확인 미완료')
        queue=store.queue_status()
        status += f' · 대기 {queue["pending"]}건 / 재시도 보류 {queue["blocked"]}건'
    except ValueError:
        error = '조회 조건을 확인하세요. 시작일은 2026-10-01 이후이며 등기구·램프·제어시스템을 따로 선택합니다.'
        kind,offset = 'FIXTURE',0
    except Exception:
        error = '계약 저장자료를 확인하지 못했습니다. 실제 계약 0건 또는 수집 완료를 뜻하지 않습니다.'
    amount_key = {'FIXTURE':'fixture_amount','LAMP':'lamp_amount','CONTROL':'control_amount'}[kind]
    def amount(value):
        return '미확인' if value is None else format(Decimal(str(value)), ',f') + '원'
    def names(values):
        return ' / '.join(str(v.get('name') or v.get('code') or '미제공') for v in values)
    def items(row):
        summary = row.get('items_summary',{})
        group = summary.get(kind,{}) if isinstance(summary,dict) else {}
        values = group.get('items',[])
        return '<br>'.join(e(str(i.get('name') or '품목명 미제공')) + ' [' + e(i.get('classification')) + ']' for i in values if i.get('kind',kind)==kind)
    trs=''.join(f'<tr><td>{e(r["contract_date"])}</td><td>{e(names(r["agencies"]))}<br>{e(names(r["vendors"]))}</td><td>{e(r["contract_name"])}<br>통합 {e(r["unified_no"])}<br>확정 {e(r["decided_no"] or "미제공")}</td><td>{items(r) or "확정 분류 상세 조회 대기"}</td><td>{e(amount(r[amount_key]))}<br><small>계약 전체: {e(amount(r["contract_amount"]))}</small></td><td>{e(r["updated_at"])}</td></tr>' for r in rows)
    base=dict(source='contract',kind=kind,start_date=start,end_date=end,q=q)
    links=''
    if offset:
        links += '<a href="/smartled?' + e(urlencode({**base,'offset':max(0,offset-100)})) + '">이전</a> '
    if len(rows)==100 and offset<50000:
        links += '<a href="/smartled?' + e(urlencode({**base,'offset':offset+100})) + '">다음</a>'
    options=''.join(f'<option value="{k}"{" selected" if kind==k else ""}>{e(v)}</option>' for k,v in smart.KINDS.items())
    warning = '<div class="notice bad">'+e(error)+'</div>' if error else ''
    body=f'''<section class="card"><h2>스마트LED</h2>{warning}
<p><a href="/smartled">일반 물품계약</a> · <a href="/smartled?source=shopping">쇼핑몰 납품요구</a></p>
<p>확인 상태: {e(status)}</p><p>계약 체결일 2026-10-01부터의 관공서 물품계약입니다. 계약일 검색과 등록일·변경일·삭제일은 구분합니다. 수집은 전일까지의 구간을 기존 직렬 작업에서 재확인합니다.</p>
<p class="notice">수집 범위: 공식 계약 API의 ‘스마트LED’ 품명 검색 후보 중 상세 품목분류가 확인된 계약. 검색 누락 가능성이 있어 관공서 계약 전수 수집을 보장하지 않습니다. 실제 API 권한·응답 검증 전에는 조회 대기일 수 있습니다. 메뉴 조회는 수집을 실행하지 않습니다.</p>
<p>등기구·램프·제어시스템 금액을 합산하지 않습니다. 혼합 계약의 전체금액은 참고값이며, 해당 구분 품목금액과 다릅니다. 원천 삭제 자료는 현재 목록에서 제외하되 법적 계약 취소로 단정하지 않습니다. 쇼핑몰 탭과 같은 계약이 포함될 수 있어 두 출처를 합산하지 않습니다.</p>
<form class="row" method="get"><input type="hidden" name="source" value="contract"><label>계약 시작일<input type="date" name="start_date" min="2026-10-01" value="{e(start)}"></label><label>계약 종료일<input type="date" name="end_date" value="{e(end)}"></label><label>구분<select name="kind">{options}</select></label><label>검색<input name="q" value="{e(q)}" placeholder="기관·업체·계약명·번호"></label><button class="primary">조회</button></form>
<p>현재 페이지 {len(rows)}계약 · {e(smart.KINDS[kind])} (전체 건수·총액 아님). 품목은 계약당 최대 3개 요약 표시합니다.</p>
<a href="https://www.data.go.kr/data/15129427/openapi.do" target="_blank" rel="noopener">공식 나라장터 계약 API 근거</a> · <a href="{smart.CLASSIFICATION_URL}" target="_blank" rel="noopener">공식 스마트LED 분류 근거</a></section>
<section class="card"><div class="table shopping-table"><table><tr><th>계약일</th><th>기관 / 업체</th><th>계약명 / 계약번호</th><th>확정 품목 / 8자리 품명번호</th><th>해당 구분 품목금액</th><th>저장 확인시각(UTC)</th></tr>{trs or '<tr><td colspan="6">조회 대기 또는 현재 조건의 저장자료 없음. 전수 확인·수집 완료를 의미하지 않습니다.</td></tr>'}</table></div>{links}</section>'''
    return layout('스마트LED',body,'스마트LED',user)
