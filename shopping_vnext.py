"""G2B 4.1 shopping/delivery normalized collection path."""
import datetime as dt
import hashlib
import urllib.parse

import shopping_scope_v4
import shopping_store_v41
from db import get_service_key
from vnext_http import request as _request
from vnext_store import get_checkpoint, save_checkpoint

DATASET="shopping_delivery"; SOURCE_SYSTEM="G2B"
SHOP_BASE_URL="https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService"
SHOP_OPERATION="getDlvrReqDtlInfoList"
SHOPPING_IDENTITY_VERSION="v2-change-order"
SHOPPING_REKEY_MARKER="shopping_delivery_source_key_migration_v2"

def _service_key():
    key=get_service_key("")
    if not key: raise RuntimeError("나라장터 API 인증키가 설정되지 않았습니다.")
    return key

def _first_text(row,*names):
    for name in names:
        value=row.get(name)
        if value is not None and str(value).strip()!="": return str(value).strip()
    return ""

def _change_order(row):
    value=_first_text(
        row,
        "dlvrReqChgOrd",
        "deliveryReqChangeOrder",
        "dlvrReqChangeOrd",
    )
    if not value:
        return "0"
    digits="".join(ch for ch in value if ch.isdigit())
    return str(int(digits)) if digits else value


def _identity_parts(row):
    # The official shopping detail source can return multiple change orders for the
    # same delivery request item in one date/page. Change order is therefore part of
    # the immutable source identity, not merely a later payload revision.
    return (
        _first_text(row,"dlvrReqNo","deliveryReqNo","reqNo"),
        _change_order(row),
        _first_text(row,"prdctSno","dlvrReqDtlSeq","dlvrReqDtlSn","detailSeq","seq"),
    )


def _legacy_source_key(row):
    req, _change, detail=_identity_parts(row)
    if req and detail:
        return hashlib.sha1(f"{req}|{detail}".encode()).hexdigest()
    return ""


def _source_key(row):
    req, change, detail=_identity_parts(row)
    if req and detail:
        return hashlib.sha1(f"{req}|{change}|{detail}".encode()).hexdigest()
    return "MISSING_DELIVERY|"+hashlib.sha1(repr(sorted(row.items())).encode()).hexdigest()


def migrate_legacy_source_keys():
    """4.1 fresh-start compatibility tombstone.

    Production 4.1 starts non-budget collection on 2026-09-01 and never imports
    the pre-4.1 RAW store, so no legacy shopping re-key is required.
    """
    return {
        "status": "NOT_REQUIRED_V41_FRESH_START",
        "migrated_current": 0,
        "copied_revisions": 0,
    }

def _source_date(row, fallback):
    text=_first_text(row,"dlvrReqRcptDate","IntlCntrctDlvrReqDate","cntrctDlvrReqDate","deliveryReqDate")
    digits="".join(ch for ch in text if ch.isdigit())
    if len(digits)>=8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(fallback)

def _identity_problem(row):
    req,_change,detail=_identity_parts(row)
    return "MISSING_SHOPPING_DELIVERY_IDENTITY" if not req or not detail else ""

def prepare_collection_storage():
    """Prepare normalized shopping + checkpoint/receipt schema once for a run."""
    from vnext_collection import ensure_collection_storage

    shopping_store_v41.ensure_schema()
    ensure_collection_storage()


def fetch_page(start_date,end_date,page=1,rows=999):
    params={
        "serviceKey":_service_key(),
        "pageNo":int(page),
        "numOfRows":min(max(int(rows),1),999),
        "type":"json",
        "inqryDiv":"1",
        "inqryBgnDate":str(start_date).replace("-",""),
        "inqryEndDate":str(end_date).replace("-",""),
    }
    return _request(f"{SHOP_BASE_URL}/{SHOP_OPERATION}?"+urllib.parse.urlencode(params),"shopping")

def collect_all(start_date,end_date,*,page_size=999,max_pages=None,resume=True,
                progress=None,storage_prepared=False):
    from vnext_collection import collect_pages
    start_obj=shopping_scope_v4.validate_start_date(start_date)
    end_obj=dt.date.fromisoformat(str(end_date))
    start_date=start_obj.isoformat(); end_date=end_obj.isoformat()
    if start_date>end_date: raise ValueError("start_date must not exceed end_date")
    page_size=min(max(int(page_size),1),999)
    prepared=bool(storage_prepared)
    if not prepared:
        prepare_collection_storage()
        prepared=True
    return collect_pages(
        dataset=DATASET,scope=f"{start_date}:{end_date}",
        range_start=start_date,range_end=end_date,
        page_size=page_size,max_pages=max_pages,resume=resume,
        fetch=lambda page,size:fetch_page(start_date,end_date,page=page,rows=size),
        identity=_source_key,source_system=SOURCE_SYSTEM,source_operation=SHOP_OPERATION,
        source_date=lambda row:_source_date(row,end_date),
        preserve=shopping_store_v41.preserve_record,checkpoint=save_checkpoint,
        lookup=lambda dataset,scope:get_checkpoint(
            dataset,scope,schema_prepared=prepared
        ),
        validate_row=_identity_problem,progress=progress,
        preserve_filter=shopping_scope_v4.should_store,
        checkpoint_contract=shopping_scope_v4.SCOPE_VERSION,
        storage_prepared=prepared,
    )
