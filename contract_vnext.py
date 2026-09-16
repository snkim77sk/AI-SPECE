"""G2B vNext service-contract RAW collection.

All service contracts are preserved first. No LED/lighting/pole keyword is used in
request construction or persistence. Contract-to-bid normalization lives in a
separate post-RAW projection module.
"""
import datetime as dt
import hashlib
import json
import urllib.parse

from db import get_service_key
from vnext_http import request as _request
from vnext_paging import source_page_complete
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET = "contract_service"
SOURCE_SYSTEM = "G2B"
BASE_URL = "https://apis.data.go.kr/1230000/ao/CntrctInfoService"
OPERATION = "getCntrctInfoListServc"


def _service_key():
    key = get_service_key("")
    if not key:
        raise RuntimeError("나라장터 API 인증키가 설정되지 않았습니다.")
    return key


def _source_key(row):
    unty = str(row.get("untyCntrctNo") or "").strip()
    decided = str(row.get("dcsnCntrctNo") or "").strip()
    stable = f"{unty}|{decided}"
    if stable.replace("|", ""):
        return stable
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _source_date(row, fallback=""):
    value = str(row.get("rgstDt") or row.get("cntrctCnclsDate") or row.get("cntrctDate") or "").strip()
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(fallback or "")


def fetch_page(start_date, end_date, page=1, rows=999):
    """Fetch one unfiltered service-contract page by registration date."""
    params = {
        "serviceKey": _service_key(),
        "pageNo": int(page),
        "numOfRows": min(max(int(rows), 1), 999),
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDt": str(start_date).replace("-", "") + "0000",
        "inqryEndDt": str(end_date).replace("-", "") + "2359",
    }
    url = f"{BASE_URL}/{OPERATION}?" + urllib.parse.urlencode(params)
    return _request(url, "contract")


def collect_all(start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    """Collect all rows with atomic RAW/receipt/checkpoint commits; no keyword filter."""
    from vnext_collection import collect_pages
    start_date = dt.date.fromisoformat(str(start_date)).isoformat()
    end_date = dt.date.fromisoformat(str(end_date)).isoformat()
    if start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    page_size = min(max(int(page_size), 1), 999)
    dataset = DATASET
    return collect_pages(
        dataset=dataset, scope=f"{start_date}:{end_date}",
        range_start=start_date, range_end=end_date, page_size=page_size,
        max_pages=max_pages, resume=resume,
        fetch=lambda page, size: fetch_page(start_date, end_date, page=page, rows=size),
        identity=_source_key, source_system=SOURCE_SYSTEM, source_operation=OPERATION,
        source_date=lambda row: _source_date(row, end_date),
        preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint,
    )
