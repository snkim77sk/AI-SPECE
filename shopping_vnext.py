"""G2B vNext shopping/delivery RAW collection path."""
import datetime as dt
import hashlib
import json
import urllib.parse

import shopping_scope_v4
from db import connect, get_service_key
from vnext_http import request as _request
from vnext_schema import ensure_vnext_schema
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

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
    """Re-key legacy shopping RAW without losing immutable source payloads.

    v3.1.14 and earlier keyed shopping rows as request+item. That collapses distinct
    dlvrReqChgOrd rows. Copy every immutable revision to the canonical
    request+change-order+item key, rebuild current RAW snapshots under those keys,
    remove only the obsolete current-index row/classification, and retain the old
    immutable revisions so historical collection receipts remain auditable.
    """
    with connect() as conn:
        ensure_vnext_schema(conn)
        marker=conn.execute(
            "SELECT value FROM app_settings WHERE key=?",
            (SHOPPING_REKEY_MARKER,),
        ).fetchone()
        if marker and str(marker["value"] or "").startswith("complete"):
            return {"status":"SKIPPED","migrated_current":0,"copied_revisions":0}

        current_rows=conn.execute(
            "SELECT source_key,payload_json,source_system,source_operation,source_date "
            "FROM raw_records WHERE dataset=? ORDER BY id",
            (DATASET,),
        ).fetchall()
        migrated=0
        copied=0
        for current in current_rows:
            try:
                payload=json.loads(str(current["payload_json"] or "{}"))
            except (TypeError,ValueError):
                continue
            old_key=str(current["source_key"] or "")
            legacy_key=_legacy_source_key(payload)
            canonical_key=_source_key(payload)
            if not legacy_key or old_key != legacy_key or canonical_key == old_key:
                continue

            revisions=conn.execute(
                "SELECT source_system,source_operation,source_date,payload_json "
                "FROM raw_record_revisions WHERE dataset=? AND source_key=? ORDER BY id",
                (DATASET,old_key),
            ).fetchall()
            if not revisions:
                revisions=[current]

            for revision in revisions:
                try:
                    revision_payload=json.loads(str(revision["payload_json"] or "{}"))
                except (TypeError,ValueError):
                    continue
                new_key=_source_key(revision_payload)
                if not new_key or new_key.startswith("MISSING_DELIVERY|"):
                    continue
                preserve_raw(
                    DATASET,
                    new_key,
                    revision_payload,
                    source_system=str(revision["source_system"] or SOURCE_SYSTEM),
                    source_operation=str(revision["source_operation"] or SHOP_OPERATION),
                    source_date=str(revision["source_date"] or ""),
                    _conn=conn,
                )
                copied += 1

            # Ensure the current legacy snapshot is represented under its canonical key
            # even if a pre-vNext revision row was missing.
            preserve_raw(
                DATASET,
                canonical_key,
                payload,
                source_system=str(current["source_system"] or SOURCE_SYSTEM),
                source_operation=str(current["source_operation"] or SHOP_OPERATION),
                source_date=str(current["source_date"] or ""),
                _conn=conn,
            )
            conn.execute(
                "DELETE FROM classifications WHERE entity_type=? AND entity_key=?",
                (DATASET,old_key),
            )
            conn.execute(
                "DELETE FROM raw_records WHERE dataset=? AND source_key=?",
                (DATASET,old_key),
            )
            migrated += 1

        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SHOPPING_REKEY_MARKER,f"complete:{migrated}:{copied}"),
        )
    return {"status":"COMPLETE","migrated_current":migrated,"copied_revisions":copied}


def _source_date(row, fallback):
    text=_first_text(row,"dlvrReqRcptDate","IntlCntrctDlvrReqDate","cntrctDlvrReqDate","deliveryReqDate")
    digits="".join(ch for ch in text if ch.isdigit())
    if len(digits)>=8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(fallback)

def _identity_problem(row):
    req,_change,detail=_identity_parts(row)
    return "MISSING_SHOPPING_DELIVERY_IDENTITY" if not req or not detail else ""

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

def collect_all(start_date,end_date,*,page_size=999,max_pages=None,resume=True,progress=None):
    from vnext_collection import collect_pages
    start_obj=shopping_scope_v4.validate_start_date(start_date)
    end_obj=dt.date.fromisoformat(str(end_date))
    start_date=start_obj.isoformat(); end_date=end_obj.isoformat()
    if start_date>end_date: raise ValueError("start_date must not exceed end_date")
    page_size=min(max(int(page_size),1),999)
    return collect_pages(
        dataset=DATASET,scope=f"{start_date}:{end_date}",
        range_start=start_date,range_end=end_date,
        page_size=page_size,max_pages=max_pages,resume=resume,
        fetch=lambda page,size:fetch_page(start_date,end_date,page=page,rows=size),
        identity=_source_key,source_system=SOURCE_SYSTEM,source_operation=SHOP_OPERATION,
        source_date=lambda row:_source_date(row,end_date),
        preserve=preserve_raw,checkpoint=save_checkpoint,lookup=get_checkpoint,
        validate_row=_identity_problem,progress=progress,
        preserve_filter=shopping_scope_v4.should_store,
        checkpoint_contract=shopping_scope_v4.SCOPE_VERSION,
    )
