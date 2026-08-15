"""v2.6.1: parameter-matrix diagnosis for the specific-item procurement API.

v2.6.0 queries getSpcifyPrdlstPrcureInfoList with a fixed parameter shape
(inqryDiv=1, classification code only, no date range). When every target
classification returns totalCount=0 the operator only sees "모두 0건", which is
not enough to tell these cases apart:

  A. the 12 hard-coded detail-item classification codes do not exist
  B. the operation requires an inquiry date range we never send
  C. inqryDiv=1 is not the right inquiry division for this operation

This patch does not guess new classification codes. It probes a small matrix of
parameter shapes, records exactly what the API answered for each attempt
(including the raw response head), and - if any shape returns rows - stores that
shape so every v2.6.0 collection path starts using it immediately.
"""
import datetime as dt
import urllib.error
import urllib.parse
import urllib.request

from db import get_setting, set_setting

VERSION = "2.6.1-sinsung-param-probe"

# 진단 1단계에서 사용할 호출 상한. 하루 안전한도를 지키기 위해 작게 잡는다.
PROBE_CALL_BUDGET = 26
PROBE_ROWS = 10
RAW_HEAD_CHARS = 400

# 날짜 파라미터 형태 후보
DATE_MODES = (
    ("none", "날짜 파라미터 없음(v2.6.0 기본)"),
    ("inqry_date", "inqryBgnDate/inqryEndDate (YYYYMMDD)"),
    ("inqry_dt", "inqryBgnDt/inqryEndDt (YYYYMMDDHHMM)"),
    ("chg_dt", "chgDtBgnDt/chgDtEndDt (YYYYMMDDHHMM)"),
)
DIV_CANDIDATES = ("1", "2")


def _today():
    return dt.date.today()


def _date_params(date_mode, days=90):
    end = _today()
    start = end - dt.timedelta(days=days)
    if date_mode == "inqry_date":
        return {"inqryBgnDate": start.strftime("%Y%m%d"), "inqryEndDate": end.strftime("%Y%m%d")}
    if date_mode == "inqry_dt":
        return {"inqryBgnDt": start.strftime("%Y%m%d") + "0000", "inqryEndDt": end.strftime("%Y%m%d") + "2359"}
    if date_mode == "chg_dt":
        return {"chgDtBgnDt": start.strftime("%Y%m%d") + "0000", "chgDtEndDt": end.strftime("%Y%m%d") + "2359"}
    return {}


def _build_params(mode, value, div, date_mode, page, rows, key, days=90):
    p = {
        "serviceKey": key,
        "pageNo": int(page),
        "numOfRows": int(rows),
        "type": "json",
        "inqryDiv": str(div),
    }
    if mode == "detail" and value:
        p["dtilPrdctClsfcNo"] = value
    elif mode == "class" and value:
        p["prdctClsfcNo"] = value
    p.update(_date_params(date_mode, days=days))
    return p


