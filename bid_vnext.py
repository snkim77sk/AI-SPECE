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
from vnext_paging import (
    assert_not_repeated_page,
    assert_page_consistency,
    page_fingerprint,
    source_page_complete,
    validate_resume_page_size,
)
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

SOURCE_SYSTEM = "G2B"
BID_BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
BUSINESS_TYPES = {
    "goods": {"dataset": "bid_notice_goods", "operation": "getBidPblancListInfoThng"},
    "service": {"dataset": "bid_notice_service", "operation": "getBidPblancListInfoServc"},
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
    spec = _spec(business_type)
    params = {
        "serviceKey": _service_key(), "pageNo": int(page),
        "numOfRows": min(max(int(rows), 1), 999), "type": "json", "inqryDiv": "1",
        "inqryBgnDt": str(start_date).replace("-", "") + "0000",
        "inqryEndDt": str(end_date).replace("-", "") + "2359",
    }
    url = f"{BID_BASE_URL}/{spec['operation']}?" + urllib.parse.urlencode(params)
    return _request(url, "bid_notice")


def collect_all(business_type, start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    page_size = min(max(int(page_size), 1), 999)
    spec = _spec(business_type)
    dataset = spec["dataset"]
    scope = f"{start_date}:{end_date}"
    checkpoint = get_checkpoint(dataset, scope) if resume else None
    if checkpoint and checkpoint.get("status") == "COMPLETE":
        return {
            "dataset": dataset, "scope": scope,
            "fetched": int(checkpoint.get("fetched_count") or 0),
            "saved": int(checkpoint.get("saved_count") or 0),
            "source_total": int(checkpoint.get("source_total") or 0),
            "complete": True, "resumed": True,
        }
    validate_resume_page_size(checkpoint, page_size)

    page = max(1, int((checkpoint or {}).get("page_no") or 1))
    fetched = int((checkpoint or {}).get("fetched_count") or 0)
    saved = int((checkpoint or {}).get("saved_count") or 0)
    total = int((checkpoint or {}).get("source_total") or 0)
    last_fingerprint = str((checkpoint or {}).get("last_page_fingerprint") or "")
    pages_done = 0

    save_checkpoint(dataset, scope, range_start=str(start_date), range_end=str(end_date),
                    page_no=page, page_size=page_size, last_page_fingerprint=last_fingerprint,
                    source_total=total, fetched_count=fetched, saved_count=saved,
                    status="RUNNING", last_error="")
    try:
        while True:
            items, source_total = fetch_page(business_type, start_date, end_date, page=page, rows=page_size)
            batch_count = len(items)
            current_fingerprint = page_fingerprint(items)
            assert_not_repeated_page(last_fingerprint, current_fingerprint, batch_count)
            reported_total = int(source_total or 0)
            candidate_total = reported_total if reported_total > 0 else total
            batch_saved = 0
            for row in items:
                preserve_raw(dataset, _source_key(row), row,
                             source_system=SOURCE_SYSTEM, source_operation=spec["operation"],
                             source_date=_source_date(row, end_date))
                batch_saved += 1
            candidate_fetched = fetched + batch_count
            candidate_saved = saved + batch_saved
            assert_page_consistency(batch_count, candidate_fetched, candidate_total)
            pages_done += 1
            next_page = page + 1
            done = source_page_complete(batch_count, page_size, candidate_fetched, candidate_total)
            save_checkpoint(dataset, scope, range_start=str(start_date), range_end=str(end_date),
                            page_no=(next_page if not done else page), page_size=page_size,
                            last_page_fingerprint=(current_fingerprint or last_fingerprint),
                            source_total=candidate_total, fetched_count=candidate_fetched,
                            saved_count=candidate_saved, status=("COMPLETE" if done else "RUNNING"),
                            last_error="")
            fetched, saved, total = candidate_fetched, candidate_saved, candidate_total
            last_fingerprint = current_fingerprint or last_fingerprint
            if done or (max_pages is not None and pages_done >= int(max_pages)):
                return {
                    "dataset": dataset, "scope": scope, "fetched": fetched, "saved": saved,
                    "source_total": total, "complete": done,
                    "stopped_at": dt.datetime.now().isoformat(timespec="seconds"),
                }
            page = next_page
    except Exception as exc:
        try:
            save_checkpoint(dataset, scope, range_start=str(start_date), range_end=str(end_date),
                            page_no=page, page_size=page_size,
                            last_page_fingerprint=last_fingerprint, source_total=total,
                            fetched_count=fetched, saved_count=saved, status="FAILED",
                            last_error=str(exc)[:1000])
        except Exception:
            pass
        raise


def collect_goods_and_services(start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    return {
        "goods": collect_all("goods", start_date, end_date, page_size=page_size,
                             max_pages=max_pages, resume=resume),
        "service": collect_all("service", start_date, end_date, page_size=page_size,
                               max_pages=max_pages, resume=resume),
    }
