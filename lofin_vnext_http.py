"""Independent 지방재정365 HTTP/parser for the G2B vNext RAW path.

This module is intentionally read-only with respect to legacy budget-sync state. It
uses only the API key lookup and performs no sync-log, serving-table, or legacy
settings mutations.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from db import get_setting

SERVICE_CODE = "QWGJK"
ENDPOINT = f"https://www.lofin365.go.kr/lf/hub/{SERVICE_CODE}"
SOURCE_NAME = "지방재정365 세부사업별 세출현황(QWGJK)"


class LofinVNextApiError(RuntimeError):
    pass


def _num(value, default=0):
    try:
        return int(round(float(str(value or 0).replace(",", "").strip())))
    except Exception:
        return default


def get_lofin_key():
    return (os.getenv("LOFIN_API_KEY") or get_setting("lofin_api_key", "") or "").strip()


def _result_from_json(data):
    if not isinstance(data, dict):
        raise LofinVNextApiError("지방재정365 응답 형식이 올바르지 않습니다.")
    if SERVICE_CODE not in data and "RESULT" in data:
        result = data.get("RESULT")
        if isinstance(result, list) and result:
            result = result[0]
        if isinstance(result, dict):
            code = str(result.get("CODE", ""))
            message = str(result.get("MESSAGE", ""))
            if code == "INFO-200":
                return [], 0, code, message
            raise LofinVNextApiError(f"{code}: {message}")
    root = data.get(SERVICE_CODE)
    if root is None:
        raise LofinVNextApiError("지방재정365 응답에 QWGJK 데이터가 없습니다.")

    entries = root if isinstance(root, list) else [root]
    total = 0
    rows = []
    code = ""
    message = ""
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        head = entry.get("head")
        heads = head if isinstance(head, list) else ([head] if isinstance(head, dict) else [])
        for h in heads:
            if not isinstance(h, dict):
                continue
            if "list_total_count" in h:
                total = _num(h.get("list_total_count"), total)
            result = h.get("RESULT")
            if isinstance(result, dict):
                code = str(result.get("CODE", code))
                message = str(result.get("MESSAGE", message))
        part = entry.get("row")
        if isinstance(part, list):
            rows.extend(x for x in part if isinstance(x, dict))
        elif isinstance(part, dict):
            rows.append(part)
    if code and code not in ("INFO-000", "INFO-200"):
        raise LofinVNextApiError(f"{code}: {message}")
    if code == "INFO-200":
        return [], total, code, message
    return rows, total or len(rows), code or "INFO-000", message


def _result_from_xml(raw):
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    root = ET.fromstring(text)
    result = root.find(".//RESULT")
    if result is not None:
        code = result.findtext("CODE") or ""
        message = result.findtext("MESSAGE") or ""
        if code not in ("INFO-000", "INFO-200", ""):
            raise LofinVNextApiError(f"{code}: {message}")
        if code == "INFO-200":
            return [], 0, code, message
    rows = [{child.tag: (child.text or "") for child in list(node)} for node in root.findall(".//row")]
    total = _num(root.findtext(".//list_total_count"), len(rows))
    return rows, total, "INFO-000", ""


def parse_response(raw):
    text = raw.decode("utf-8-sig", "replace").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
    if not text:
        raise LofinVNextApiError("지방재정365 API가 빈 응답을 반환했습니다.")
    try:
        return _result_from_json(json.loads(text))
    except json.JSONDecodeError:
        return _result_from_xml(text)


def _request(params, retries=3, timeout=45):
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(max(1, int(retries))):
        req = urllib.request.Request(url, headers={"User-Agent": "G2B-vNext-LOFIN/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return parse_response(response.read())
        except LofinVNextApiError:
            raise
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            last = exc
            if attempt + 1 >= max(1, int(retries)):
                break
            time.sleep(1.2 * (2 ** attempt))
    raise LofinVNextApiError(f"지방재정365 API 연결 실패: {last}")


def fetch_budget_page(fiscal_year, snapshot_date, keyword="", page=1, size=1000):
    """Fetch one raw QWGJK page without mutating legacy budget-sync state."""
    key = get_lofin_key()
    if not key:
        raise LofinVNextApiError("지방재정365 API 인증키가 설정되지 않았습니다.")
    digits = "".join(ch for ch in str(snapshot_date or "") if ch.isdigit())
    if len(digits) != 8:
        raise ValueError("기준일자는 YYYY-MM-DD 형식이어야 합니다.")
    params = {
        "Key": key,
        "Type": "json",
        "pIndex": int(page),
        "pSize": min(max(int(size), 1), 1000),
        "fyr": int(fiscal_year),
        "exe_ymd": digits,
        "dbiz_nm": str(keyword or "").strip(),
    }
    return _request(params)
