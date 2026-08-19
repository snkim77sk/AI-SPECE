"""Education-office budget integration for 지방교육재정알리미 Open API.

The official portal exposes REST Open APIs for 17 metropolitan/provincial
education offices. Each dataset has its own requestType, so the service name is
stored separately from the API key and can be changed without redeploying.
"""
import datetime as dt
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from db import connect, get_setting, set_setting

VERSION = "2.6.0-education-budget"
ENDPOINT = "https://openapi.eduinfo.go.kr/openApi.do"
DEFAULT_REQUEST_TYPE = "opclTotal"
SOURCE_PREFIX = "지방교육재정알리미"

LIGHTING_KEYWORDS = (
    "led", "조명", "가로등", "보안등", "경관조명", "경관 조명", "투광등",
    "실내조명", "실내 조명", "면조명", "평판등", "평판조명", "패널조명",
    "다운라이트", "터널등", "터널조명", "등기구", "전등",
)

REGION_ALIASES = {
    "서울특별시교육청": "서울특별시", "서울": "서울특별시",
    "부산광역시교육청": "부산광역시", "부산": "부산광역시",
    "대구광역시교육청": "대구광역시", "대구": "대구광역시",
    "인천광역시교육청": "인천광역시", "인천": "인천광역시",
    "광주광역시교육청": "광주광역시", "광주": "광주광역시",
    "대전광역시교육청": "대전광역시", "대전": "대전광역시",
    "울산광역시교육청": "울산광역시", "울산": "울산광역시",
    "세종특별자치시교육청": "세종특별자치시", "세종": "세종특별자치시",
    "경기도교육청": "경기도", "경기": "경기도",
    "강원특별자치도교육청": "강원특별자치도", "강원": "강원특별자치도",
    "충청북도교육청": "충청북도", "충북": "충청북도",
    "충청남도교육청": "충청남도", "충남": "충청남도",
    "전북특별자치도교육청": "전북특별자치도", "전북": "전북특별자치도",
    "전라남도교육청": "전라남도", "전남": "전라남도",
    "경상북도교육청": "경상북도", "경북": "경상북도",
    "경상남도교육청": "경상남도", "경남": "경상남도",
    "제주특별자치도교육청": "제주특별자치도", "제주": "제주특별자치도",
}

_SYNC_LOCK = threading.Lock()
_SYNC_THREAD = None


class EduInfoApiError(RuntimeError):
    pass


def get_api_key():
    return (os.getenv("EDUINFO_API_KEY") or get_setting("eduinfo_api_key", "") or "").strip()


def get_request_type():
    return (os.getenv("EDUINFO_REQUEST_TYPE") or get_setting("eduinfo_request_type", "") or DEFAULT_REQUEST_TYPE).strip()


def configured():
    return bool(get_api_key())


