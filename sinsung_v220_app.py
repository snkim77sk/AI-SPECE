"""Cafe24-safe application wrapper for SINSUNG G2B DATA VIEW 2.2.1.

Keeps the proven 2.2 startup/automatic collection path and adds one isolated
manual upload path for historical shopping delivery-detail data only.
"""
import asyncio
import csv
import datetime as dt
import hashlib
import hmac
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from email import policy
from email.parser import BytesParser
from urllib.parse import quote

import app as legacy_app

APP_VERSION = "2.2.1"
SHOP_HISTORY_START = "2025-01-01"
SHOP_HISTORY_END = "2026-07-31"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

_original_manual_start = legacy_app._start_background_collect
_original_background_collect = legacy_app._background_collect
_original_proxy = legacy_app._proxy


def _set_manual_state(active, source=""):
    try:
        from db import set_setting
        set_setting("manual_sync_active", "1" if active else "0")
        set_setting("manual_sync_source", source if active else "")
    except Exception:
        pass


def _background_collect_v220(path, body, headers):
    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    try:
        return _original_background_collect(path, body, headers)
    finally:
        _set_manual_state(False, "")


def _start_background_collect_v220(path, body, request_headers):
    try:
        from db import get_setting
        if get_setting("last_auto_sync_status", "") == "수집중":
            source = get_setting("last_auto_sync_current_source", "") or "자동수집"
            return False, f"{source} 자동수집이 진행 중입니다. 완료 후 수동수집을 실행해 주세요."
        if get_setting("manual_sync_active", "0") == "1":
            source = get_setting("manual_sync_source", "") or "수동수집"
            return False, f"이미 {source} 수동수집이 진행 중입니다."
    except Exception:
        pass

    label = legacy_app._SYNC_PATHS.get(path, (path, ""))[0]
    _set_manual_state(True, label)
    ok, message = _original_manual_start(path, body, request_headers)
    if not ok:
        _set_manual_state(False, "")
    return ok, message


def _upload_token():
    secret = os.getenv("DASHBOARD_SECRET", "")
    if not secret:
        return ""
    return hmac.new(
        secret.encode("utf-8"),
        b"sinsung-shop-history-upload-2.2.1",
        hashlib.sha256,
    ).hexdigest()


