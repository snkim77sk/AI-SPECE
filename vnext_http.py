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
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
SUCCESS_CODES = frozenset({"0", "00", "000", "0000"})
RETRYABLE_SOURCE_CODES = frozenset({"01", "02", "05"})
G2B_ERROR_MESSAGES = {
    "01": "APPLICATION_ERROR",
    "02": "DB_ERROR",
    "03": "NODATA_ERROR",
    "04": "HTTP_ERROR",
    "05": "SERVICETIMEOUT_ERROR",
    "10": "INVALID_REQUEST_PARAMETER_ERROR",
    "11": "NO_MANDATORY_REQUEST_PARAMETERS_ERROR",
    "12": "NO_OPENAPI_SERVICE_ERROR",
    "20": "SERVICE_ACCESS_DENIED_ERROR",
    "21": "TEMPORARILY_DISABLE_THE_SERVICEKEY_ERROR",
    "22": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
    "23": "SERVICE_REQUESTS_EXCEEDS_ERROR",
    "29": "BLACKLIST_IP_ACCESS_ERROR",
    "30": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "31": "DEADLINE_HAS_EXPIRED_ERROR",
    "32": "UNREGISTERED_IP_ERROR",
    "99": "UNKNOWN_ERROR",
}


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


def _record_connection_probe(status, code=""):
    """Persist safe source-connectivity evidence without storing credentials."""
    stamp = dt.datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    try:
        with connect() as conn:
            _setting_upsert(conn, "g2b_api_connection_status", str(status or ""))
            _setting_upsert(conn, "g2b_api_connection_code", str(code or "")[:80])
            _setting_upsert(conn, "g2b_api_connection_at", stamp)
    except Exception:
        # Diagnostics must never break the source request itself.
        pass


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
            conn.execute(
                "UPDATE app_settings SET value='0' "
                "WHERE key LIKE 'vnext!_api!_calls!_%!_count' ESCAPE '!'"
            )
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


def _safe_error_message(code):
    return G2B_ERROR_MESSAGES.get(str(code or "").strip(), "SOURCE_API_ERROR")


def _api_error(code, message=""):
    code = str(code or "").strip()
    safe_message = _safe_error_message(code) if code not in SUCCESS_CODES else str(message or "OK")
    if code == "22":
        return VNextQuotaReached(code, safe_message)
    if code == "23":
        return VNextRateLimited(code, safe_message)
    return VNextApiError(code, safe_message)


def _raise_api_error(code, message=""):
    code = str(code or "").strip()
    if code in SUCCESS_CODES:
        return
    if not code:
        raise VNextResponseError("SCHEMA", "missing result code")
    raise _api_error(code, message)


def _find_header(node):
    if isinstance(node, dict):
        if any(key in node for key in ("resultCode", "resultCd", "returnReasonCode")):
            return node
        for key in (
            "header", "cmmMsgHeader", "OpenAPI_ServiceResponse", "response",
            "nkoneps.com.response.ResponseError", "ResponseError", "responseError",
        ):
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


def _header_code(header):
    if not isinstance(header, dict):
        return ""
    return str(
        header.get("resultCode", header.get("resultCd", header.get("returnReasonCode", "")))
        or ""
    ).strip()