def _raw_call(g, params):
    """원시 응답까지 확보하는 호출. 호출수는 g의 카운터로 동일하게 차감한다."""
    base = g.get_setting("shop_api_base_url").rstrip("/")
    import sinsung_v260_patch as v260
    url = f"{base}/{v260.SPECIFIC_OPERATION}?" + urllib.parse.urlencode(params, safe="%")
    g._quota_take("shop")
    req = urllib.request.Request(url, headers={"User-Agent": "SinsungG2B/2.6.1-probe"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read()
            status = r.getcode()
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        status = exc.code
    text = ""
    try:
        text = body.decode("utf-8-sig", errors="replace")
    except Exception:
        text = str(body[:RAW_HEAD_CHARS])
    try:
        items, total = g._parse_response(body)
        return status, items, int(total or 0), text[:RAW_HEAD_CHARS], ""
    except Exception as exc:
        return status, [], 0, text[:RAW_HEAD_CHARS], str(exc)[:200]


def _target_codes():
    import g2b_sync as g
    import sinsung_v260_patch as v260
    return v260._target_codes(g)


def run_param_probe():
    """파라미터 형태를 바꿔가며 어떤 조합에서 데이터가 나오는지 확인한다.

    분류코드는 새로 만들어내지 않는다. 기존 12개 코드 중 대표 1개를 사용하고,
    '분류 조건 없음' 조회를 함께 넣어 오퍼레이션 자체가 응답하는지 분리한다.
    """
    import g2b_sync as g

    key = g.get_setting("api_key")
    if not key:
        raise RuntimeError("공공데이터포털 서비스키가 설정되지 않았습니다.")

    codes = _target_codes()
    if not codes:
        raise RuntimeError("대상 세부품명번호가 비어 있습니다. sinsung_runtime_fix 설정을 확인하세요.")

    sample_detail = codes[0]
    sample_class = codes[0][:8]

    # (설명, mode, value) — '분류조건 없음'을 먼저 넣어 오퍼레이션 응답 여부부터 가른다.
    targets = [
        ("분류조건없음", "none", ""),
        (f"세부품명 {sample_detail}", "detail", sample_detail),
        (f"물품분류 {sample_class}", "class", sample_class),
    ]

    report = []
    raw_samples = []
    calls = 0
    winner = None

    for label, mode, value in targets:
        for date_mode, date_desc in DATE_MODES:
            for div in DIV_CANDIDATES:
                if calls >= PROBE_CALL_BUDGET:
                    report.append("· 호출 상한 도달로 나머지 조합 생략")
                    break
                params = _build_params(mode, value, div, date_mode, 1, PROBE_ROWS, key)
                calls += 1
                try:
                    status, items, total, raw_head, parse_err = _raw_call(g, params)
                except g.ApiQuotaReached as exc:
                    report.append(f"· 호출한도 도달: {exc}")
                    set_setting("v261_probe_report", "\n".join(report))
                    raise
                except Exception as exc:
                    report.append(f"· {label} / div={div} / {date_mode}: 호출실패 {str(exc)[:120]}")
                    continue

                mark = f"· {label} / inqryDiv={div} / {date_desc}: HTTP {status}, total={total}, rows={len(items)}"
                if parse_err:
                    mark += f", 파싱오류={parse_err}"
                report.append(mark)

                if len(raw_samples) < 3 and (parse_err or total == 0):
                    raw_samples.append(f"[{label}/div{div}/{date_mode}] {raw_head}")

                if items and total > 0:
                    winner = {"mode": mode, "div": div, "date_mode": date_mode, "label": label, "total": total}
                    report.append("  → 이 조합에서 데이터 확인. 채택합니다.")
                    break
            if winner:
                break
        if winner:
            break

    report.append(f"· 총 {calls}회 호출 사용")
    if raw_samples:
        report.append("· 원본 응답 일부:")
        report.extend(f"  {s}" for s in raw_samples)

    text = "\n".join(report)
    set_setting("v261_probe_report", text)
    set_setting("v261_probe_at", dt.datetime.now().isoformat(timespec="seconds"))

    if winner:
        set_setting("v261_div", winner["div"])
        set_setting("v261_date_mode", winner["date_mode"])
        set_setting("v261_active", "1")
        # 분류조건 없이 성공한 경우에도 수집은 기존 detail/class 모드를 그대로 사용한다.
        if winner["mode"] in ("detail", "class"):
            set_setting("shop_specific_mode", winner["mode"])
        set_setting("v261_probe_hit",
                    f"{winner['label']} / inqryDiv={winner['div']} / {winner['date_mode']} / total={winner['total']:,}")
        return winner

    set_setting("v261_active", "0")
    set_setting("v261_probe_hit", "")
    raise RuntimeError(
        "모든 파라미터 조합에서 0건입니다. 설정 화면의 '파라미터 진단(v2.6.1)' 원본 응답을 확인해 주세요. "
        "분류조건 없이도 0건이면 오퍼레이션/활용신청 범위 문제이고, 분류조건에서만 0건이면 세부품명번호가 실제 코드와 다릅니다."
    )


def apply_v261_patch():
    import g2b_sync as g
    import server as s
    import sinsung_v260_patch as v260

    # ── 1. v2.6.0 파라미터 생성기를 진단 결과가 반영되도록 교체 ──────────────
    original_params = v260._params

    def params_with_probe(mode, value, page, rows, key, change_start=None, change_end=None):
        if get_setting("v261_active", "0") != "1":
            return original_params(mode, value, page, rows, key, change_start, change_end)

        p = {
            "serviceKey": key,
            "pageNo": int(page),
            "numOfRows": int(rows),
            "type": "json",
            "inqryDiv": get_setting("v261_div", "1"),
        }
        if mode == "detail":
            p["dtilPrdctClsfcNo"] = value
        else:
            p["prdctClsfcNo"] = value

        if change_start is not None and change_end is not None:
            # 2시간 변경분 수집은 기존 동작 유지
            p["chgDtBgnDt"] = change_start.strftime("%Y%m%d%H%M")
            p["chgDtEndDt"] = change_end.strftime("%Y%m%d%H%M")
            return p

        date_mode = get_setting("v261_date_mode", "none")
        if date_mode == "inqry_date":
            p["inqryBgnDate"] = get_setting("v261_hist_start", "20250101")
            p["inqryEndDate"] = _today().strftime("%Y%m%d")
        elif date_mode == "inqry_dt":
            p["inqryBgnDt"] = get_setting("v261_hist_start", "20250101") + "0000"
            p["inqryEndDt"] = _today().strftime("%Y%m%d") + "2359"
        elif date_mode == "chg_dt":
            p["chgDtBgnDt"] = get_setting("v261_hist_start", "20250101") + "0000"
            p["chgDtEndDt"] = _today().strftime("%Y%m%d") + "2359"
        return p

    v260._params = params_with_probe

    # ── 2. 연결 테스트를 파라미터 매트릭스 진단으로 교체 ────────────────────
    original_test = v260.test_specific_api

    def test_shopping_api():
        try:
            return original_test()
        except Exception as first_error:
            set_setting("v261_last_v260_error", str(first_error)[:400])
            winner = run_param_probe()
            # 채택된 조합으로 v2.6.0 탐색을 한 번 더 돌려 mode를 확정한다.
            try:
                mode = v260._probe_source(force=True)
            except Exception:
                mode = get_setting("shop_specific_mode", "") or winner["mode"]
            set_setting("shop_specific_test_result",
                        f"파라미터 진단으로 복구 · 조회모드 {mode} · inqryDiv={winner['div']} · {winner['date_mode']}")
            return 0, int(winner.get("total") or 0)

    g.test_shopping_api = test_shopping_api
    s.test_shopping_api = test_shopping_api

    # ── 3. 설정 화면에 진단 결과 노출 ──────────────────────────────────────
    original_settings_html = s.settings_html

    def settings_html(msg="", error=False):
        page = original_settings_html(msg, error)
        report = get_setting("v261_probe_report", "") or "아직 파라미터 진단을 실행하지 않았습니다."
        hit = get_setting("v261_probe_hit", "") or "-"
        at = get_setting("v261_probe_at", "") or "-"
        prev = get_setting("v261_last_v260_error", "") or "-"
        block = (
            '<div class="notice"><b>파라미터 진단 (v2.6.1)</b><br>'
            f'채택 조합: {s.esc(hit)} · 실행시각: {s.esc(at)}<br>'
            f'직전 v2.6.0 오류: {s.esc(prev)}<br>'
            f'<pre style="white-space:pre-wrap;font-size:12px;margin:8px 0 0">{s.esc(report)}</pre>'
            '<small>연결 테스트를 누르면 기존 방식으로 먼저 시도하고, 실패하면 파라미터 조합을 '
            '순회하며 어떤 형태에서 데이터가 나오는지 기록합니다. 분류코드는 새로 만들지 않습니다.</small></div>'
        )
        marker = '<hr><h3>수동 동기화</h3>'
        if marker in page and "파라미터 진단 (v2.6.1)" not in page:
            page = page.replace(marker, block + marker, 1)
        return page

    s.settings_html = settings_html
    s.APP_VERSION = VERSION
    return s
