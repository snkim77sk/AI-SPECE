"""SINSUNG budget collector using the official 지방재정365 detailed-project expenditure API."""
import datetime as dt
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from db import connect, finish_sync_log, get_setting, new_sync_log, set_setting

SERVICE_CODE = "QWGJK"
ENDPOINT = f"https://www.lofin365.go.kr/lf/hub/{SERVICE_CODE}"
SOURCE_NAME = "지방재정365 세부사업별 세출현황(QWGJK)"

TARGET_KEYWORDS = (
    "LED", "조명", "가로등", "보안등", "경관조명", "투광등",
    "실내조명", "태양광", "분전반", "가로등주",
)

REGION_MAP = {
    "서울": "서울특별시", "서울특별시": "서울특별시",
    "부산": "부산광역시", "부산광역시": "부산광역시",
    "대구": "대구광역시", "대구광역시": "대구광역시",
    "인천": "인천광역시", "인천광역시": "인천광역시",
    "광주": "광주광역시", "광주광역시": "광주광역시",
    "대전": "대전광역시", "대전광역시": "대전광역시",
    "울산": "울산광역시", "울산광역시": "울산광역시",
    "세종": "세종특별자치시", "세종특별자치시": "세종특별자치시",
    "경기": "경기도", "경기도": "경기도",
    "강원": "강원특별자치도", "강원도": "강원특별자치도", "강원특별자치도": "강원특별자치도",
    "충북": "충청북도", "충청북도": "충청북도",
    "충남": "충청남도", "충청남도": "충청남도",
    "전북": "전북특별자치도", "전라북도": "전북특별자치도", "전북특별자치도": "전북특별자치도",
    "전남": "전라남도", "전라남도": "전라남도",
    "경북": "경상북도", "경상북도": "경상북도",
    "경남": "경상남도", "경상남도": "경상남도",
    "제주": "제주특별자치도", "제주특별자치도": "제주특별자치도",
}


class LofinApiError(RuntimeError):
    pass


def _num(value, default=0):
    try:
        return int(round(float(str(value or 0).replace(",", "").strip())))
    except Exception:
        return default


