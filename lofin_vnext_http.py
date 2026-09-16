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
    recognized = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        head = entry.get("head")
        heads = head if isinstance(head, list) else ([head] if isinstance(head, dict) else [])
        if heads:
            recognized = True
        for h in heads:
            if not isinstance(h, dict):
                continue
            if "list_total_count" in h:
                recognized = True
                raw_total = h.get("list_total_count")
                if raw_total not in (None, ""):
                    total = _num(raw_total, -1)
                    if total < 0:
                        raise LofinVNextApiError("지방재정365 list_total_count 값이 숫자가 아닙니다.")
            result = h.get("RESULT")
            if isinstance(result, dict):
                recognized = True
                code = str(result.get("CODE", code))
                message = str(result.get("MESSAGE", message))
        if "row" in entry:
            recognized = True
        part = entry.get("row")
        if isinstance(part, list):
            rows.extend(x for x in part if isinstance(x, dict))
        elif isinstance(part, dict):
            rows.append(part)
    if not recognized:
        raise LofinVNextApiError("지방재정365 QWGJK 응답 구조를 인식할 수 없습니다.")
    if code and code not in ("INFO-000", "INFO-200"):
        raise LofinVNextApiError(f"{code}: {message}")
    if code == "INFO-200":
        return [], total, code, message
    return rows, total, code or "INFO-000", message


def _result_from_xml(raw):
    text = raw.decode("utf-8-sig", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise LofinVNextApiError(f"지방재정365 XML 파싱 실패: {exc}") from exc
    result = root.find(".//RESULT")
    code = ""
    message = ""
    if result is not None:
        code = result.findtext("CODE") or ""
        message = result.findtext("MESSAGE") or ""
        if code not in ("INFO-000", "INFO-200", ""):
            raise LofinVNextApiError(f"{code}: {message}")
        if code == "INFO-200":
            return [], 0, code, message
    rows = [{child.tag: (child.text or "") for child in list(node)} for node in root.findall(".//row")]
    total_node = root.find(".//list_total_count")
    recognized = result is not None or total_node is not None or bool(rows) or root.find(".//row") is not None
    if not recognized:
        raise LofinVNextApiError("지방재정365 XML 응답 구조를 인식할 수 없습니다.")
    total = 0
    if total_node is not None and (total_node.text or "").strip():
        total = _num(total_node.text, -1)
        if total < 0:
            raise LofinVNextApiError("지방재정365 list_total_count 값이 숫자가 아닙니다.")
    return rows, total, code or "INFO-000", message


def parse_response(raw):
    text = raw.decode("utf-8-sig", "replace").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
    if not text:
        raise LofinVNextApiError("지방재정365 API가 빈 응답을 반환했습니다.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return _result_from_xml(text)
    return _result_from_json(data)


def _request(params, retries=3, timeout=45):
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    last = None
    retryable_http = (429, 500, 502, 503, 504)
    for attempt in range(max(1, int(retries))):
        req = urllib.request.Request(url, headers={"User-Agent": "G2B-vNext-LOFIN/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return parse_response(response.read())
        except LofinVNextApiError:
            raise
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            parsed_error = None
            if body:
                try:
                    parse_response(body)
                except Exception as parse_exc:
                    parsed_error = parse_exc
            last = parsed_error or exc
            if exc.code not in retryable_http or attempt + 1 >= max(1, int(retries)):
                raise LofinVNextApiError(f"지방재정365 HTTP {exc.code}: {last}") from exc
            time.sleep(1.2 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
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