def _norm_key(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


def _pick(row, *names):
    if not isinstance(row, dict):
        return ""
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    norm = {_norm_key(k): v for k, v in row.items()}
    for name in names:
        value = norm.get(_norm_key(name))
        if value not in (None, ""):
            return value
    return ""


def _num(value, default=0):
    try:
        text = str(value or "").replace(",", "").replace("원", "").strip()
        return int(round(float(text))) if text else default
    except Exception:
        return default


def infer_region(text):
    text = str(text or "")
    for key in sorted(REGION_ALIASES, key=len, reverse=True):
        if key in text:
            return REGION_ALIASES[key]
    return ""


def classify_project(text):
    compact = "".join(str(text or "").casefold().split())
    rules = (
        ("다운라이트", ("다운라이트", "downlight")),
        ("터널등", ("터널등", "터널조명")),
        ("보안등", ("보안등", "방범등")),
        ("경관조명", ("경관조명", "경관등")),
        ("투광등", ("투광등", "투광조명")),
        ("실내조명", ("실내조명", "면조명", "평판등", "평판조명", "패널조명")),
        ("가로등", ("가로등", "도로조명")),
        ("LED조명", ("led",)),
        ("조명", ("조명", "등기구", "전등")),
    )
    for category, words in rules:
        if any("".join(w.casefold().split()) in compact for w in words):
            return category
    return ""


def _detail_mapping(category):
    mapping = {
        "다운라이트": ("3911151502", "LED다운라이트", "높음"),
        "터널등": ("3911160304", "LED터널등기구", "높음"),
        "보안등": ("3911160802", "LED보안등기구", "높음"),
        "경관조명": ("3911160501", "LED경관조명기구", "높음"),
        "투광등": ("3911161102", "LED투광등기구", "높음"),
        "실내조명": ("3911210201", "LED실내조명등", "높음"),
        "가로등": ("3911160302", "LED가로등기구", "높음"),
    }
    return mapping.get(category, (
        "3911151502,3911160302,3911160304,3911160501,3911160802,3911161102,3911210201",
        "LED조명 후보군", "낮음",
    ))


def ensure_schema():
    additions = {
        "executed_amount": "INTEGER NOT NULL DEFAULT 0", "remaining_amount": "INTEGER NOT NULL DEFAULT 0",
        "source_date": "TEXT NOT NULL DEFAULT ''", "source_key": "TEXT NOT NULL DEFAULT ''",
        "matched_keyword": "TEXT NOT NULL DEFAULT ''", "field_name": "TEXT NOT NULL DEFAULT ''",
        "account_name": "TEXT NOT NULL DEFAULT ''", "org_code": "TEXT NOT NULL DEFAULT ''",
        "project_code": "TEXT NOT NULL DEFAULT ''", "detail_item_no": "TEXT NOT NULL DEFAULT ''",
        "detail_item_name": "TEXT NOT NULL DEFAULT ''", "match_confidence": "TEXT NOT NULL DEFAULT ''",
        "matched_term": "TEXT NOT NULL DEFAULT ''", "updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    with connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(budget_items)")}
        for name, definition in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE budget_items ADD COLUMN {name} {definition}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_source_key ON budget_items(source_key) WHERE source_key <> ''")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_budget_source ON budget_items(source)")
    if not get_setting("education_budget_auto_sync_enabled", ""):
        set_setting("education_budget_auto_sync_enabled", "1")


