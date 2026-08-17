"""SINSUNG budget monitor UI/runtime patch for 지방재정365 real data."""
import csv
import datetime as dt
import io
import os
import threading

from budget_sync import (
    budget_api_configured,
    ensure_budget_schema,
    get_lofin_key,
    sync_budget_snapshot,
    test_budget_api,
)

VERSION = "2.5.0-sinsung-budget-monitor"
OFFICIAL_SOURCE_URL = "https://www.lofin365.go.kr/portal/LF5110000.do?pdtaId=0GAR4HBB8LWEBSL4NIHZ817053"
_BUDGET_THREAD = None
_BUDGET_LOCK = threading.Lock()

CATEGORIES = ("", "LED조명", "조명", "가로등", "가로등주", "보안등", "경관조명", "투광등", "실내조명", "태양광", "분전반")
STATUSES = ("all", "예산편성", "집행중", "집행완료", "금액확인")


def _budget_filters(s, qs):
    try:
        year = int((qs.get("year") or [str(s.TODAY.year)])[0])
    except Exception:
        year = s.TODAY.year
    region = (qs.get("region") or [s.get_setting("default_region", "인천광역시")])[0]
    category = (qs.get("category") or [""])[0].strip()
    status = (qs.get("status") or ["all"])[0].strip() or "all"
    q = (qs.get("q") or [""])[0].strip()
    return year, region, category, status, q


def _budget_where(year, region, category, status, q):
    clauses = ["fiscal_year=?", "is_sample=0"]
    vals = [year]
    if region:
        clauses.append("region=?")
        vals.append(region)
    if category:
        clauses.append("category=?")
        vals.append(category)
    if status != "all":
        clauses.append("status=?")
        vals.append(status)
    if q:
        like = f"%{q}%"
        clauses.append("(org_name LIKE ? OR project_name LIKE ? OR category LIKE ? OR field_name LIKE ?)")
        vals += [like, like, like, like]
    return " AND ".join(clauses), vals


def _category_options(s, selected_value):
    out = []
    for value in CATEGORIES:
        label = "전체 품목" if not value else value
        out.append(f'<option value="{s.esc(value)}"{s.selected(selected_value, value)}>{s.esc(label)}</option>')
    return "".join(out)


def _status_options(s, selected_value):
    out = []
    for value in STATUSES:
        label = "전체 상태" if value == "all" else value
        out.append(f'<option value="{s.esc(value)}"{s.selected(selected_value, value)}>{s.esc(label)}</option>')
    return "".join(out)


def _budget_csv(s, qs):
    year, region, category, status, q = _budget_filters(s, qs)
    where, vals = _budget_where(year, region, category, status, q)
    rows = s.qrows(f"SELECT * FROM budget_items WHERE {where} ORDER BY budget_amount DESC,org_name,project_name", vals)
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["회계연도", "지역", "자치단체", "세부사업명", "분류", "분야", "회계", "예산현액", "지출액", "미집행잔액", "상태", "기준일", "출처"])
    for r in rows:
        w.writerow([
            r["fiscal_year"], r["region"], r["org_name"], r["project_name"], r["category"],
            r["field_name"], r["account_name"], r["budget_amount"], r["executed_amount"],
            r["remaining_amount"], r["status"], r["source_date"], r["source"],
        ])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _start_budget_sync(s, year, snapshot_date):
    global _BUDGET_THREAD
    with _BUDGET_LOCK:
        if _BUDGET_THREAD is not None and _BUDGET_THREAD.is_alive():
            return False

        def runner():
            s.set_setting("budget_sync_status", "수집중")
            try:
                sync_budget_snapshot(year, snapshot_date)
                s.set_setting("budget_sync_status", "완료")
            except Exception as exc:
                s.set_setting("budget_sync_status", "오류")
                s.set_setting("last_budget_sync_result", f"예산 수집 실패: {exc}")

        _BUDGET_THREAD = threading.Thread(target=runner, name="sinsung-budget-sync", daemon=True)
        _BUDGET_THREAD.start()
        return True