def _extract_source_error(raw):
    """Read a gateway error envelope without ever exposing upstream credential text."""
    text = (
        raw.decode("utf-8-sig", errors="replace")
        if isinstance(raw, (bytes, bytearray))
        else str(raw or "")
    ).strip()
    if not text:
        return None
    try:
        if text.startswith(("{", "[")):
            data = json.loads(text)
            header = _find_header(data)
            code = _header_code(header)
        else:
            root = ET.fromstring(text)
            header = root.find(".//cmmMsgHeader")
            if header is None:
                header = root.find(".//header")
            code = (
                header.findtext("returnReasonCode")
                or header.findtext("resultCode")
                or header.findtext("resultCd")
                or ""
            ).strip() if header is not None else ""
    except (ValueError, ET.ParseError, UnicodeError):
        return None
    if not code or code in SUCCESS_CODES:
        return None
    return _api_error(code)


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
            code = _header_code(header)
            safe_message = "OK" if code in SUCCESS_CODES else _safe_error_message(code)
            _record_result(code, safe_message)
            _raise_api_error(code, safe_message)
            envelope = data.get("response", data) if isinstance(data, dict) else {}
            body = envelope.get("body") if isinstance(envelope, dict) else None
            if code not in SUCCESS_CODES or not isinstance(body, dict):
                raise VNextResponseError("SCHEMA", "missing successful response body")
            return _extract_items(body), parse_count(body.get("totalCount"))
        root = xml_root(text)
        header = root.find("header")
        if header is None:
            header = root.find(".//cmmMsgHeader")
        if header is None:
            raise VNextResponseError("SCHEMA", "unrecognized XML envelope")
        code = (
            header.findtext("resultCode")
            or header.findtext("resultCd")
            or header.findtext("returnReasonCode")
            or ""
        ).strip()
        safe_message = "OK" if code in SUCCESS_CODES else _safe_error_message(code)
        _record_result(code, safe_message)
        _raise_api_error(code, safe_message)
        if root.tag != "response":
            raise VNextResponseError("SCHEMA", "missing successful XML response envelope")
        body = root.find("body")
        items_node = body.find("items") if body is not None else None
        if code not in SUCCESS_CODES or body is None or items_node is None:
            raise VNextResponseError("SCHEMA", "missing successful XML body/items")
        if any(node.tag != "item" for node in list(items_node)):
            raise VNextResponseError("SCHEMA", "invalid XML item container")
        items = [{child.tag: (child.text or "") for child in list(item)} for item in list(items_node)]
        return items, parse_count(body.findtext("totalCount"))
    except (ValueError, ET.ParseError, UnicodeError) as exc:
        raise VNextResponseError("PARSE", type(exc).__name__) from None


def _read_response_limited(response):
    """Read at most MAX_RESPONSE_BYTES while keeping simple test transports compatible."""
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    except TypeError:
        raw = response.read()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise VNextResponseError(
            "SOURCE_RESPONSE_TOO_LARGE",
            "source response exceeded safe size limit",
        )
    return raw


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
                result = parse_response(_read_response_limited(response))
                record_source_transport_success(result[0], result[1])
                _record_connection_probe("OK", "SUCCESS")
                return result
        except VNextQuotaReached:
            _record_connection_probe("BLOCKED", "LOCAL_QUOTA")
            raise
        except VNextRateLimited as exc:
            _record_connection_probe("FAILED", exc.code or "RATE_LIMIT")
            last = exc
            if attempt >= attempts - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
        except VNextApiError as exc:
            _record_connection_probe("FAILED", exc.code or "API_ERROR")
            last = exc
            if exc.code not in RETRYABLE_SOURCE_CODES or attempt >= attempts - 1:
                raise
            time.sleep(1.5 * (2 ** attempt))
        except urllib.error.HTTPError as exc:
            # NO1 parity: inspect the official gateway error envelope so a 403 can
            # distinguish key/permission/IP errors. Never echo the source URL, key,
            # or upstream free-text message.
            try:
                body = exc.read(65536)
            except Exception:
                body = b""
            parsed_error = _extract_source_error(body)
            if parsed_error is not None:
                _record_result(parsed_error.code, parsed_error.message)
                _record_connection_probe("FAILED", parsed_error.code or "SOURCE_ERROR")
                last = parsed_error
            else:
                last = VNextApiError(
                    f"HTTP_{exc.code}", f"HTTP {exc.code} source HTTP failure"
                )
                _record_connection_probe("FAILED", f"HTTP_{exc.code}")
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= attempts - 1:
                raise last from None
            time.sleep(1.5 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            _record_connection_probe("FAILED", "NETWORK")
            last = VNextApiError("NETWORK", type(exc).__name__)
            if attempt >= attempts - 1:
                raise last from None
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"API 요청 실패: {last}")
