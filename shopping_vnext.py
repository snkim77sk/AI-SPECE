"""G2B vNext shopping/delivery RAW collection path."""
import datetime as dt
import hashlib
import urllib.parse

from db import get_service_key
from vnext_http import request as _request
from vnext_store import get_checkpoint, preserve_raw, save_checkpoint

DATASET="shopping_delivery"; SOURCE_SYSTEM="G2B"
SHOP_BASE_URL="https://apis.data.go.kr/1230000/at/ShoppingMallPrdctInfoService"
SHOP_OPERATION="getDlvrReqDtlInfoList"

def _service_key():
    key=get_service_key("")
    if not key: raise RuntimeError("나라장터 API 인증키가 설정되지 않았습니다.")
    return key

def _first_text(row,*names):
    for name in names:
        value=row.get(name)
        if value is not None and str(value).strip()!="": return str(value).strip()
    return ""

def _identity_parts(row):
    return _first_text(row,"dlvrReqNo","deliveryReqNo","reqNo"), _first_text(row,"prdctSno","dlvrReqDtlSeq","dlvrReqDtlSn","detailSeq","seq")

def _identity_problem(row):
    req,detail=_identity_parts(row)
    return "MISSING_SHOPPING_DELIVERY_IDENTITY" if not req or not detail else ""

def _source_key(row):
    req,detail=_identity_parts(row)
    if req and detail: return hashlib.sha1(f"{req}|{detail}".encode()).hexdigest()
    return "MISSING_DELIVERY|"+hashlib.sha1(repr(sorted(row.items())).encode()).hexdigest()

def fetch_page(start_date,end_date,page=1,rows=999):
    params={"serviceKey":_service_key(),"pageNo":int(page),"numOfRows":min(max(int(rows),1),999),"type":"json","inqryBgnDate":str(start_date).replace("-",""),"inqryEndDate":str(end_date).replace("-","")}
    return _request(f"{SHOP_BASE_URL}/{SHOP_OPERATION}?"+urllib.parse.urlencode(params),"shopping")

def collect_all(start_date,end_date,*,page_size=999,max_pages=None,resume=True):
    from vnext_collection import collect_pages
    start_date=dt.date.fromisoformat(str(start_date)).isoformat(); end_date=dt.date.fromisoformat(str(end_date)).isoformat()
    if start_date>end_date: raise ValueError("start_date must not exceed end_date")
    page_size=min(max(int(page_size),1),999)
    return collect_pages(dataset=DATASET,scope=f"{start_date}:{end_date}",range_start=start_date,range_end=end_date,page_size=page_size,max_pages=max_pages,resume=resume,fetch=lambda page,size:fetch_page(start_date,end_date,page=page,rows=size),identity=_source_key,source_system=SOURCE_SYSTEM,source_operation=SHOP_OPERATION,source_date=lambda row:str(end_date),preserve=preserve_raw,checkpoint=save_checkpoint,lookup=get_checkpoint,validate_row=_identity_problem)
