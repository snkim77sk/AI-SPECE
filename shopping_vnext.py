"""G2B vNext shopping/delivery RAW collection path.

This module deliberately does not write to shopping_contracts.  It preserves every
row returned by the official delivery-request-detail operation first; product
classification and serving-table projection happen later.
"""
import datetime as dt
import hashlib
import math
import urllib.parse

from collector_v200 import SHOP_BASE_URL, SHOP_OPERATION, _request
from db import get_setting
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET = "shopping_delivery"
SOURCE_SYSTEM = "G2B"


def _service_key():
    key = (get_setting("api_service_key", "") or "").strip()
    if not key:
        raise RuntimeError("나라장터 API 인증키가 설정되지 않았습니다.")
    return key


def _source_key(row):
    """Stable identity without using lighting/product filters."""
    fields = (
        "dlvrReqNo", "deliveryReqNo", "reqNo",
        "prdctSno", "dlvrReqDtlSeq", "dlvrReqDtlSn", "detailSeq", "seq",
        "cntrctNo", "contractNo", "prdctIdntNo", "goodsIdntNo", "itemId",
    )
    parts = [str(row.get(name) or "").strip() for name in fields]
    stable = "|".join(parts)
    if stable.replace("|", ""):
        return hashlib.sha1(stable.encode("utf-8")).hexdigest()
    return hashlib.sha1(repr(sorted(row.items())).encode("utf-8")).hexdigest()


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
    return _request(url, "shop")


def collect_all(start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    """Preserve every returned row to RAW, independent of product classification."""
    scope = f"{start_date}:{end_date}"
    checkpoint = get_checkpoint(DATASET, scope) if resume else None
    page = max(1, int((checkpoint or {}).get("page_no") or 1))
    fetched = int((checkpoint or {}).get("fetched_count") or 0)
    saved = int((checkpoint or {}).get("saved_count") or 0)
    pages_done = 0
    total = int((checkpoint or {}).get("source_total") or 0)

    save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                    page_no=page, source_total=total, fetched_count=fetched,
                    saved_count=saved, status="RUNNING", last_error="")
    try:
        while True:
            rows, source_total = fetch_page(start_date, end_date, page=page, rows=page_size)
            total = max(total, int(source_total or 0))
            for row in rows:
                preserve_raw(DATASET, _source_key(row), row,
                             source_system=SOURCE_SYSTEM, source_operation=SHOP_OPERATION,
                             source_date=str(end_date))
                saved += 1
            fetched += len(rows)
            pages_done += 1
            total_pages = max(1, int(math.ceil(total / float(page_size)))) if total else page
            next_page = page + 1
            done = not rows or page >= total_pages
            save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                            page_no=(next_page if not done else page), source_total=total,
                            fetched_count=fetched, saved_count=saved,
                            status=("COMPLETE" if done else "RUNNING"), last_error="")
            if done or (max_pages is not None and pages_done >= int(max_pages)):
                return {"dataset": DATASET, "scope": scope, "fetched": fetched,
                        "saved": saved, "source_total": total, "complete": done,
                        "stopped_at": dt.datetime.now().isoformat(timespec="seconds")}
            page = next_page
    except Exception as exc:
        save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                        page_no=page, source_total=total, fetched_count=fetched,
                        saved_count=saved, status="FAILED", last_error=str(exc)[:1000])
        raise
