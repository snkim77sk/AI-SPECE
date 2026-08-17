"""Restore the familiar procurement screen while keeping v2.4.1 data fixes."""

VERSION = "2.4.2-sinsung-ui-restore"

ITEM_LABELS = {
    "3911151502": "LED다운라이트",
    "3911160302": "LED가로등기구",
    "3911160304": "LED터널등기구",
    "3911160501": "LED경관조명기구",
    "3911160802": "LED보안등기구",
    "3911161102": "LED투광등기구",
    "3911210201": "LED실내조명등",
    "2611160701": "태양광발전장치",
    "3912110101": "분전반",
    "3911152601": "철제가로등주",
    "3911152602": "스테인리스가로등주",
    "3911152607": "가로등주부속자재",
}


def apply_ui_restore():
    import server as s

    original_build = s.build_shop_params

    def build_shop_params(qs):
        p = original_build(qs)
        allowed = tuple(s.GROUPS[p["group"]][1])
        requested = []
        for code in qs.get("item", []):
            code = str(code).strip()
            if code in allowed and code not in requested:
                requested.append(code)
        # No explicit checkbox selection means all items in the current menu.
        p["detail_item_nos"] = tuple(requested or allowed)
        p["items"] = []
        return p

    s.build_shop_params = build_shop_params

    def shopping_html(p):
        rows, where, vals = s.query_shop(p)
        company = s.get_setting("company_name")
        result_count = s.scalar(f"SELECT COUNT(*) FROM shopping_contracts WHERE {where}", vals) if p["view"] == "detail" else len(rows)
        total = s.scalar(f"SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where}", vals)
        own_names = s.company_names()
        own_clause = "vendor_name IN (%s)" % ",".join("?" for _ in own_names) if own_names else "1=0"
        own = s.scalar(f"SELECT COALESCE(SUM(supply_amount),0) FROM shopping_contracts WHERE {where} AND {own_clause}", vals + own_names)
        share = own / total * 100 if total else 0

        tabs = [
            ("detail", "상세내역"),
            ("itemname", "물품식별명별 합계"),
            ("detailitem", "세부품목별 합계"),
            ("region", "수요기관지역별 합계"),
            ("org", "수요기관명별 합계"),
            ("quarter", "분기별(전체) 합계"),
        ]
        tabhtml = []
        selected_codes = list(p["detail_item_nos"])
        for i, (key, label) in enumerate(tabs):
            href = s.link(
                "/g2b/shopping/prdct_detail.php",
                group=p["group"], start=p["start"], end=p["end"], region=p["region"],
                q=p["q"], view=key, item=selected_codes,
            )
            tabhtml.append(f'<a class="{"on" if p["view"] == key else ""}" href="{href}">{label}</a>')
            if i < len(tabs) - 1:
                tabhtml.append("<b>|</b>")

        allowed_codes = tuple(s.GROUPS[p["group"]][1])
        checks = "".join(
            f'<label><input class="itembox" type="checkbox" name="item" value="{s.esc(code)}"'
            f'{" checked" if code in selected_codes else ""}> {s.esc(ITEM_LABELS.get(code, code))} <small>{s.esc(code)}</small></label>'
            for code in allowed_codes
        )

        export_q = s.urlencode({
            "group": p["group"], "start": p["start"], "end": p["end"], "region": p["region"],
            "q": p["q"], "view": p["view"], "item": selected_codes,
        }, doseq=True)
        title = s.GROUPS[p["group"]][0]

        page_links = ""
        if p["view"] == "detail":
            page_count = max(1, (result_count + 99) // 100)
            common = dict(group=p["group"], start=p["start"], end=p["end"], region=p["region"], q=p["q"], view=p["view"], item=selected_codes)
            prev = s.link("/g2b/shopping/prdct_detail.php", **common, page=max(1, p["page"] - 1))
            nxt = s.link("/g2b/shopping/prdct_detail.php", **common, page=min(page_count, p["page"] + 1))
            page_links = f'<div class="pagination"><a class="btn" href="{prev}">이전</a><b>{p["page"]:,} / {page_count:,}</b><a class="btn" href="{nxt}">다음</a></div>'

        body = f'''{s.pathbar('/g2b/shopping_prdct_detail.php','SINSUNG / G2B / SHOPPING')}
<section class="card page procurement-page">
<div class="pagehead"><div><h2>{s.esc(title)}</h2><p>나라장터 쇼핑몰 납품요구 실데이터 조회</p></div><a class="btn" href="/settings">데이터 수집 설정</a></div>
<div class="subtabs">{"".join(tabhtml)}</div>
<form method="get" class="filters procurement-filters">
<input type="hidden" name="group" value="{s.esc(p['group'])}"><input type="hidden" name="view" value="{s.esc(p['view'])}">
<div class="filterline"><strong>기간</strong><input type="date" name="start" value="{s.esc(p['start'])}"><span>~</span><input type="date" name="end" value="{s.esc(p['end'])}"><strong>지역</strong><select name="region">{s.regopts(p['region'])}</select><strong>통합 검색</strong><input class="q" type="text" name="q" value="{s.esc(p['q'])}" placeholder="업체명, 수요기관명, 계약명, 식별명 검색"></div>
<div class="itemlabel"><strong>세부품명</strong> <button type="button" class="btn mini" onclick="var a=[...document.querySelectorAll('.itembox')],all=a.every(x=>x.checked);a.forEach(x=>x.checked=!all)">전체 선택/해제</button></div>
<div class="checks">{checks}</div>
<div class="actions"><button class="primary" type="submit">검색</button><a class="btn" href="/export.csv?{export_q}">CSV</a><a class="btn" href="/vendors?start={s.esc(p['start'])}&end={s.esc(p['end'])}&region={s.quote(p['region'])}">업체순위</a><span class="syncinfo">{s.esc(s.get_setting('last_sync_result'))}</span></div>
</form>
<div class="kpis"><div><span>총 공급금액 (전체 업체)</span><strong>{s.money(total)} 원</strong></div><div><span>{s.esc(company)} 공급금액</span><strong>{s.money(own)} 원</strong></div><div><span>{s.esc(company)} 점유율</span><strong>{share:.2f} %</strong></div></div>
{f'<div class="notice">검색 결과 {result_count:,}건 · 페이지당 100건 · CSV는 전체 결과를 내보냅니다.</div>' if p['view']=='detail' else ''}
<div class="tablewrap">{s.shop_table(p, rows, total)}</div>{page_links}
</section>'''
        return s.base_html(body, title)

    s.shopping_html = shopping_html
    return s
