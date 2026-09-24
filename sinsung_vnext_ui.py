"""Read-only production UI adapter for the verified G2B vNext budget read model.

This module intentionally exposes only already-stored vNext state. It never starts a
collector, scheduler, historical backfill, canary, or live source transport.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlencode, urlparse

VNEXT_PATH = "/vnext"
VNEXT_API_PATH = "/api/vnext/budget"
VNEXT_NAV_LABEL = "G2B vNext"
CATEGORIES = ("LIGHTING", "POLE", "ELECTRICAL", "SOLAR")
CATEGORY_LABELS = {
    "LIGHTING": "조명",
    "POLE": "가로등주",
    "ELECTRICAL": "전기",
    "SOLAR": "태양광",
}
STAGE_LABELS = {
    "BUDGET_ONLY": "예산확보",
    "NOTICE_PUBLISHED": "공고확인",
    "OPENED": "개찰",
    "AWARDED": "낙찰",
    "CONTRACTED": "계약",
}


def _first(qs, key, default=""):
    return str((qs.get(key) or [default])[0] or "").strip()


def _year(qs):
    value = _first(qs, "year")
    if not value:
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ValueError("연도는 숫자로 입력해 주세요.") from None
    if year < 2000 or year > 2100:
        raise ValueError("연도 범위를 확인해 주세요.")
    return year


def _categories(qs):
    value = _first(qs, "category").upper()
    if not value or value == "ALL":
        return None
    if value not in CATEGORIES:
        raise ValueError("지원하지 않는 분류입니다.")
    return (value,)


def _limit(qs, default=100, maximum=200):
    value = _first(qs, "limit", str(default))
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _money(value):
    try:
        return f"{int(value or 0):,} 원"
    except (TypeError, ValueError):
        return "0 원"


def _num(value):
    try:
        return f"{int(value or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _stage(value):
    text = str(value or "BUDGET_ONLY")
    return STAGE_LABELS.get(text, text)


def _query_link(**kwargs):
    clean = {key: value for key, value in kwargs.items() if value not in (None, "", "ALL")}
    return VNEXT_PATH + ("?" + urlencode(clean) if clean else "")


def _read_payload(qs, *, include_full=False):
    import budget_read_vnext as read

    year = _year(qs)
    categories = _categories(qs)
    limit = _limit(qs)
    status = read.budget_status(
        fiscal_year=year,
        categories=categories,
    )
    prebid = read.prebid_budget_rows(
        fiscal_year=year,
        categories=categories,
        limit=limit,
        offset=0,
    )
    projects = read.budget_project_rows(
        fiscal_year=year,
        categories=categories,
        limit=limit,
        offset=0,
    )
    result = {
        "status": status,
        "prebid_rows": prebid,
        "project_pipelines": projects,
        "read_only": True,
        "source_traffic": False,
    }
    if include_full:
        result = read.budget_read_model(
            fiscal_year=year,
            categories=categories,
            limit=limit,
            offset=0,
        )
    return result


def vnext_dashboard_html(qs):
    import server as s

    try:
        payload = _read_payload(qs)
        error = ""
    except Exception as exc:
        payload = {
            "status": {
                "collection": {"datasets": [], "totals": {}},
                "analysis": {},
                "procurement_pipeline": {},
            },
            "prebid_rows": [],
            "project_pipelines": [],
        }
        error = f"vNext 저장자료 조회 오류: {type(exc).__name__}"

    year = _first(qs, "year")
    category = _first(qs, "category", "ALL").upper() or "ALL"
    status = payload.get("status") or {}
    collection = status.get("collection") or {}
    totals = collection.get("totals") or {}
    analysis = status.get("analysis") or {}
    pipeline = status.get("procurement_pipeline") or {}
    prebid = payload.get("prebid_rows") or []
    projects = payload.get("project_pipelines") or []

    category_options = ['<option value="ALL">전체 대상</option>']
    for code in CATEGORIES:
        selected = " selected" if category == code else ""
        category_options.append(
            f'<option value="{code}"{selected}>{s.esc(CATEGORY_LABELS[code])}</option>'
        )
    if category == "ALL":
        category_options[0] = '<option value="ALL" selected>전체 대상</option>'

    dataset_rows = []
    for row in collection.get("datasets") or []:
        dataset_rows.append(
            "<tr>"
            f"<td>{s.esc(row.get('dataset'))}</td>"
            f"<td class=\"num\">{_num(row.get('raw_rows'))}</td>"
            f"<td class=\"num\">{_num(row.get('raw_revisions'))}</td>"
            f"<td class=\"num\">{_num(row.get('verified_complete_scopes'))}</td>"
            f"<td>{s.esc(', '.join(f'{k}:{v}' for k, v in (row.get('checkpoint_status_counts') or {}).items()) or '-')}</td>"
            "</tr>"
        )

    prebid_rows = []
    for row in prebid:
        prebid_rows.append(
            "<tr>"
            f"<td>{s.esc(row.get('fiscal_year'))}</td>"
            f"<td>{s.esc(row.get('org_name') or row.get('institution_name') or row.get('region_name'))}</td>"
            f"<td>{s.esc(row.get('project_name'))}</td>"
            f"<td>{s.esc(CATEGORY_LABELS.get(str(row.get('primary_category') or ''), row.get('primary_category') or '-'))}</td>"
            f"<td class=\"num\">{_money(row.get('budget_amount'))}</td>"
            f"<td class=\"num\">{_money(row.get('executed_amount'))}</td>"
            f"<td class=\"num\">{_money(row.get('remaining_amount'))}</td>"
            "</tr>"
        )

    project_rows = []
    for row in projects:
        project_rows.append(
            "<tr>"
            f"<td>{s.esc(row.get('fiscal_year'))}</td>"
            f"<td>{s.esc(row.get('org_name') or row.get('institution_name') or row.get('region_name'))}</td>"
            f"<td>{s.esc(row.get('project_name'))}</td>"
            f"<td>{s.esc(CATEGORY_LABELS.get(str(row.get('primary_category') or ''), row.get('primary_category') or '-'))}</td>"
            f"<td>{s.esc(_stage(row.get('latest_known_stage')))}</td>"
            f"<td class=\"num\">{_num(row.get('procurement_candidate_count'))}</td>"
            f"<td class=\"num\">{_money(row.get('remaining_amount'))}</td>"
            "</tr>"
        )

    by_stage = pipeline.get("by_stage") or {}
    budget_only = (by_stage.get("BUDGET_ONLY") or {}).get("projects", 0)
    notice_published = (by_stage.get("NOTICE_PUBLISHED") or {}).get("projects", 0)
    contracted = (by_stage.get("CONTRACTED") or {}).get("projects", 0)
    api_query = {}
    if year:
        api_query["year"] = year
    if category and category != "ALL":
        api_query["category"] = category
    api_query["limit"] = _limit(qs)
    api_href = VNEXT_API_PATH + "?" + urlencode(api_query)

    flash = f'<div class="flash error">{s.esc(error)}</div>' if error else ""
    body = f'''{s.pathbar(VNEXT_PATH,'SINSUNG / G2B vNext')}
<section class="card page">
  <div class="pagehead">
    <div><h2>G2B vNext 예산 · 조달 영업 파이프라인</h2>
    <p>저장된 RAW → 정규화 → 후분류 → 공고/개찰/낙찰/계약 연결을 읽기 전용으로 조회합니다.</p></div>
    <a class="btn" href="{s.esc(api_href)}" target="_blank" rel="noopener">JSON API</a>
  </div>
  {flash}
  <div class="notice"><b>운영 안전모드:</b> 이 화면은 저장자료만 읽습니다. 조회 시 신규 API 호출·자동수집·bulk historical·APPROVED_HISTORICAL 활성화를 실행하지 않습니다.<br>
  <b>범위 주의:</b> 아래 수치는 현재 로컬에 저장된 RAW/체크포인트 기준이며 전체 원천의 완전수집을 의미하지 않습니다.</div>
  <form class="filters" method="get" action="{VNEXT_PATH}">
    <label>연도<input name="year" inputmode="numeric" placeholder="전체" value="{s.esc(year)}"></label>
    <label>분류<select name="category">{''.join(category_options)}</select></label>
    <label>표시건수<input name="limit" type="number" min="1" max="200" value="{_limit(qs)}"></label>
    <div class="actions"><button class="primary" type="submit">조회</button><a class="btn" href="{VNEXT_PATH}">초기화</a></div>
  </form>
  <div class="kpis">
    <div><b>{_num(totals.get('raw_rows'))}</b><span>vNext RAW</span></div>
    <div><b>{_num(analysis.get('current_projects'))}</b><span>대상 예산사업</span></div>
    <div><b>{_num(budget_only)}</b><span>공고 전 영업후보</span></div>
    <div><b>{_num(notice_published)}</b><span>공고 연결</span></div>
    <div><b>{_num(contracted)}</b><span>계약 확인</span></div>
  </div>
  <h3 style="margin-top:24px">원천별 저장 상태</h3>
  <div class="tablewrap"><table><thead><tr><th>데이터셋</th><th>현재 RAW</th><th>RAW 이력</th><th>영수증 검증완료 scope</th><th>체크포인트</th></tr></thead>
  <tbody>{''.join(dataset_rows) or '<tr><td colspan="5" class="empty">vNext 저장자료 없음</td></tr>'}</tbody></table></div>

  <h3 style="margin-top:24px">공고 전 영업후보 (BUDGET_ONLY)</h3>
  <div class="notice">잔여예산이 양수이고 저장된 공고 후보가 아직 연결되지 않은 사업입니다. 영업 우선순위 점수가 아니라 사실 기반 후보 목록입니다.</div>
  <div class="tablewrap"><table><thead><tr><th>연도</th><th>기관</th><th>사업명</th><th>분류</th><th>예산</th><th>집행</th><th>잔액</th></tr></thead>
  <tbody>{''.join(prebid_rows) or '<tr><td colspan="7" class="empty">현재 조건의 공고 전 후보 없음</td></tr>'}</tbody></table></div>

  <h3 style="margin-top:24px">예산 → 조달 진행상태</h3>
  <div class="tablewrap"><table><thead><tr><th>연도</th><th>기관</th><th>사업명</th><th>분류</th><th>현재 단계</th><th>공고 후보</th><th>잔액</th></tr></thead>
  <tbody>{''.join(project_rows) or '<tr><td colspan="7" class="empty">현재 조건의 파이프라인 자료 없음</td></tr>'}</tbody></table></div>
</section>'''
    return s.base_html(body, VNEXT_NAV_LABEL)


def vnext_api_payload(qs):
    payload = _read_payload(qs, include_full=True)
    payload["runtime_adapter"] = {
        "view": "G2B_VNEXT_READ_ONLY",
        "source_traffic": False,
        "collection_side_effects": False,
    }
    return payload


def apply_vnext_ui():
    import server as s

    if getattr(s, "_vnext_read_ui_applied", False):
        return s

    if not any(name == VNEXT_NAV_LABEL for name, _ in s.NAVS):
        s.NAVS.append((VNEXT_NAV_LABEL, VNEXT_PATH))

    original_do_get = s.Handler.do_GET

    def do_get_vnext(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path not in (VNEXT_PATH, VNEXT_API_PATH):
            return original_do_get(self)
        if self.require_auth(path):
            return
        qs = parse_qs(parsed.query)
        if path == VNEXT_PATH:
            return self.send_bytes(vnext_dashboard_html(qs))
        try:
            data = vnext_api_payload(qs)
            body = json.dumps(data, ensure_ascii=False, default=str)
            return self.send_bytes(body, "application/json; charset=utf-8")
        except ValueError as exc:
            body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            return self.send_bytes(body, "application/json; charset=utf-8", 400)
        except Exception as exc:
            s.traceback.print_exc()
            body = json.dumps(
                {"ok": False, "error": "VNEXT_READ_MODEL_ERROR", "detail": type(exc).__name__},
                ensure_ascii=False,
            )
            return self.send_bytes(body, "application/json; charset=utf-8", 500)

    s.Handler.do_GET = do_get_vnext
    s._vnext_read_ui_applied = True
    return s
