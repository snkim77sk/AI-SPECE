"""G2B vNext bid-notice RAW collection.

The production collector intentionally keeps its existing lighting-oriented serving
filters. This module is the independent vNext ingestion path: it requests complete
goods/service notice lists for a date range and preserves every returned source row
before any LED/lighting/pole classification.
"""
import datetime as dt
import hashlib
import urllib.parse

from db import get_service_key
from vnext_http import request as _request
from vnext_paging import source_page_complete
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

SOURCE_SYSTEM = "G2B"
BID_BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
BUSINESS_TYPES = {
    "goods": {
        "dataset": "bid_notice_goods",
        "operation": "getBidPblancListInfoThng",
    },
    "service": {
        "dataset": "bid_notice_service",
        "operation": "getBidPblancListInfoServc",
    },
}


def _service_key():
    key = get_service_key("")
    if not key:
        raise RuntimeError("나라장터 API 인증키가 설정되지 않았습니다.")
    return key


def _spec(business_type):
    key = str(business_type or "").strip().lower()
    if key not in BUSINESS_TYPES:
        raise ValueError("business_type must be 'goods' or 'service'")
    return BUSINESS_TYPES[key]


def _source_key(row):
    """Stable notice identity; title/category text never participates in filtering."""
    notice_no = str(row.get("bidNtceNo") or row.get("bidNoticeNo") or "").strip()
    notice_ord = str(row.get("bidNtceOrd") or row.get("bidNoticeOrd") or "000").strip()
    if notice_no:
        return f"{notice_no}|{notice_ord or '000'}"
    return hashlib.sha1(repr(sorted(row.items())).encode("utf-8")).hexdigest()


def _source_date(row, fallback=""):
    value = str(row.get("bidNtceDt") or row.get("bidNoticeDate") or "").strip()
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(fallback or "")


def fetch_page(business_type, start_date, end_date, page=1, rows=999):
    """Fetch one complete basic-notice page with no keyword/category prefilter."""
    spec = _spec(business_type)
    params = {
        "serviceKey": _service_key(),
        "pageNo": int(page),
        "numOfRows": min(max(int(rows), 1), 999),
        "type": "json",
        "inqryDiv": "1",
        "inqryBgnDt": str(start_date).replace("-", "") + "0000",
        "inqryEndDt": str(end_date).replace("-", "") + "2359",
    }
    url = f"{BID_BASE_URL}/{spec['operation']}?" + urllib.parse.urlencode(params)
    return _request(url, "bid_notice")


def collect_all(business_type, start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    """Collect all rows with atomic RAW/receipt/checkpoint commits; no keyword filter."""
    from vnext_collection import collect_pages
    start_date = dt.date.fromisoformat(str(start_date)).isoformat()
    end_date = dt.date.fromisoformat(str(end_date)).isoformat()
    if start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    page_size = min(max(int(page_size), 1), 999)
    spec = _spec(business_type)
    dataset = spec["dataset"]
    return collect_pages(
        dataset=dataset, scope=f"{start_date}:{end_date}",
        range_start=start_date, range_end=end_date, page_size=page_size,
        max_pages=max_pages, resume=resume,
        fetch=lambda page, size: fetch_page(business_type, start_date, end_date, page=page, rows=size),
        identity=_source_key, source_system=SOURCE_SYSTEM, source_operation=spec["operation"],
        source_date=lambda row: _source_date(row, end_date),
        preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint,
    )


def collect_goods_and_services(start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    """Convenience entry point for the two vNext tender families."""
    return {
        "goods": collect_all("goods", start_date, end_date, page_size=page_size,
                             max_pages=max_pages, resume=resume),
        "service": collect_all("service", start_date, end_date, page_size=page_size,
                               max_pages=max_pages, resume=resume),
    }
