"""Independent 지방재정365 HTTP/parser for the G2B vNext RAW path.

This module is intentionally read-only with respect to legacy budget-sync state. It
uses only the API key lookup and performs no sync-log, serving-table, or legacy
settings mutations.

QWGJK remains the detail-business execution/snapshot source. AIDFA is added as a
separate appropriation source so vNext can preserve both layers without changing
the legacy budget serving tables.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from db import connect, get_setting
from vnext_source_guard import (
    current_source_request_context,
    record_source_transport_success,
    require_source_request_context,
)

BASE_ENDPOINT = "https://www.lofin365.go.kr/lf/hub"
SERVICE_CODE = "QWGJK"
APPROPRIATION_SERVICE_CODE = "AIDFA"
ENDPOINT = f"{BASE_ENDPOINT}/{SERVICE_CODE}"
SOURCE_NAME = "지방재정365 세부사업별 세출현황(QWGJK)"
APPROPRIATION_SOURCE_NAME = "지방재정365 구조별 기능별 세출예산(AIDFA)"


class LofinVNextApiError(RuntimeError):
    pass


def _num(value, default=0):
    try:
        return int(round(float(str(value or 0).replace(",", "").strip())))
    except Exception:
        return default


def get_lofin_key():
    return (os.getenv("LOFIN_API_KEY") or get_setting("lofin_api_key", "") or "").strip()


def _result_from_json(data, service_code=SERVICE_CODE):
    from vnext_response import parse_count
    service_code = str(service_code or SERVICE_CODE)
    if not isinstance(data, dict):
        raise LofinVNextApiError("SCHEMA: expected object")
    if service_code not in data:
        result = data.get("RESULT")
        if isinstance(result, list) and len(result) == 1:
            result = result[0]
        if isinstance(result, dict):
            code = str(result.get("CODE", ""))
            if code == "INFO-200":
                return [], 0, code, str(result.get("MESSAGE", ""))
            raise LofinVNextApiError("API: " + code)
        raise LofinVNextApiError(f"SCHEMA: missing {service_code} envelope")
    root = data[service_code]
    entries = root if isinstance(root, list) else [root]
    if not entries or any(not isinstance(x, dict) for x in entries):
        raise LofinVNextApiError(f"SCHEMA: malformed {service_code} entries")
    rows, totals, results = [], [], []
    row_seen = False
    for entry in entries:
        head = entry.get("head", [])
        heads = head if isinstance(head, list) else [head]
        for h in heads:
            if not isinstance(h, dict):
                raise LofinVNextApiError("SCHEMA: malformed head")
            if "list_total_count" in h:
                totals.append(parse_count(h["list_total_count"]))
            if "RESULT" in h:
                result = h["RESULT"]
                if not isinstance(result, dict):
                    raise LofinVNextApiError("SCHEMA: malformed RESULT")
                results.append(result)
        if "row" in entry:
            row_seen = True
            part = entry["row"]
            if part in (None, ""):
                part = []
            if isinstance(part, dict):
                part = [part]
            if not isinstance(part, list) or any(not isinstance(x, dict) for x in part):
                raise LofinVNextApiError("SCHEMA: malformed row; refusing silent row loss")
            rows.extend(part)
    if not results:
        raise LofinVNextApiError("SCHEMA: missing result status")
    codes = {str(r.get("CODE", "")) for r in results}
    if len(codes) != 1 or not codes.issubset({"INFO-000", "INFO-200"}):
        raise LofinVNextApiError("API: " + ",".join(sorted(codes)))
    known = {n for n in totals if n is not None}
    if len(known) > 1:
        raise LofinVNextApiError("SCHEMA: conflicting totals")
    total = next(iter(known), None)
    code = next(iter(codes))
    if code == "INFO-200":
        if rows or (total is not None and total > 0):
            raise LofinVNextApiError("SCHEMA: no-data status contradicts rows/total")
        return [], 0, code, str(results[0].get("MESSAGE", ""))
    if not row_seen:
        raise LofinVNextApiError("SCHEMA: missing row container")
    return rows, total, code, str(results[0].get("MESSAGE", ""))


def _result_from_xml(raw, service_code=SERVICE_CODE):
    from vnext_response import xml_root
    service_code = str(service_code or SERVICE_CODE)
    text = raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else str(raw)
    root = xml_root(text)
    if root.tag == "RESULT":
        return _result_from_json({"RESULT": {x.tag: x.text or "" for x in root}}, service_code)
    if root.tag != service_code:
        raise LofinVNextApiError("SCHEMA: unrecognized XML envelope")
    head = root.find("head")
    if head is None:
        raise LofinVNextApiError("SCHEMA: missing XML head")
    headers = []
    for child in head:
        if child.tag == "RESULT":
            headers.append({"RESULT": {x.tag: x.text or "" for x in child}})
        elif child.tag == "list_total_count":
            headers.append({"list_total_count": child.text})
    rows = [{child.tag: child.text or "" for child in node} for node in root.findall("row")]
    return _result_from_json({service_code: [{"head": headers, "row": rows}]}, service_code)


def parse_response(raw, service_code=SERVICE_CODE):
    try:
        text = raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
        text = text.strip()
        if not text:
            raise LofinVNextApiError("EMPTY_BODY")
        if text.startswith(("{", "[")):
            return _result_from_json(json.loads(text), service_code)
        return _result_from_xml(text, service_code)
    except (ValueError, ET.ParseError, UnicodeError) as exc:
        raise LofinVNextApiError("PARSE: " + type(exc).__name__) from None


def _quota_take():
    import datetime as dt
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
    limit = max(1, int(os.getenv('LOFIN_VNEXT_API_DAILY_LIMIT', '100')))
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        values = {r['key']: r['value'] for r in conn.execute("SELECT key,value FROM app_settings WHERE key IN ('lofin_vnext_calls_date','lofin_vnext_calls_count')")}
        count = int(values.get('lofin_vnext_calls_count', '0')) if values.get('lofin_vnext_calls_date') == today else 0
        if count >= limit:
            raise LofinVNextApiError('LOCAL_DAILY_QUOTA_REACHED')
        conn.executemany("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         [('lofin_vnext_calls_date', today), ('lofin_vnext_calls_count', str(count + 1))])
    return count + 1


def _request(params, retries=3, timeout=45, *, service_code=SERVICE_CODE):
    service_code = str(service_code or SERVICE_CODE)
    url = f"{BASE_ENDPOINT}/{service_code}?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(max(1, int(retries))):
        # Consume an explicit execution-context permit before quota or network I/O.
        require_source_request_context()
        _quota_take()
        req = urllib.request.Request(url, headers={"User-Agent": "G2B-vNext-LOFIN/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                parsed = parse_response(response.read(), service_code=service_code)
                # QWGJK records success at budget_vnext.fetch_page for backward
                # compatibility. New LOFIN services attest here at the audited
                # low-level transport boundary.
                if service_code != SERVICE_CODE and current_source_request_context() is not None:
                    record_source_transport_success(parsed[0], parsed[1])
                return parsed
        except LofinVNextApiError:
            raise
        except urllib.error.HTTPError as exc:
            last = 'HTTP_' + str(exc.code)
            if (exc.code != 429 and exc.code < 500) or attempt + 1 >= max(1, int(retries)):
                raise LofinVNextApiError(last) from None
            time.sleep(1.2 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
            last = type(exc).__name__
            if attempt + 1 >= max(1, int(retries)):
                break
            time.sleep(1.2 * (2 ** attempt))
    raise LofinVNextApiError(f"지방재정365 API 연결 실패: {last}")


def fetch_budget_page(fiscal_year, snapshot_date, keyword="", page=1, size=1000, *,
                      region_code="", retries=3):
    """Fetch one raw QWGJK page without mutating legacy budget-sync state.

    region_code maps to the documented wa_laf_cd query parameter. Leaving it
    blank preserves the existing nationwide query behaviour.
    """
    key = get_lofin_key()
    if not key:
        raise LofinVNextApiError("지방재정365 API 인증키가 설정되지 않았습니다.")
    import datetime as dt
    day = dt.date.fromisoformat(str(snapshot_date))
    year = int(fiscal_year)
    if day.year != year:
        raise ValueError("fiscal year and snapshot year must match")
    digits = day.strftime("%Y%m%d")
    params = {
        "Key": key,
        "Type": "json",
        "pIndex": int(page),
        "pSize": min(max(int(size), 1), 1000),
        "fyr": int(fiscal_year),
        "exe_ymd": digits,
        "dbiz_nm": str(keyword or "").strip(),
    }
    region = str(region_code or "").strip()
    if region:
        params["wa_laf_cd"] = region
    return _request(params, retries=retries)


def fetch_appropriation_page(fiscal_year, region_code="", page=1, size=1000, *, retries=3):
    """Fetch one raw AIDFA appropriation page without a business-name filter.

    ``region_code`` is optional so callers can collect a whole fiscal year or split
    the same source by wide-area code for operationally safer bounded collection.
    """
    key = get_lofin_key()
    if not key:
        raise LofinVNextApiError("지방재정365 API 인증키가 설정되지 않았습니다.")
    year = int(fiscal_year)
    params = {
        "Key": key,
        "Type": "json",
        "pIndex": int(page),
        "pSize": min(max(int(size), 1), 1000),
        "fyr": year,
    }
    region = str(region_code or "").strip()
    if region:
        params["wa_laf_cd"] = region
    return _request(params, retries=retries, service_code=APPROPRIATION_SERVICE_CODE)
