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
    notice = _notice_key(row) or "NO_NOTICE"
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return f"{notice}|{digest}"


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
    dataset, operation, link_type = _spec(stage)
    scope = f"{start_date}:{end_date}"
    checkpoint = get_checkpoint(dataset, scope) if resume else None
    if checkpoint and checkpoint.get("status") == "COMPLETE":
        return {
            "dataset": dataset,
            "scope": scope,
            "fetched": int(checkpoint.get("fetched_count") or 0),
            "saved": int(checkpoint.get("saved_count") or 0),
            "source_total": int(checkpoint.get("source_total") or 0),
            "complete": True,
            "resumed": True,
        }

    page = max(1, int((checkpoint or {}).get("page_no") or 1))
    fetched = int((checkpoint or {}).get("fetched_count") or 0)
    saved = int((checkpoint or {}).get("saved_count") or 0)
    total = int((checkpoint or {}).get("source_total") or 0)
    pages_done = 0
    save_checkpoint(dataset, scope, range_start=str(start_date), range_end=str(end_date),
                    page_no=page, source_total=total, fetched_count=fetched,
                    saved_count=saved, status="RUNNING", last_error="")
    try:
        while True:
            items, source_total = fetch_page(stage, start_date, end_date, page=page, rows=page_size)
            reported_total = int(source_total or 0)
            if reported_total > 0:
                total = reported_total
            for row in items:
                raw_key = _raw_source_key(row)
                preserve_raw(dataset, raw_key, row, source_system=SOURCE_SYSTEM,
                             source_operation=operation, source_date=_source_date(row, end_date))
                saved += 1
                notice = _notice_key(row)
                if notice:
                    save_lifecycle_link("bid_notice", notice, dataset, raw_key, link_type,
                                        confidence=1.0, reason="exact bid notice identity")
            fetched += len(items)
            pages_done += 1
            next_page = page + 1
            done = source_page_complete(len(items), page_size, fetched, total)
            save_checkpoint(dataset, scope, range_start=str(start_date), range_end=str(end_date),
                            page_no=(next_page if not done else page), source_total=total,
                            fetched_count=fetched, saved_count=saved,
                            status=("COMPLETE" if done else "RUNNING"), last_error="")
            if done or (max_pages is not None and pages_done >= int(max_pages)):
                return {
                    "dataset": dataset, "scope": scope, "fetched": fetched,
                    "saved": saved, "source_total": total, "complete": done,
                    "stopped_at": dt.datetime.now().isoformat(timespec="seconds"),
                }
            page = next_page
    except Exception as exc:
        save_checkpoint(dataset, scope, range_start=str(start_date), range_end=str(end_date),
                        page_no=page, source_total=total, fetched_count=fetched,
                        saved_count=saved, status="FAILED", last_error=str(exc)[:1000])
        raise


def collect_service_opening(start_date, end_date, **kwargs):
    return collect_all("opening", start_date, end_date, **kwargs)


def collect_service_awards(start_date, end_date, **kwargs):
    return collect_all("award", start_date, end_date, **kwargs)
