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
from zoneinfo import ZoneInfo

from db import connect, get_setting
from vnext_source_guard import record_source_transport_success, require_source_request_context

USER_AGENT = "AI-SPECE-G2B-VNEXT/1.0"


class VNextApiError(RuntimeError):
    def __init__(self, code="", message=""):
        self.code = str(code or "")
        self.message = str(message or "")
        super().__init__(f"API 오류 {self.code}: {self.message}".strip())


class VNextResponseError(VNextApiError):
    """Backward-compatible invalid-response error; still a VNextApiError."""
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
    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    limit = _daily_limit()
    kind_key = _safe_kind(kind)
    with connect() as conn:
        # Serialize read-modify-write quota reservations across threads/processes.
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
        if not same_day:
            conn.execute("UPDATE app_settings SET value='0' WHERE key GLOB 'vnext_api_calls_*_count'")
        total += 1
        per_kind += 1
        _setting_upsert(conn, "vnext_api_calls_date", today)
        _setting_upsert(conn, "vnext_api_calls_total", total)
        _setting_upsert(conn, f"vnext_api_calls_{kind_key}_count", per_kind)
    return total, limit


def api_usage(kind=None):
    today = dt.datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    with connect() as conn:
        date_row = conn.execute(
            "SELECT value FROM app_settings WHERE key='vnext_api_calls_date'"
        ).fetchone()
        same_day = bool(date_row and str(date_row["value"]) == today)
        total_row = conn.execute(
            "SELECT value FROM app_settings WHERE key='vnext_api_calls_total'"
        ).fetchone()
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
    if not isinstance(body, dict) or "items" not in body:
        raise VNextResponseError("SCHEMA", "missing items container")
    items = body["items"]
    if items in (None, ""):
        return []
    if isinstance(items, dict):
        if not items:
            return []
        items = items.get("item", items)
    if items in (None, ""):
        return []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
        raise VNextResponseError("SCHEMA", "invalid item shape; refusing silent row loss")
    return items


def parse_response(raw):
    """Only recognized successful envelopes may represent an empty source.

    Missing totalCount stays None. Explicit zero is retained for source diagnostics;
    the paging layer treats zero with nonempty rows as an unknown source total.
    """
    from vnext_response import parse_count, xml_root
    text = (raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else str(raw or "")).strip()
    if not text:
        raise VNextResponseError("EMPTY_BODY", "empty response body")
    try:
        if text.startswith(("{", "[")):
            data = json.loads(text)
            header = _find_header(data)
            if not isinstance(header, dict):
                raise VNextResponseError("SCHEMA", "missing result header")
            code = str(header.get("resultCode", header.get("resultCd", "")))
            message = str(header.get("resultMsg", header.get("resultMessage", "")))
            _record_result(code, message)
            _raise_api_error(code, message)
            envelope = data.get("response", data) if isinstance(data, dict) else {}
            body = envelope.get("body") if isinstance(envelope, dict) else None
            if code not in ("0", "00") or not isinstance(body, dict):
                raise VNextResponseError("SCHEMA", "missing successful response body")
            return _extract_items(body), parse_count(body.get("totalCount"))
        root = xml_root(text)
        header = root.find("header")
        if root.tag != "response" or header is None:
            code = root.findtext(".//returnReasonCode") or root.findtext(".//resultCode")
            if code:
                _raise_api_error(code, "source error envelope")
            raise VNextResponseError("SCHEMA", "unrecognized XML envelope")
        code = header.findtext("resultCode") or header.findtext("resultCd") or ""
        message = header.findtext("resultMsg") or header.findtext("resultMessage") or ""
        _record_result(code, message)
        _raise_api_error(code, message)
        body = root.find("body")
        items_node = body.find("items") if body is not None else None
        if code not in ("0", "00") or body is None or items_node is None:
            raise VNextResponseError("SCHEMA", "missing successful XML body/items")
        if any(node.tag != "item" for node in list(items_node)):
            raise VNextResponseError("SCHEMA", "invalid XML item container")
        items = [{child.tag: (child.text or "") for child in list(item)} for item in list(items_node)]
        return items, parse_count(body.findtext("totalCount"))
    except (ValueError, ET.ParseError, UnicodeError) as exc:
        raise VNextResponseError("PARSE", type(exc).__name__) from None


def request(url, kind, timeout=45, retries=3):
    """Perform a namespaced vNext request with bounded retries and quota accounting."""
    last = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        # Scope authorization is checked before quota reservation or network I/O.
        require_source_request_context(g2b_url=url)
        _quota_take(kind)
        req = urllib.request.Request(str(url), headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = parse_response(response.read())
                record_source_transport_success(result[0], result[1])
                return result
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
            # An HTTP failure must never be converted into ([], 0), even if its
            # body happens to look like a successful API response. Never echo URL/key.
            last = VNextApiError(f"HTTP_{exc.code}", f"HTTP {exc.code} source HTTP failure")
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= attempts - 1:
                raise last from None
            time.sleep(1.5 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            last = VNextApiError("NETWORK", type(exc).__name__)
            if attempt >= attempts - 1:
                raise last from None
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"API 요청 실패: {last}")