def apply_budget_monitor():
    import server as s

    ensure_budget_schema()

    def budgets_html(qs):
        year, region, category, status, q = _budget_filters(s, qs)
        where, vals = _budget_where(year, region, category, status, q)
        result_count = s.scalar(f"SELECT COUNT(*) FROM budget_items WHERE {where}", vals)
        rows = s.qrows(
            f"SELECT * FROM budget_items WHERE {where} ORDER BY remaining_amount DESC,budget_amount DESC,id DESC LIMIT 2000",
            vals,
        )
        total = s.scalar(f"SELECT COALESCE(SUM(budget_amount),0) FROM budget_items WHERE {where}", vals)
        executed = s.scalar(f"SELECT COALESCE(SUM(executed_amount),0) FROM budget_items WHERE {where}", vals)
        remaining = s.scalar(f"SELECT COALESCE(SUM(remaining_amount),0) FROM budget_items WHERE {where}", vals)

        prev_where, prev_vals = _budget_where(year - 1, region, category, status, q)
        prev_total = s.scalar(f"SELECT COALESCE(SUM(budget_amount),0) FROM budget_items WHERE {prev_where}", prev_vals)
        growth = ((total - prev_total) / prev_total * 100) if prev_total else 0

        row_html = []
        for r in rows:
            row_html.append(
                f'<tr><td>{r["fiscal_year"]}</td><td>{s.esc(r["region"] or "미분류")}</td>'
                f'<td>{s.esc(r["org_name"])}</td><td><b>{s.esc(r["project_name"])}</b>'
                f'<small>{s.esc(r["field_name"])}{(" · " + s.esc(r["account_name"])) if r["account_name"] else ""}</small></td>'
                f'<td>{s.esc(r["category"])}</td><td class="num">{s.money(r["budget_amount"])}</td>'
                f'<td class="num">{s.money(r["executed_amount"])}</td><td class="num">{s.money(r["remaining_amount"])}</td>'
                f'<td>{s.esc(r["status"])}</td><td>{s.esc(r["source_date"])}</td></tr>'
            )

        env_key = bool(os.getenv("LOFIN_API_KEY"))
        configured = budget_api_configured()
        key_placeholder = "서버 환경변수(LOFIN_API_KEY)로 설정됨" if env_key else ("인증키 저장됨 · 변경할 때만 입력" if configured else "지방재정365 API 인증키 입력")
        auto_enabled = s.get_setting("budget_auto_sync_enabled", "1") == "1"
        sync_status = s.get_setting("budget_sync_status", "대기")
        last_result = s.get_setting("last_budget_sync_result", "지방재정365 예산 실데이터를 아직 수집하지 않았습니다.")
        last_sync = s.get_setting("last_budget_sync", "-") or "-"
        today = dt.date.today().isoformat()
        export_q = s.urlencode({"year": year, "region": region, "category": category, "status": status, "q": q})

        body = f'''{s.pathbar('/g2b/budget.php','SINSUNG / budget-monitor')}
<section class="card page"><div class="pagehead"><div><h2>예산 모니터</h2><p>지방재정365 세부사업별 세출현황 실데이터 · 조명 관련 사업 자동 분류</p></div><a class="btn" target="_blank" rel="noopener" href="{OFFICIAL_SOURCE_URL}">공식 데이터 출처</a></div>
<form class="filters" method="get"><div class="filterline"><strong>연도</strong><select name="year">{''.join(f'<option value="{y}"{s.selected(year,y)}>{y}</option>' for y in range(s.TODAY.year-3,s.TODAY.year+2))}</select><strong>지역</strong><select name="region">{s.regopts(region)}</select><strong>분류</strong><select name="category">{_category_options(s,category)}</select><strong>상태</strong><select name="status">{_status_options(s,status)}</select><input class="q" name="q" value="{s.esc(q)}" placeholder="기관명 / 세부사업명 검색"><button class="primary">조회</button><a class="btn" href="/budget-export.csv?{export_q}">CSV</a></div></form>
<div class="kpis five"><div><span>관련 예산사업</span><strong>{result_count:,} 건</strong><small>{year}년 · {s.esc(region or '전국')}</small></div><div><span>예산현액</span><strong>{s.money(total)} 원</strong></div><div><span>지출액</span><strong>{s.money(executed)} 원</strong></div><div><span>미집행 잔액</span><strong>{s.money(remaining)} 원</strong></div><div><span>전년 대비</span><strong>{growth:+.1f}%</strong><small>{'전년도 데이터 기준' if prev_total else '전년도 데이터 미수집'}</small></div></div>
<div class="notice"><b>수집 상태:</b> {s.esc(sync_status)} · 최근 수집 {s.esc(last_sync)}<br>{s.esc(last_result)}</div>
<div class="tablewrap"><table><thead><tr><th>연도</th><th>지역</th><th>자치단체</th><th>세부사업명</th><th>분류</th><th>예산현액</th><th>지출액</th><th>미집행잔액</th><th>상태</th><th>기준일</th></tr></thead><tbody>{''.join(row_html) or '<tr><td colspan="10" class="empty">조건에 맞는 예산 실데이터가 없습니다.</td></tr>'}</tbody></table></div>
<hr><div class="grid2"><section class="panel"><h3>지방재정365 연동</h3><form method="post" action="/budget-settings" class="settings">{s.csrf_input('/budget-settings')}<label>API 인증키<input type="password" name="lofin_api_key" placeholder="{s.esc(key_placeholder)}"{' disabled' if env_key else ''}></label><label style="display:flex;align-items:center;gap:8px"><input type="checkbox" name="budget_auto_sync_enabled" value="1"{' checked' if auto_enabled else ''}> 매일 1회 현재연도 예산 자동수집</label><button class="primary" type="submit">예산 연동 설정 저장</button></form><p><small>지방재정365에서 발급받은 인증키를 사용합니다. 인증키는 화면에 다시 표시하지 않습니다.</small></p></section>
<section class="panel"><h3>예산 실데이터 수집</h3><form method="post" action="/sync-budget" class="syncform">{s.csrf_input('/sync-budget')}<label>회계연도<input type="number" name="year" min="2016" max="{s.TODAY.year+1}" value="{year}"></label><label>기준일<input type="date" name="snapshot_date" value="{today}"></label><button class="primary" type="submit">수집 시작</button></form><div class="actions"><form method="post" action="/budget-api-test">{s.csrf_input('/budget-api-test')}<input type="hidden" name="year" value="{year}"><input type="hidden" name="snapshot_date" value="{today}"><button class="btn" type="submit">API 연결 테스트</button></form></div><p><small>대상: LED, 조명, 가로등, 보안등, 경관조명, 투광등, 실내조명, 태양광, 분전반, 가로등주.</small></p></section></div>
</section>'''
        return s.base_html(body, "예산")

    s.budgets_html = budgets_html

    original_get = s.Handler.do_GET
    def do_GET(self):
        u = s.urlparse(self.path)
        if u.path == "/budget-export.csv":
            if self.require_auth(u.path):
                return
            qs = s.parse_qs(u.query)
            return self.send_bytes(
                _budget_csv(s, qs),
                "text/csv; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=sinsung_budget_export.csv"},
            )
        return original_get(self)
    s.Handler.do_GET = do_GET

    original_post = s.Handler.do_POST
    def do_POST(self):
        u = s.urlparse(self.path)
        if u.path not in ("/budget-settings", "/sync-budget", "/budget-api-test"):
            return original_post(self)
        try:
            form = self.parse_post()
            if self.require_auth(u.path):
                return
            if not s.valid_csrf(u.path, form):
                return self.send_bytes("CSRF validation failed", "text/plain; charset=utf-8", 403)

            if u.path == "/budget-settings":
                key = (form.get("lofin_api_key") or [""])[0].strip()
                if key and not os.getenv("LOFIN_API_KEY"):
                    s.set_setting("lofin_api_key", key)
                s.set_setting("budget_auto_sync_enabled", "1" if "budget_auto_sync_enabled" in form else "0")
                return self.redirect("/budgets?msg=" + s.quote("예산 연동 설정을 저장했습니다."))

            try:
                year = int((form.get("year") or [str(s.TODAY.year)])[0])
            except Exception:
                year = s.TODAY.year
            snapshot_date = (form.get("snapshot_date") or [dt.date.today().isoformat()])[0]

            if u.path == "/budget-api-test":
                try:
                    n, total, code, message = test_budget_api(year, snapshot_date)
                    msg = f"지방재정365 연결 성공: 조명 검색 첫 페이지 {n:,}건 / 전체 {total:,}건 · {code} {message}".strip()
                    err = 0
                except Exception as exc:
                    msg = f"지방재정365 연결 실패: {exc}"
                    err = 1
                return self.redirect(f"/budgets?year={year}&error={err}&msg=" + s.quote(msg))

            if not get_lofin_key():
                return self.redirect(f"/budgets?year={year}&error=1&msg=" + s.quote("지방재정365 API 인증키를 먼저 저장해 주세요."))
            started = _start_budget_sync(s, year, snapshot_date)
            msg = "예산 실데이터 수집을 백그라운드에서 시작했습니다." if started else "예산 수집이 이미 진행 중입니다."
            return self.redirect(f"/budgets?year={year}&msg=" + s.quote(msg))
        except Exception as exc:
            return self.send_bytes(f"<pre>{s.esc(exc)}</pre>", status=500)

    s.Handler.do_POST = do_POST
    return s
