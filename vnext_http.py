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
    if not isinstance(body, dict):
        return []
    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", items)
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def parse_response(raw):
    raw = bytes(raw or b"").strip()
    if not raw:
        raise RuntimeError("API가 빈 응답을 반환했습니다.")
    if raw.startswith((b"{", b"[")):
        data = json.loads(raw.decode("utf-8-sig"))
        header = _find_header(data) or {}
        code = str(header.get("resultCode", header.get("resultCd", "00")))
        message = str(header.get("resultMsg", header.get("resultMessage", "")))
        _record_result(code, message)
        _raise_api_error(code, message)
        body = _find_body(data)
        if not isinstance(body, dict):
            return [], 0
        items = _extract_items(body)
        total = int(_num(body.get("totalCount", len(items)), len(items)))
        return items, total

    root = ET.fromstring(raw)
    code = root.findtext(".//resultCode") or root.findtext(".//resultCd") or "00"
    message = root.findtext(".//resultMsg") or root.findtext(".//resultMessage") or ""
    _record_result(code, message)
    _raise_api_error(code, message)
    items = [{child.tag: (child.text or "") for child in list(item)} for item in root.findall(".//item")]
    total = int(_num(root.findtext(".//totalCount"), len(items)))
    return items, total


def request(url, kind, timeout=45, retries=3):
    """Perform a namespaced vNext request with bounded retries and quota accounting."""
    last = None
    for attempt in range(max(1, int(retries))):
        _quota_take(kind)
        req = urllib.request.Request(str(url), headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return parse_response(response.read())
        except VNextQuotaReached:
            raise
        except VNextRateLimited as exc:
            last = exc
            if attempt >= int(retries) - 1:
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
            if body:
                try:
                    return parse_response(body)
                except (VNextQuotaReached, VNextRateLimited, VNextApiError):
                    raise
                except Exception as parsed:
                    last = parsed
            else:
                last = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= int(retries) - 1:
                raise RuntimeError(f"HTTP {exc.code}: {last}") from exc
            time.sleep(1.5 * (2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt >= int(retries) - 1:
                raise RuntimeError(f"API 네트워크 오류: {exc}") from exc
            time.sleep(1.5 * (2 ** attempt))
    raise RuntimeError(f"API 요청 실패: {last}")
