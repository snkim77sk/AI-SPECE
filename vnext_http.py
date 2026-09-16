"""Independent HTTP/response layer for G2B vNext collectors.

The legacy production collector has its own request counters and last-result settings.
vNext must never mutate those keys, so this module owns a namespaced quota and parser
state while sharing only the runtime service key through ``db.get_service_key``.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from db import connect, get_setting

USER_AGENT = "AI-SPECE-G2B-VNEXT/1.0"


class VNextApiError(RuntimeError):
    def __init__(self, code="", message=""):
        self.code = str(code or "")
        self.message = str(message or "")
        super().__init__(f"API 오류 {self.code}: {self.message}".strip())


class VNextResponseError(VNextApiError):
    pass


class VNextQuotaReached(VNextApiError):
    pass


class VNextRateLimited(VNextApiError):
    pass


def _num(value, default=0):
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return default


def _safe_kind(kind):
    text = "".join(ch if ch.isalnum() else "_" for ch in str(kind or "api").lower()).strip("_")
    return text or "api"


def _daily_limit():
    raw = str(os.getenv("G2B_VNEXT_API_DAILY_LIMIT", "") or "").strip()
    if not raw:
        raw = str(get_setting("api_daily_limit", "900") or "900").strip()
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return 900


def _setting_upsert(conn, key, value):
    conn.execute(
        "INSERT INTO app_settings(key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(key), str(value)),
    )


def _quota_take(kind):
    """Atomically reserve one vNext request without touching legacy quota keys."""
    today = dt.date.today().isoformat()
    limit = _daily_limit()
    kind_key = _safe_kind(kind)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = {
            str(row["key"]): str(row["value"])
            for row in conn.execute(
                "SELECT key,value FROM app_settings WHERE key IN (?,?,?)",
                ("vnext_api_calls_date", "vnext_api_calls_total", f"vnext_api_calls_{kind_key}_count"),
            ).fetchall()
        }
        same_day = rows.get("vnext_api_calls_date", "") == today
        total = int(float(rows.get("vnext_api_calls_total", "0") or 0)) if same_day else 0
        per_kind = int(float(rows.get(f"vnext_api_calls_{kind_key}_count", "0") or 0)) if same_day else 0
        if total >= limit:
            raise VNextQuotaReached("22", f"VNEXT API 일일 안전한도 {limit:,}회 도달")
        total += 1
        per_kind += 1
        _setting_upsert(conn, "vnext_api_calls_date", today)
        _setting_upsert(conn, "vnext_api_calls_total", total)
        _setting_upsert(conn, f"vnext_api_calls_{kind_key}_count", per_kind)
    return total, limit


def api_usage(kind=None):
    today = dt.date.today().isoformat()
    with connect() as conn:
        date_row = conn.execute("SELECT value FROM app_settings WHERE key='vnext_api_calls_date'").fetchone()
        same_day = bool(date_row and str(date_row["value"]) == today)
        total_row = conn.execute("SELECT value FROM app_settings WHERE key='vnext_api_calls_total'").fetchone()
        total = int(float(total_row["value"] or 0)) if same_day and total_row else 0
        result = {"date": today, "total": total, "limit": _daily_limit()}
        if kind is not None:
            key = f"vnext_api_calls_{_safe_kind(kind)}_count"
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            result["kind"] = _safe_kind(kind)
            result["kind_count"] = int(float(row["value"] or 0)) if same_day and row else 0
        return result


def _record_result(code, message):
    with connect() as conn:
        _setting_upsert(conn, "vnext_last_api_result_code", str(code or ""))
        _setting_upsert(conn, "vnext_last_api_result_message", str(message or "")[:1000])


def _invalid_response(message):
    _record_result("INVALID_RESPONSE", message)
    raise VNextResponseError("INVALID_RESPONSE", message)


def _raise_api_error(code, message):
    code = str(code or "")
    message = str(message or "")
    if code in ("", "0", "00"):
        return
    if code == "22":
        raise VNextQuotaReached(code, message)
    if code == "23":
        raise VNextRateLimited(code, message)
    raise VNextApiError(code, message)


def _find_header(node):
    if isinstance(node, dict):
        if any(key in node for key in ("resultCode", "resultCd")):
            return node
        for key in ("header", "response", "nkoneps.com.response.ResponseError", "ResponseError", "responseError"):
            if key in node:
                found = _find_header(node.get(key))
                if found:
                    return found
        for value in node.values():
            found = _find_header(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_header(value)
            if found:
                return found
    return None


def _find_body(node):
    if isinstance(node, dict):
        body = node.get("body")
        if isinstance(body, dict):
            return body
        response = node.get("response")
        if isinstance(response, dict) and isinstance(response.get("body"), dict):
            return response["body"]
        if "items" in node or "totalCount" in node:
            return node
        for value in node.values():
            found = _find_body(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_body(value)
            if found:
                return found
    return None


def _extract_items(body):
    if not isinstance(body, dict):
        return []
    items = body.get("items", [])
    if items in (None, ""):
        return []
    if isinstance(items, dict):
        items = items.get("item", items)
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        _invalid_response("G2B body.items 형식이 list/dict가 아닙니다.")
    return [item for item in items if isinstance(item, dict)]


def _extract_total(body):
    if not isinstance(body, dict) or "totalCount" not in body:
        return 0
    value = body.get("totalCount")
    if value in (None, ""):
        return 0
    total = int(_num(value, -1))
    if total < 0:
        _invalid_response("G2B totalCount 값이 숫자가 아닙니다.")
    return total


def parse_response(raw):
    raw = bytes(raw or b"").strip()
    if not raw:
        _invalid_response("API가 빈 응답을 반환했습니다.")
    if raw.startswith((b"{", b"[")):
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _invalid_response(f"G2B JSON 파싱 실패: {exc}")
        header = _find_header(data)
        body = _find_body(data)
        if header:
            code = str(header.get("resultCode", header.get("resultCd", "")))
            message = str(header.get("resultMsg", header.get("resultMessage", "")))
            _record_result(code or "00", message)
            _raise_api_error(code, message)
        if not isinstance(body, dict):
            _invalid_response("G2B 응답에 인식 가능한 body/items/totalCount가 없습니다.")
        items = _extract_items(body)
        total = _extract_total(body)
        if not header:
            _record_result("00", "headerless recognized body")
        return items, total

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        _invalid_response(f"G2B XML 파싱 실패: {exc}")
    code_node = root.find(".//resultCode") or root.find(".//resultCd")
    code = (code_node.text or "").strip() if code_node is not None else ""
    message = root.findtext(".//resultMsg") or root.findtext(".//resultMessage") or ""
    items = [{child.tag: (child.text or "") for child in list(item)} for item in root.findall(".//item")]
    total_node = root.find(".//totalCount")
    recognized = code_node is not None or total_node is not None or bool(items) or root.find(".//items") is not None
    if not recognized:
        _invalid_response("G2B XML 응답이 공식 response 구조가 아닙니다.")
    _record_result(code or "00", message)
    _raise_api_error(code, message)
    total = 0
    if total_node is not None and (total_node.text or "").strip():
        total = int(_num(total_node.text, -1))
        if total < 0:
            _invalid_response("G2B totalCount 값이 숫자가 아닙니다.")
    return items, total


def request(url, kind, timeout=45, retries=3):
    """Perform a namespaced vNext request with bounded retries and quota accounting."""
    last = None
    attempts = max(1, int(retries))
    retryable_http = (429, 500, 502, 503, 504)
    for attempt in range(attempts):
        _quota_take(kind)
        req = urllib.request.Request(str(url), headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return parse_response(response.read())
        except VNextQuotaReached:
            raise
        except VNextRateLimited as exc:
            last = exc
            if attempt >= attempts - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
        except VNextApiError:
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
                except VNextApiError as api_exc:
                    parsed_error = api_exc
                except Exception as parse_exc:
                    parsed_error = parse_exc
            last = parsed_error or exc
            if exc.code not in retryable_http or attempt >= attempts - 1:
                raise RuntimeError(f"HTTP {exc.code}: {last}") from exc
            time.sleep(1.5 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt >= attempts - 1:
                raise RuntimeError(f"API 네트워크 오류: {exc}") from exc
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"API 요청 실패: {last}")
