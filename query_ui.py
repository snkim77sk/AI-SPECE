"""Read-only UI for daily server pages; authentication can be attached separately."""
import html
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

REFRESH_JS = b"""(()=>{let busy=false;const tag=document.querySelector('meta[name=g2b-revision]');if(!tag)return;setInterval(async()=>{if(busy||document.hidden||['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName))return;busy=true;const c=new AbortController();const timer=setTimeout(()=>c.abort(),5000);try{const r=await fetch('/api/update-status',{cache:'no-store',signal:c.signal});if(r.ok){const s=await r.json();if(s.revision&&s.revision!==tag.content)location.reload();}}catch{}finally{clearTimeout(timer);busy=false;}},60000);})();"""


def render(data, version):
    esc = lambda v: html.escape(str(v if v is not None else '미확인'), quote=True)
    section = data['section']; meta = data['manifest']; page = data['page']
    tabs = [('shopping', '나라장터 납품'), ('vendors', '업체 조회'), ('budget_details', '세부사업 예산·집행'), ('budget_structure', '구조별 세출예산')]
    fields = [('source_date', '자료 기준일'), ('item_name', '품명'), ('demand_org', '수요기관'), ('vendor_name', '업체'), ('amount', '금액'), ('product_number', '물품식별번호')]
    if section.startswith('budget_'):
        fields = [('source_date', '자료 기준일'), ('org_name', '기관'), ('project_name', '사업·분야'), ('account_name', '회계'), ('budget_amount', '예산액')]
        fields += [('executed_amount', '집행액'), ('remaining_amount', '잔액')] if section == 'budget_details' else [('administration_budget', '행정운영경비'), ('financial_activity_budget', '재무활동')]
    elif section == 'vendors': fields = [('vendor_name', '업체'), ('item_name', '저장된 납품자료')]
    rows = ''.join('<tr>' + ''.join('<td>' + esc(row.get(k)) + '</td>' for k, _ in fields) + '</tr>' for row in data['rows'])
    scopes = meta['scopes']
    synced_kst = datetime.fromisoformat(meta['received_at_utc']).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S')
    coverage = ''.join('<tr>' + ''.join('<td>' + esc(s.get(k)) + '</td>' for k in ('scope', 'state', 'saved_count', 'web_rows', 'source_total')) + '</tr>' for s in sorted(scopes, key=lambda s: s['scope'], reverse=True))
    def link(number):
        return '/?' + urlencode({'section': section, 'q': data.get('search_query', ''), 'page': number})
    note = ('금액은 제공처 원문 값이며 단위 확인 전입니다. 구조별 예산과 세부사업 예산, 서로 다른 기준일의 금액은 합산하지 않습니다.' if section.startswith('budget_') else '서버에 저장되고 검증된 자료만 표시합니다. 웹 반영 건수와 원천 수집 완료는 별개입니다.')
    return (f'<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="g2b-revision" content="{esc(meta["revision"])}"><title>G2B 서버 자료 조회</title><link rel="stylesheet" href="/style.css"><script src="/refresh.js" defer></script><main>'
            f'<header><span class="badge">G2B · 조회 전용 · v{esc(version)}</span><h1>나라장터·지방재정 자료 조회</h1><p>서버 자동수집 자료가 반영됩니다. 새 반영 여부를 60초마다 확인합니다.</p></header>'
            '<nav>' + ''.join(f'<a href="/?section={key}"'+(' aria-current="page"' if key == section else '')+f'>{title}</a>' for key, title in tabs) + '</nav>'
            f'<p>최신 자료 기준일: <b>{esc(meta["latest_source_date"])}</b> · 마지막 웹 반영(한국시간): {esc(synced_kst)}</p><p>{note}</p>'
            f'<form><input type="hidden" name="section" value="{esc(section)}"><label>기관·업체·품명·사업 검색<input name="q" maxlength="150" value="{esc(data.get("search_query", ""))}"></label><button>검색</button></form><p>검색 결과 {data["count"]:,}건 · {page}페이지</p><div class="table"><table><thead><tr>'
            + ''.join('<th>'+title+'</th>' for _, title in fields) + '</tr></thead><tbody>' + (rows or f'<tr><td colspan="{len(fields)}">저장된 검색 결과가 없습니다.</td></tr>') + '</tbody></table></div><p>'
            + (f'<a href="{esc(link(page-1))}">이전</a> ' if page > 1 else '')
            + (f'<a href="{esc(link(page+1))}">다음</a>' if page * 50 < data['count'] else '')
            + '</p><details><summary>자료별 수집·웹 반영 범위</summary><p>저장된 체크포인트 상태이며 현재 실행 여부를 뜻하지 않습니다.</p><div class="table"><table><thead><tr><th>범위</th><th>저장 체크포인트</th><th>서버 저장</th><th>웹 반영</th><th>제공처 전체</th></tr></thead><tbody>'
            + coverage + '</tbody></table></div></details><footer>조회는 원천 API를 호출하지 않습니다. 미완료·보류 범위는 그대로 표시합니다. <a href="/release-notes">변경이력</a></footer></main></html>').encode()