def _extract_multipart_file(body, content_type):
    if "multipart/form-data" not in str(content_type or "").lower():
        raise ValueError("파일 업로드 형식이 아닙니다.")
    message = BytesParser(policy=policy.default).parsebytes(
        ("Content-Type: " + str(content_type) + "\r\nMIME-Version: 1.0\r\n\r\n").encode("utf-8")
        + bytes(body or b"")
    )
    token = ""
    filename = ""
    payload = b""
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition") or ""
        if name == "upload_token":
            token = (part.get_content() or "").strip()
        elif name == "file":
            filename = os.path.basename(part.get_filename() or "")
            payload = part.get_payload(decode=True) or b""
    if not filename or not payload:
        raise ValueError("업로드할 CSV 또는 XLSX 파일을 선택해 주세요.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("파일이 50MB를 초과합니다. 파일을 나누어 올려 주세요.")
    return token, filename, payload


def _clean_header(value):
    return re.sub(r"\s+", "", str(value or "")).replace("\ufeff", "")


HEADER_ALIASES = {
    "계약일자": "cntrctDt", "계약일": "cntrctDt",
    "납품요구접수일자": "dlvrReqRcptDate", "납품요구일자": "dlvrReqRcptDate", "납품요구일": "dlvrReqRcptDate",
    "수요기관명": "dminsttNm", "수요기관": "dminsttNm",
    "수요기관지역": "dminsttRgnNm", "수요기관지역명": "dminsttRgnNm",
    "수요기관주소": "dminsttAddr", "납품장소": "dlvrDstnAddr",
    "계약업체명": "corpNm", "계약업체": "corpNm", "업체명": "corpNm", "공급업체명": "corpNm",
    "세부품명번호": "dtilPrdctClsfcNo", "세부품목번호": "dtilPrdctClsfcNo",
    "세부품명": "dtilPrdctClsfcNoNm", "세부품목명": "dtilPrdctClsfcNoNm",
    "물품식별번호": "prdctIdntNo", "식별번호": "prdctIdntNo",
    "물품식별번호명": "prdctIdntNoNm", "물품명": "prdctIdntNoNm", "품명": "prdctIdntNoNm", "제품명": "prdctIdntNoNm",
    "모델명": "modelNm", "규격": "prdctSpecNm", "단위": "prdctUnit",
    "단가": "prdctUprc", "제품단가": "prdctUprc",
    "수량": "prdctQty", "납품요구수량": "prdctQty",
    "금액": "prdctAmt", "제품금액": "prdctAmt", "납품요구금액": "prdctAmt",
    "납품요구명": "dlvrReqNm", "계약명": "cntrctNm", "사업명": "dlvrReqNm",
    "계약번호": "cntrctNo", "납품요구번호": "dlvrReqNo",
    "납품요구상세순번": "prdctSno", "순번": "prdctSno",
    "계약업체사업자등록번호": "cntrctCorpBizno", "사업자등록번호": "cntrctCorpBizno",
    "최종납품요구여부": "fnlDlvrReqYn", "최종여부": "fnlDlvrReqYn",
    "계약체결형태명": "cntrctCnclsStleNm", "계약방법": "cntrctCnclsStleNm",
    "납품기한": "dlvrTmlmtDate", "납품기한일자": "dlvrTmlmtDate",
}
DATE_KEYS = {"cntrctDt", "dlvrReqRcptDate", "dlvrTmlmtDate"}


def _canonical_header(value):
    raw = str(value or "").strip().replace("\ufeff", "")
    return HEADER_ALIASES.get(_clean_header(raw), raw)


def _excel_serial_to_date(value):
    try:
        number = float(str(value).strip())
    except Exception:
        return value
    if 20000 <= number <= 70000:
        date_value = dt.date(1899, 12, 30) + dt.timedelta(days=int(number))
        return date_value.isoformat()
    return value


def _normalize_uploaded_row(row):
    out = {}
    for key, value in (row or {}).items():
        canonical = _canonical_header(key)
        if not canonical:
            continue
        if canonical in DATE_KEYS:
            value = _excel_serial_to_date(value)
        if value is None:
            value = ""
        out[canonical] = str(value).strip()
    return out


def _decode_csv(payload):
    last = None
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError as exc:
            last = exc
    raise ValueError(f"CSV 문자 인코딩을 읽을 수 없습니다: {last}")


def _parse_csv_rows(payload):
    text = _decode_csv(payload)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV 첫 행에서 컬럼명을 찾을 수 없습니다.")
    return [_normalize_uploaded_row(row) for row in reader if any(str(v or "").strip() for v in row.values())]


def _xlsx_cell_text(cell, shared):
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//{*}t"))
    value = cell.findtext("{*}v", default="")
    if kind == "s":
        try:
            return shared[int(value)]
        except Exception:
            return value
    if kind == "b":
        return "1" if value == "1" else "0"
    return value


def _column_index(ref):
    letters = "".join(ch for ch in str(ref or "") if ch.isalpha()).upper()
    result = 0
    for ch in letters:
        result = result * 26 + (ord(ch) - 64)
    return max(0, result - 1)


def _parse_xlsx_rows(payload):
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except Exception as exc:
        raise ValueError(f"XLSX 파일을 열 수 없습니다: {exc}") from exc
    names = set(archive.namelist())
    sheet_names = sorted(name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
    if not sheet_names:
        raise ValueError("XLSX에서 워크시트를 찾을 수 없습니다.")

    shared = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in root.findall(".//{*}si"):
            shared.append("".join(t.text or "" for t in item.findall(".//{*}t")))

    sheet = ET.fromstring(archive.read(sheet_names[0]))
    matrix = []
    for row in sheet.findall(".//{*}sheetData/{*}row"):
        values = {}
        max_index = -1
        for cell in row.findall("{*}c"):
            idx = _column_index(cell.attrib.get("r", ""))
            values[idx] = _xlsx_cell_text(cell, shared)
            max_index = max(max_index, idx)
        if max_index >= 0:
            matrix.append([values.get(i, "") for i in range(max_index + 1)])
    if not matrix:
        raise ValueError("XLSX 첫 시트에 데이터가 없습니다.")

    headers = [_canonical_header(v) for v in matrix[0]]
    rows = []
    for values in matrix[1:]:
        row = {}
        for i, header in enumerate(headers):
            if not header:
                continue
            value = values[i] if i < len(values) else ""
            if header in DATE_KEYS:
                value = _excel_serial_to_date(value)
            row[header] = str(value or "").strip()
        if any(row.values()):
            rows.append(row)
    return rows


def _parse_shop_history_file(filename, payload):
    lower = str(filename or "").lower()
    if lower.endswith(".csv"):
        return _parse_csv_rows(payload)
    if lower.endswith(".xlsx"):
        return _parse_xlsx_rows(payload)
    raise ValueError("CSV 또는 XLSX 파일만 업로드할 수 있습니다. XLS 파일은 XLSX로 저장 후 올려 주세요.")


def _process_shop_history_upload(filename, payload):
    from collector_v200 import SHOP_DETAIL_ITEM_NOS, normalize_shop_item, upsert_shop
    from db import set_setting

    rows = _parse_shop_history_file(filename, payload)
    if not rows:
        raise ValueError("업로드 파일에서 데이터 행을 찾지 못했습니다.")

    in_period = []
    outside = 0
    missing_date = 0
    non_target = 0
    for row in rows:
        normalized = normalize_shop_item(row)
        base_date = normalized.get("base_date", "")
        if not base_date:
            missing_date += 1
            continue
        if not (SHOP_HISTORY_START <= base_date <= SHOP_HISTORY_END):
            outside += 1
            continue
        if normalized.get("detail_item_no", "") not in SHOP_DETAIL_ITEM_NOS:
            non_target += 1
            continue
        in_period.append(row)

    saved, matched, skipped = upsert_shop(in_period, target_only=True)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = (
        f"{filename}: 전체 {len(rows):,}건 / 기간내 대상 {matched:,}건 / "
        f"저장·갱신 {saved:,}건 / 필수값 누락 {skipped + missing_date:,}건 / "
        f"기간외 {outside:,}건 / 대상품목외 {non_target:,}건"
    )
    set_setting("last_shop_upload_at", now)
    set_setting("last_shop_upload_file", filename)
    set_setting("last_shop_upload_rows", str(len(rows)))
    set_setting("last_shop_upload_saved", str(saved))
    set_setting("last_shop_upload_result", result)
    set_setting("last_shop_upload_error", "")
    return result


async def _proxy_v221(request, path):
    if request.method == "POST" and path == "upload-shop-history":
        try:
            from db import get_setting, set_setting
            if get_setting("last_auto_sync_status", "") == "수집중":
                raise RuntimeError("자동수집이 진행 중입니다. 완료 후 업로드해 주세요.")
            if get_setting("manual_sync_active", "0") == "1":
                source = get_setting("manual_sync_source", "") or "수동 작업"
                raise RuntimeError(f"{source}가 진행 중입니다. 완료 후 업로드해 주세요.")

            body = await request.body()
            token, filename, payload = _extract_multipart_file(body, request.headers.get("content-type", ""))
            expected = _upload_token()
            if not expected or not hmac.compare_digest(token, expected):
                raise RuntimeError("업로드 인증정보가 올바르지 않습니다. 설정 화면을 새로고침한 뒤 다시 시도해 주세요.")

            _set_manual_state(True, "쇼핑몰 과거자료 업로드")
            try:
                message = await asyncio.to_thread(_process_shop_history_upload, filename, payload)
            finally:
                _set_manual_state(False, "")

            return legacy_app.Response(status_code=303, headers={
                "Location": "/settings?msg=" + quote(message), "Cache-Control": "no-store",
            })
        except Exception as exc:
            try:
                from db import set_setting
                set_setting("last_shop_upload_error", str(exc))
            except Exception:
                pass
            _set_manual_state(False, "")
            return legacy_app.Response(status_code=303, headers={
                "Location": "/settings?error=1&msg=" + quote(str(exc)), "Cache-Control": "no-store",
            })
    return await _original_proxy(request, path)


def _start_backend_v220() -> None:
    ok, msg = legacy_app._configured()
    if not ok:
        legacy_app._backend_error = msg
        return

    os.environ["HOST"] = legacy_app.BACKEND_HOST
    os.environ["PORT"] = str(legacy_app.BACKEND_PORT)
    os.environ["G2B_PUBLIC_MODE"] = "0" if legacy_app.TEST_MODE else "1"
    os.environ["G2B_OPEN_BROWSER"] = "0"
    os.environ["G2B_SEED_SAMPLE"] = "0"
    os.environ.setdefault("G2B_COOKIE_SECURE", "1")

    try:
        from sinsung_v200_reset import reset_data_once
        reset_data_once()
        from sinsung_v210_auto import initialize_auto_sync
        initialize_auto_sync()
        from sinsung_v220_stability import initialize_auto_stability
        initialize_auto_stability()

        if str(os.getenv("G2B_PURGE_SAMPLE_DATA", "1")).lower() in ("1", "true", "yes", "on"):
            from db import init_db
            from seed import clear_samples
            init_db()
            clear_samples()

        import server
        server.main(open_browser=False)
    except Exception as exc:
        legacy_app._backend_error = f"내부 대시보드 시작 실패: {exc}"


def _fast_backend_wait(timeout: float = 0.5) -> bool:
    return legacy_app._backend_listening()


legacy_app._background_collect = _background_collect_v220
legacy_app._start_background_collect = _start_background_collect_v220
legacy_app._start_backend = _start_backend_v220
legacy_app._wait_for_backend = _fast_backend_wait
legacy_app._proxy = _proxy_v221
legacy_app.APP_VERSION = APP_VERSION
legacy_app.app.version = APP_VERSION

app = legacy_app.app

__all__ = [
    "app", "APP_VERSION", "SHOP_HISTORY_START", "SHOP_HISTORY_END",
    "_parse_shop_history_file", "_process_shop_history_upload", "_upload_token",
]
