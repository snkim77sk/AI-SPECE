"""G2B vNext shopping/delivery RAW collection path.

This module deliberately does not write to shopping_contracts. It preserves every
row returned by the official delivery-request-detail operation first; product
classification and serving-table projection happen later.
"""
import datetime as dt
import hashlib
import urllib.parse

from db import get_service_key
from vnext_http import request as _request
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET = "shopping_delivery"
SOURCE_SYSTEM = "G2B"
SHOP_BASE_URL = "https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService"
SHOP_OPERATION = "getDlvrReqDtlInfoList"


def _service_key():
    key = get_service_key("")
    if not key:
        raise RuntimeError("나라장터 API 인증키가 설정되지 않았습니다.")
    return key


def _identity_parts(row):
    req = str(row.get("dlvrReqNo") or row.get("deliveryReqNo") or row.get("reqNo") or "").strip()
    detail = str(
        row.get("prdctSno") or row.get("dlvrReqDtlSeq") or row.get("dlvrReqDtlSn")
        or row.get("detailSeq") or row.get("seq") or ""
    ).strip()
    return req, detail


def _identity_problem(row):
    req, detail = _identity_parts(row)
    if not req or not detail:
        return "MISSING_SHOPPING_DELIVERY_IDENTITY"
    return ""


def _source_key(row):
    """Stable delivery-detail identity without lighting/product filters."""
    req, detail = _identity_parts(row)
    if req and detail:
        return hashlib.sha1(f"{req}|{detail}".encode("utf-8")).hexdigest()
    # Preserve malformed rows before failing the collection checkpoint closed.
    return "MISSING_DELIVERY|" + hashlib.sha1(repr(sorted(row.items())).encode("utf-8")).hexdigest()


def fetch_page(start_date, end_date, page=1, rows=999):
    """Fetch one unclassified delivery-detail page for a date range."""
    params = {
        "serviceKey": _service_key(),
        "pageNo": int(page),
        "numOfRows": min(max(int(rows), 1), 999),
        "type": "json",
        "inqryBgnDate": str(start_date).replace("-", ""),
        "inqryEndDate": str(end_date).replace("-", ""),
    }
    url = f"{SHOP_BASE_URL}/{SHOP_OPERATION}?" + urllib.parse.urlencode(params)
    return _request(url, "shopping")


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
        identity=_source_key, source_system=SOURCE_SYSTEM, source_operation=SHOP_OPERATION,
        source_date=lambda row: str(end_date),
        preserve=preserve_raw, checkpoint=save_checkpoint, lookup=get_checkpoint,
        validate_row=_identity_problem,
    )