def normalize_region(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text in REGION_MAP:
        return REGION_MAP[text]
    for key, full in REGION_MAP.items():
        if key and key in text:
            return full
    return text


def get_lofin_key():
    return (os.getenv("LOFIN_API_KEY") or get_setting("lofin_api_key", "") or "").strip()


def budget_api_configured():
    return bool(get_lofin_key())


def ensure_budget_schema():
    """Extend budget_items without touching procurement, bids, or users."""
    additions = {
        "executed_amount": "INTEGER NOT NULL DEFAULT 0",
        "remaining_amount": "INTEGER NOT NULL DEFAULT 0",
        "source_date": "TEXT NOT NULL DEFAULT ''",
        "source_key": "TEXT NOT NULL DEFAULT ''",
        "matched_keyword": "TEXT NOT NULL DEFAULT ''",
        "field_name": "TEXT NOT NULL DEFAULT ''",
        "account_name": "TEXT NOT NULL DEFAULT ''",
        "org_code": "TEXT NOT NULL DEFAULT ''",
        "project_code": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    with connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(budget_items)")}
        for name, definition in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE budget_items ADD COLUMN {name} {definition}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_source_key ON budget_items(source_key) WHERE source_key <> ''")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_budget_region ON budget_items(region)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_budget_category ON budget_items(category)")
        conn.execute("DELETE FROM budget_items WHERE is_sample=1")
    if not get_setting("budget_auto_sync_enabled", ""):
        set_setting("budget_auto_sync_enabled", "1")
    if not get_setting("last_budget_sync_result", ""):
        set_setting("last_budget_sync_result", "지방재정365 예산 실데이터를 아직 수집하지 않았습니다.")


def _result_from_json(data):
    if not isinstance(data, dict):
        raise LofinApiError("지방재정365 응답 형식이 올바르지 않습니다.")
    if SERVICE_CODE not in data and "RESULT" in data:
        result = data.get("RESULT")
        if isinstance(result, list) and result:
            result = result[0]
        if isinstance(result, dict):
            code = str(result.get("CODE", ""))
            message = str(result.get("MESSAGE", ""))
            if code == "INFO-200":
                return [], 0, code, message
            raise LofinApiError(f"{code}: {message}")
    root = data.get(SERVICE_CODE)
    if root is None:
        raise LofinApiError("지방재정365 응답에 QWGJK 데이터가 없습니다.")

    entries = root if isinstance(root, list) else [root]
    total = 0
    rows = []
    code = ""
    message = ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        head = entry.get("head")
        heads = head if isinstance(head, list) else ([head] if isinstance(head, dict) else [])
        for h in heads:
            if not isinstance(h, dict):
                continue
            if "list_total_count" in h:
                total = _num(h.get("list_total_count"), total)
            result = h.get("RESULT")
            if isinstance(result, dict):
                code = str(result.get("CODE", code))
                message = str(result.get("MESSAGE", message))
        part = entry.get("row")
        if isinstance(part, list):
            rows.extend(x for x in part if isinstance(x, dict))
        elif isinstance(part, dict):
            rows.append(part)
    if code and code not in ("INFO-000", "INFO-200"):
        raise LofinApiError(f"{code}: {message}")
    if code == "INFO-200":
        return [], total, code, message
    return rows, total or len(rows), code or "INFO-000", message


def _result_from_xml(raw):
    root = ET.fromstring(raw)
    result = root.find(".//RESULT")
    if result is not None:
        code = result.findtext("CODE") or ""
        message = result.findtext("MESSAGE") or ""
        if code not in ("INFO-000", "INFO-200", ""):
            raise LofinApiError(f"{code}: {message}")
        if code == "INFO-200":
            return [], 0, code, message
    rows = [{child.tag: (child.text or "") for child in list(node)} for node in root.findall(".//row")]
    total = _num(root.findtext(".//list_total_count"), len(rows))
    return rows, total, "INFO-000", ""


def _parse_response(raw):
    text = raw.decode("utf-8-sig", "replace").strip()
    if not text:
        raise LofinApiError("지방재정365 API가 빈 응답을 반환했습니다.")
    try:
        return _result_from_json(json.loads(text))
    except json.JSONDecodeError:
        return _result_from_xml(text)


def _request(params, retries=3, timeout=45):
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": "SINSUNG-G2B-Budget-Monitor/2.5"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return _parse_response(response.read())
        except LofinApiError:
            raise
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            last = exc
            if attempt == retries - 1:
                break
            time.sleep(1.2 * (2 ** attempt))
    raise LofinApiError(f"지방재정365 API 연결 실패: {last}")


def fetch_budget_page(fiscal_year, snapshot_date, keyword, page=1, size=1000):
    key = get_lofin_key()
    if not key:
        raise LofinApiError("지방재정365 API 인증키가 설정되지 않았습니다.")
    y = int(fiscal_year)
    d = "".join(ch for ch in str(snapshot_date or "") if ch.isdigit())
    if len(d) != 8:
        raise ValueError("기준일자는 YYYY-MM-DD 형식이어야 합니다.")
    params = {
        "Key": key, "Type": "json", "pIndex": int(page), "pSize": min(max(int(size), 1), 1000),
        "fyr": y, "exe_ymd": d, "dbiz_nm": str(keyword or "").strip(),
    }
    return _request(params)


def classify_project(project_name):
    text = str(project_name or "").casefold()
    rules = (
        ("가로등주", ("가로등주", "등주")),
        ("보안등", ("보안등",)),
        ("경관조명", ("경관조명", "경관 조명")),
        ("투광등", ("투광등",)),
        ("실내조명", ("실내조명", "실내 조명")),
        ("가로등", ("가로등",)),
        ("분전반", ("분전반", "분전함")),
        ("태양광", ("태양광",)),
        ("LED조명", ("led",)),
        ("조명", ("조명",)),
    )
    for category, words in rules:
        if any(word.casefold() in text for word in words):
            return category
    return ""


def normalize_budget_row(raw, matched_keyword, snapshot_date):
    year = _num(raw.get("fyr"), 0)
    region = normalize_region(raw.get("wa_laf_hg_nm"))
    org_name = str(raw.get("laf_hg_nm") or "").strip()
    org_code = str(raw.get("laf_cd") or "").strip()
    project_name = str(raw.get("dbiz_nm") or "").strip()
    project_code = str(raw.get("dbiz_cd") or "").strip()
    department_code = str(raw.get("dept_cd") or "").strip()
    account_code = str(raw.get("acnt_dv_cd") or "").strip()
    account_name = str(raw.get("acnt_dv_nm") or "").strip()
    field_name = str(raw.get("fld_nm") or "").strip()
    category = classify_project(project_name)
    if not category:
        return None

    budget_amount = _num(raw.get("bdg_cash_amt"), 0) or _num(raw.get("cpl_amt"), 0)
    executed_amount = _num(raw.get("ep_amt"), 0)
    remaining_amount = budget_amount - executed_amount
    if budget_amount > 0 and executed_amount <= 0:
        status = "예산편성"
    elif budget_amount > 0 and remaining_amount > 0:
        status = "집행중"
    elif budget_amount > 0:
        status = "집행완료"
    else:
        status = "금액확인"

    rawkey = "|".join([str(year), org_code, department_code, project_code, account_code, project_name])
    source_key = hashlib.sha1(rawkey.encode("utf-8")).hexdigest()
    return {
        "fiscal_year": year, "region": region, "org_name": org_name, "project_name": project_name,
        "category": category, "budget_amount": budget_amount, "executed_amount": executed_amount,
        "remaining_amount": remaining_amount, "status": status, "source": SOURCE_NAME,
        "source_date": str(snapshot_date), "source_key": source_key, "matched_keyword": str(matched_keyword),
        "field_name": field_name, "account_name": account_name, "org_code": org_code, "project_code": project_code,
    }


def upsert_budget_rows(rows, matched_keyword, snapshot_date, seen_keys=None):
    seen_keys = seen_keys if seen_keys is not None else set()
    saved = 0
    with connect() as conn:
        for raw in rows:
            item = normalize_budget_row(raw, matched_keyword, snapshot_date)
            if not item or not item["fiscal_year"] or not item["org_name"] or not item["project_name"]:
                continue
            if item["source_key"] in seen_keys:
                continue
            seen_keys.add(item["source_key"])
            conn.execute(
                """
                INSERT INTO budget_items(
                    fiscal_year,region,org_name,project_name,category,budget_amount,status,source,is_sample,
                    executed_amount,remaining_amount,source_date,source_key,matched_keyword,field_name,
                    account_name,org_code,project_code,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(source_key) WHERE source_key <> '' DO UPDATE SET
                    fiscal_year=excluded.fiscal_year,region=excluded.region,org_name=excluded.org_name,
                    project_name=excluded.project_name,category=excluded.category,budget_amount=excluded.budget_amount,
                    status=excluded.status,source=excluded.source,is_sample=0,
                    executed_amount=excluded.executed_amount,remaining_amount=excluded.remaining_amount,
                    source_date=excluded.source_date,matched_keyword=excluded.matched_keyword,
                    field_name=excluded.field_name,account_name=excluded.account_name,
                    org_code=excluded.org_code,project_code=excluded.project_code,updated_at=CURRENT_TIMESTAMP
                """,
                (
                    item["fiscal_year"], item["region"], item["org_name"], item["project_name"], item["category"],
                    item["budget_amount"], item["status"], item["source"], item["executed_amount"],
                    item["remaining_amount"], item["source_date"], item["source_key"], item["matched_keyword"],
                    item["field_name"], item["account_name"], item["org_code"], item["project_code"],
                ),
            )
            saved += 1
    return saved


def sync_budget_snapshot(fiscal_year=None, snapshot_date=None, keywords=None, max_pages=100):
    ensure_budget_schema()
    fiscal_year = int(fiscal_year or dt.date.today().year)
    snapshot_date = snapshot_date or dt.date.today().isoformat()
    keywords = tuple(keywords or TARGET_KEYWORDS)
    log_id = new_sync_log("BUDGET", str(fiscal_year), str(snapshot_date))
    raw_count = 0
    saved = 0
    seen_keys = set()
    try:
        for keyword in keywords:
            page = 1
            seen_for_keyword = 0
            while page <= max_pages:
                rows, total, _, _ = fetch_budget_page(fiscal_year, snapshot_date, keyword, page=page, size=1000)
                if not rows:
                    break
                raw_count += len(rows)
                seen_for_keyword += len(rows)
                saved += upsert_budget_rows(rows, keyword, snapshot_date, seen_keys)
                if seen_for_keyword >= total:
                    break
                page += 1
                time.sleep(0.1)
            if page > max_pages:
                raise LofinApiError(f"'{keyword}' 예산 조회가 페이지 안전한도 {max_pages}를 초과했습니다.")

        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = f"{fiscal_year}년 / {snapshot_date}: 원본 {raw_count:,}건 확인 · 조명관련 예산 {saved:,}건 저장·갱신"
        set_setting("last_budget_sync", now)
        set_setting("last_budget_sync_date", str(snapshot_date))
        set_setting("last_budget_sync_result", message)
        finish_sync_log(log_id, "OK", saved, message)
        return saved
    except Exception as exc:
        message = str(exc)
        set_setting("last_budget_sync_result", "예산 수집 실패: " + message)
        finish_sync_log(log_id, "ERROR", saved, message)
        raise


def test_budget_api(fiscal_year=None, snapshot_date=None):
    fiscal_year = int(fiscal_year or dt.date.today().year)
    snapshot_date = snapshot_date or dt.date.today().isoformat()
    rows, total, code, message = fetch_budget_page(fiscal_year, snapshot_date, "조명", page=1, size=5)
    return len(rows), total, code, message
