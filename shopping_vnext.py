"""G2B vNext shopping/delivery RAW collection path."""
import datetime as dt
import hashlib
import urllib.parse

from db import get_service_key
from vnext_http import request as _request
from vnext_paging import (
    assert_not_repeated_page, assert_page_consistency, page_fingerprint,
    source_page_complete, validate_resume_page_size,
)
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


def _source_key(row):
    fields = ("dlvrReqNo", "deliveryReqNo", "reqNo", "prdctSno", "dlvrReqDtlSeq",
              "dlvrReqDtlSn", "detailSeq", "seq", "cntrctNo", "contractNo",
              "prdctIdntNo", "goodsIdntNo", "itemId")
    stable = "|".join(str(row.get(name) or "").strip() for name in fields)
    if stable.replace("|", ""):
        return hashlib.sha1(stable.encode("utf-8")).hexdigest()
    return hashlib.sha1(repr(sorted(row.items())).encode("utf-8")).hexdigest()


def fetch_page(start_date, end_date, page=1, rows=999):
    params = {
        "serviceKey": _service_key(), "pageNo": int(page),
        "numOfRows": min(max(int(rows), 1), 999), "type": "json",
        "inqryBgnDate": str(start_date).replace("-", ""),
        "inqryEndDate": str(end_date).replace("-", ""),
    }
    url = f"{SHOP_BASE_URL}/{SHOP_OPERATION}?" + urllib.parse.urlencode(params)
    return _request(url, "shopping")


def collect_all(start_date, end_date, *, page_size=999, max_pages=None, resume=True):
    page_size = min(max(int(page_size), 1), 999)
    scope = f"{start_date}:{end_date}"
    checkpoint = get_checkpoint(DATASET, scope) if resume else None
    if checkpoint and checkpoint.get("status") == "COMPLETE":
        return {"dataset": DATASET, "scope": scope,
                "fetched": int(checkpoint.get("fetched_count") or 0),
                "saved": int(checkpoint.get("saved_count") or 0),
                "source_total": int(checkpoint.get("source_total") or 0),
                "complete": True, "resumed": True}
    validate_resume_page_size(checkpoint, page_size)
    page = max(1, int((checkpoint or {}).get("page_no") or 1))
    fetched = int((checkpoint or {}).get("fetched_count") or 0)
    saved = int((checkpoint or {}).get("saved_count") or 0)
    total = int((checkpoint or {}).get("source_total") or 0)
    last_fp = str((checkpoint or {}).get("last_page_fingerprint") or "")
    pages_done = 0
    save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                    page_no=page, page_size=page_size, last_page_fingerprint=last_fp,
                    source_total=total, fetched_count=fetched, saved_count=saved,
                    status="RUNNING", last_error="")
    try:
        while True:
            rows, reported = fetch_page(start_date, end_date, page=page, rows=page_size)
            count = len(rows)
            current_fp = page_fingerprint(rows)
            assert_not_repeated_page(last_fp, current_fp, count)
            candidate_total = int(reported or 0) or total
            batch_saved = 0
            for row in rows:
                preserve_raw(DATASET, _source_key(row), row, source_system=SOURCE_SYSTEM,
                             source_operation=SHOP_OPERATION, source_date=str(end_date))
                batch_saved += 1
            candidate_fetched = fetched + count
            candidate_saved = saved + batch_saved
            assert_page_consistency(count, candidate_fetched, candidate_total)
            pages_done += 1
            next_page = page + 1
            done = source_page_complete(count, page_size, candidate_fetched, candidate_total)
            save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                            page_no=(page if done else next_page), page_size=page_size,
                            last_page_fingerprint=(current_fp or last_fp), source_total=candidate_total,
                            fetched_count=candidate_fetched, saved_count=candidate_saved,
                            status=("COMPLETE" if done else "RUNNING"), last_error="")
            fetched, saved, total = candidate_fetched, candidate_saved, candidate_total
            last_fp = current_fp or last_fp
            if done or (max_pages is not None and pages_done >= int(max_pages)):
                return {"dataset": DATASET, "scope": scope, "fetched": fetched,
                        "saved": saved, "source_total": total, "complete": done,
                        "stopped_at": dt.datetime.now().isoformat(timespec="seconds")}
            page = next_page
    except Exception as exc:
        try:
            save_checkpoint(DATASET, scope, range_start=str(start_date), range_end=str(end_date),
                            page_no=page, page_size=page_size, last_page_fingerprint=last_fp,
                            source_total=total, fetched_count=fetched, saved_count=saved,
                            status="FAILED", last_error=str(exc)[:1000])
        except Exception:
            pass
        raise