def _find_total(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _norm_key(key) in ("listtotalcount", "totalcount", "totcnt", "total", "datacount", "count"):
                n = _num(value, -1)
                if n >= 0:
                    return n
        for value in obj.values():
            found = _find_total(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_total(value)
            if found is not None:
                return found
    return None


def _rowish(value):
    return isinstance(value, dict) and sum(not isinstance(v, (dict, list)) for v in value.values()) >= 2


def _find_rows(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _norm_key(key) in ("row", "rows", "list", "data", "resultlist", "items", "item"):
                if isinstance(value, list):
                    rows = [v for v in value if _rowish(v)]
                    if rows:
                        return rows
                elif _rowish(value):
                    return [value]
        for value in obj.values():
            rows = _find_rows(value)
            if rows:
                return rows
    elif isinstance(obj, list):
        rows = [v for v in obj if _rowish(v)]
        if rows:
            return rows
        for value in obj:
            rows = _find_rows(value)
            if rows:
                return rows
    return []


def _parse_response(raw):
    text = raw.decode("utf-8-sig", "replace").strip()
    if not text:
        raise EduInfoApiError("지방교육재정알리미 API가 빈 응답을 반환했습니다.")
    try:
        data = json.loads(text)
        rows = _find_rows(data)
        total = _find_total(data)
        return rows, int(total if total is not None else len(rows)), "OK", ""
    except json.JSONDecodeError:
        root = ET.fromstring(text)
        code = root.findtext(".//CODE") or root.findtext(".//resultCode") or ""
        message = root.findtext(".//MESSAGE") or root.findtext(".//resultMsg") or ""
        if code and code not in ("0", "00", "INFO-000", "SUCCESS", "OK"):
            raise EduInfoApiError(f"{code}: {message}".strip())
        nodes = root.findall(".//row") or root.findall(".//item")
        rows = [{child.tag: (child.text or "") for child in list(node)} for node in nodes]
        total = _num(root.findtext(".//list_total_count") or root.findtext(".//totalCount"), len(rows))
        return rows, total, code or "OK", message


def fetch_page(fiscal_year, page=1, size=100):
    key = get_api_key()
    if not key:
        raise EduInfoApiError("지방교육재정알리미 OpenAPI 인증키가 설정되지 않았습니다.")
    request_type = get_request_type()
    params = {
        "requestType": request_type, "key": key, "Type": "json",
        "pIndex": int(page), "pSize": min(max(int(size), 1), 1000), "YMQ": int(fiscal_year),
    }
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "SINSUNG-G2B-Education-Budget/2.6"})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return _parse_response(response.read())
    except EduInfoApiError:
        raise
    except Exception as exc:
        raise EduInfoApiError(f"지방교육재정알리미 API 연결 실패: {exc}") from exc


def _project_name(raw):
    value = _pick(raw, "project_name", "projectName", "business_name", "businessName", "bizNm", "bsnsNm",
                  "dbiz_nm", "SAUP_NM", "사업명", "세부사업명", "단위사업명", "정책사업명")
    if value:
        return str(value).strip()
    candidates = [str(v or "").strip() for v in raw.values() if any(k in str(v or "").casefold() for k in LIGHTING_KEYWORDS)]
    return max(candidates, key=len) if candidates else ""


def normalize_row(raw, fiscal_year):
    text = " ".join(str(v or "") for v in raw.values())
    lower = text.casefold()
    matched = next((k for k in LIGHTING_KEYWORDS if k in lower), "")
    if not matched:
        return None
    project_name = _project_name(raw)
    category = classify_project(project_name + " " + text)
    if not project_name or not category:
        return None
    office = str(_pick(raw, "office_name", "officeName", "eduOfficeNm", "ATPT_OFCDC_SC_NM", "시도교육청명", "교육청명", "기관명", "org_name") or "").strip()
    region = infer_region(office + " " + text)
    if not office:
        office = (region + "교육청") if region else "교육청"
    year = _num(_pick(raw, "YMQ", "year", "fiscalYear", "회계연도"), int(fiscal_year))
    budget_amount = _num(_pick(raw, "budget_amount", "budgetAmount", "bdgAmt", "BUDGET_AMT", "예산액", "예산현액", "본예산액", "최종예산액"), 0)
    executed_amount = _num(_pick(raw, "executed_amount", "executedAmount", "expenseAmt", "EXPENDITURE_AMT", "집행액", "지출액", "결산액"), 0)
    remaining_amount = max(0, budget_amount - executed_amount) if budget_amount else 0
    status = "예산편성" if budget_amount and not executed_amount else ("집행중" if remaining_amount else ("집행완료" if budget_amount else "금액확인"))
    code, item_name, confidence = _detail_mapping(category)
    request_type = get_request_type()
    fingerprint = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    source_key = hashlib.sha1(f"EDUINFO|{request_type}|{year}|{office}|{project_name}|{fingerprint}".encode("utf-8")).hexdigest()
    return {
        "fiscal_year": year, "region": region, "org_name": office, "project_name": project_name,
        "category": category, "budget_amount": budget_amount, "executed_amount": executed_amount,
        "remaining_amount": remaining_amount, "status": status, "source": f"{SOURCE_PREFIX}({request_type})",
        "source_date": dt.date.today().isoformat(), "source_key": source_key, "matched_keyword": matched,
        "field_name": "교육비특별회계", "account_name": "", "org_code": str(_pick(raw, "officeCode", "eduOfficeCode", "교육청코드") or ""),
        "project_code": str(_pick(raw, "projectCode", "businessCode", "사업코드", "세부사업코드") or ""),
        "detail_item_no": code, "detail_item_name": item_name, "match_confidence": confidence, "matched_term": matched,
    }


def upsert_rows(rows, fiscal_year, seen=None):
    seen = seen if seen is not None else set()
    saved = 0
    with connect() as conn:
        for raw in rows:
            item = normalize_row(raw, fiscal_year)
            if not item or item["source_key"] in seen:
                continue
            seen.add(item["source_key"])
            conn.execute("""
                INSERT INTO budget_items(
                    fiscal_year,region,org_name,project_name,category,budget_amount,status,source,is_sample,
                    executed_amount,remaining_amount,source_date,source_key,matched_keyword,field_name,
                    account_name,org_code,project_code,detail_item_no,detail_item_name,match_confidence,matched_term,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(source_key) WHERE source_key <> '' DO UPDATE SET
                    fiscal_year=excluded.fiscal_year,region=excluded.region,org_name=excluded.org_name,
                    project_name=excluded.project_name,category=excluded.category,budget_amount=excluded.budget_amount,
                    status=excluded.status,source=excluded.source,is_sample=0,executed_amount=excluded.executed_amount,
                    remaining_amount=excluded.remaining_amount,source_date=excluded.source_date,
                    matched_keyword=excluded.matched_keyword,field_name=excluded.field_name,account_name=excluded.account_name,
                    org_code=excluded.org_code,project_code=excluded.project_code,detail_item_no=excluded.detail_item_no,
                    detail_item_name=excluded.detail_item_name,match_confidence=excluded.match_confidence,
                    matched_term=excluded.matched_term,updated_at=CURRENT_TIMESTAMP
            """, (
                item["fiscal_year"], item["region"], item["org_name"], item["project_name"], item["category"],
                item["budget_amount"], item["status"], item["source"], item["executed_amount"], item["remaining_amount"],
                item["source_date"], item["source_key"], item["matched_keyword"], item["field_name"], item["account_name"],
                item["org_code"], item["project_code"], item["detail_item_no"], item["detail_item_name"],
                item["match_confidence"], item["matched_term"],
            ))
            saved += 1
    return saved


def sync_education_budget(fiscal_year=None, max_pages=100):
    ensure_schema()
    year = int(fiscal_year or dt.date.today().year)
    raw_count = 0
    saved = 0
    seen = set()
    total = None
    for page in range(1, max_pages + 1):
        rows, current_total, _, _ = fetch_page(year, page=page, size=100)
        total = current_total if total is None else total
        if not rows:
            break
        raw_count += len(rows)
        saved += upsert_rows(rows, year, seen)
        if total is not None and raw_count >= total:
            break
        time.sleep(0.1)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if raw_count and saved == 0:
        message = f"교육청 예산 연결 성공 · requestType={get_request_type()} · 원본 {raw_count:,}건 · 조명 세부사업 0건. 세부사업 데이터셋 requestType을 확인해 주세요."
    else:
        message = f"교육청 예산 수집 · {year}년 · requestType={get_request_type()} · 원본 {raw_count:,}건 · 조명관련 {saved:,}건 저장·갱신"
    set_setting("education_budget_sync_status", "완료")
    set_setting("last_education_budget_sync", now)
    set_setting("last_education_budget_sync_result", message)
    set_setting("last_education_budget_auto_attempt_date", dt.date.today().isoformat())
    return saved


def test_api(fiscal_year=None):
    year = int(fiscal_year or dt.date.today().year)
    rows, total, code, message = fetch_page(year, page=1, size=5)
    return len(rows), total, code, f"requestType={get_request_type()}" + (f" · {message}" if message else "")


def start_background_sync(fiscal_year=None):
    global _SYNC_THREAD
    with _SYNC_LOCK:
        if _SYNC_THREAD is not None and _SYNC_THREAD.is_alive():
            return False
        year = int(fiscal_year or dt.date.today().year)
        def runner():
            set_setting("education_budget_sync_status", "수집중")
            try:
                sync_education_budget(year)
            except Exception as exc:
                set_setting("education_budget_sync_status", "오류")
                set_setting("last_education_budget_sync_result", f"교육청 예산 수집 실패: {exc}")
        _SYNC_THREAD = threading.Thread(target=runner, name="education-budget-sync", daemon=True)
        _SYNC_THREAD.start()
        return True
