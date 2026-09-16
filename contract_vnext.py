"""G2B vNext service-contract RAW collection.

All service contracts are preserved first. No LED/lighting/pole keyword is used in
request construction or persistence. Contract-to-bid normalization lives in a
separate post-RAW projection module.
"""
import datetime as dt
import hashlib
import json
import math
import urllib.parse

from db import get_service_key
from vnext_http import request as _request
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
    """Preserve every returned service contract and checkpoint page progress."""
    scope = f"{start_date}:{end_date}"
    checkpoint = get_checkpoint(DATASET, scope) if resume else None
    if checkpoint and checkpoint.get("status") == "COMPLETE":
        return {
            "dataset": DATASET,
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

    save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                    page_no=page, source_total=total, fetched_count=fetched,
                    saved_count=saved, status="RUNNING", last_error="")
    try:
        while True:
            items, source_total = fetch_page(start_date, end_date, page=page, rows=page_size)
            total = max(total, int(source_total or 0))
            for row in items:
                preserve_raw(DATASET, _source_key(row), row,
                             source_system=SOURCE_SYSTEM, source_operation=OPERATION,
                             source_date=_source_date(row, end_date))
                saved += 1
            fetched += len(items)
            pages_done += 1
            total_pages = max(1, int(math.ceil(total / float(page_size)))) if total else page
            next_page = page + 1
            done = not items or page >= total_pages
            save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                            page_no=(next_page if not done else page), source_total=total,
                            fetched_count=fetched, saved_count=saved,
                            status=("COMPLETE" if done else "RUNNING"), last_error="")
            if done or (max_pages is not None and pages_done >= int(max_pages)):
                return {
                    "dataset": DATASET, "scope": scope, "fetched": fetched,
                    "saved": saved, "source_total": total, "complete": done,
                    "stopped_at": dt.datetime.now().isoformat(timespec="seconds"),
                }
            page = next_page
    except Exception as exc:
        save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                        page_no=page, source_total=total, fetched_count=fetched,
                        saved_count=saved, status="FAILED", last_error=str(exc)[:1000])
        raise
