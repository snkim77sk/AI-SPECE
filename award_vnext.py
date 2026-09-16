"""G2B vNext service opening/final-award RAW collectors.

Every upstream row is preserved before normalization. First-rank normalization is
kept separate until the live response fields are confirmed by canary collection.
"""
import datetime as dt
import hashlib
import json
import urllib.parse

from db import get_service_key
from vnext_http import request as _request
from vnext_paging import source_page_complete
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint, save_lifecycle_link

SOURCE_SYSTEM = "G2B"
BASE_URL = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"
STAGES = {
    "opening": ("opening_result_service", "getOpengResultListInfoServc", "HAS_OPENING_RESULT"),
    "award": ("award_result_service", "getScsbidListSttusServc", "HAS_FINAL_AWARD"),
}


def _service_key():
    key = get_service_key("")
    if not key:
        raise RuntimeError("나라장터 API 인증키가 설정되지 않았습니다.")
    return key


def _spec(stage):
    key = str(stage or "").strip().lower()
    if key not in STAGES:
        raise ValueError("stage must be 'opening' or 'award'")
    return STAGES[key]


def _notice_key(row):
    no = str(row.get("bidNtceNo") or row.get("bidNoticeNo") or "").strip()
    order = str(row.get("bidNtceOrd") or row.get("bidNoticeOrd") or "000").strip() or "000"
    return f"{no}|{order}" if no else ""


def _raw_source_key(row):
    """Stable execution/rebid identity, falling back only when official keys are absent.

    `bidClsfcNo` is the execution serial number for the same bid notice and `rbidNo`
    is the rebid number. Keeping these in the source identity lets changed upstream
    payloads become immutable revisions instead of unrelated RAW rows.
    """
    notice = _notice_key(row)
    bid_clsfc = str(row.get("bidClsfcNo") or row.get("bidClsfNo") or "").strip()
    rebid = str(row.get("rbidNo") or row.get("rebidNo") or "").strip()
    if notice:
        return f"{notice}|{bid_clsfc or '0'}|{rebid or '0'}"
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "NO_NOTICE|" + hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _source_date(row, fallback=""):
    value = str(row.get("opengDt") or row.get("opengDate") or row.get("rgstDt") or "").strip()
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(fallback or "")


def fetch_page(stage, start_date, end_date, page=1, rows=999):
    """Fetch one unfiltered 용역 개찰/낙찰 page."""
    _, operation, _ = _spec(stage)
    params = {
        "serviceKey": _service_key(),
        "pageNo": int(page),
        "numOfRows": min(max(int(rows), 1), 999),
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDt": str(start_date).replace("-", "") + "0000",
        "inqryEndDt": str(end_date).replace("-", "") + "2359",
    }
    url = f"{BASE_URL}/{operation}?" + urllib.parse.urlencode(params)
    kind = "opening" if str(stage).strip().lower() == "opening" else "award"
    return _request(url, kind)


def collect_all(stage, start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    """Collect all rows with atomic RAW/receipt/checkpoint commits; no keyword filter."""
    from vnext_collection import collect_pages
    start_date = dt.date.fromisoformat(str(start_date)).isoformat()
    end_date = dt.date.fromisoformat(str(end_date)).isoformat()
    if start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    page_size = min(max(int(page_size), 1), 999)
    dataset, operation, link_type = _spec(stage)
    return collect_pages(
        dataset=dataset, scope=f"{start_date}:{end_date}",
        range_start=start_date, range_end=end_date, page_size=page_size,
        max_pages=max_pages, resume=resume,
        fetch=lambda page, size: fetch_page(stage, start_date, end_date, page=page, rows=size),
        identity=_raw_source_key, source_system=SOURCE_SYSTEM, source_operation=operation,
        source_date=lambda row: _source_date(row, end_date),
        preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint,
        relationships=lambda row, key: [("bid_notice", _notice_key(row), dataset, key, link_type)] if _notice_key(row) else [],
    )


def collect_service_opening(start_date, end_date, **kwargs):
    return collect_all("opening", start_date, end_date, **kwargs)


def collect_service_awards(start_date, end_date, **kwargs):
    return collect_all("award", start_date, end_date, **kwargs)
