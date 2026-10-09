"""Bounded official 지방재정365 project-detail lookup.

This module is intentionally NOT part of automatic collection.  It is used only
for an explicit authenticated project-detail action and reads one public LOFIN
project detail page.  The page can contain an embedded `var list3 = {...}`
object with the organization fields omitted by the QWGJK OpenAPI.

No HTML or source JSON is persisted.  Callers receive only compact normalized
organization evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from vnext_source_guard import (
    record_source_transport_success,
    require_source_request_context,
)

DETAIL_ENDPOINT = "https://www.lofin365.go.kr/portal/LF3120204.do"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_LIST3_PREFIX = re.compile(r"var\s+list3\s*=\s*", re.I)


class LofinProjectDetailError(RuntimeError):
    pass


def _clean(value, *, limit=500):
    text = " ".join(str(value or "").replace("\x00", "").split()).strip()
    return text[: int(limit)]


def _date_digits(value):
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) != 8:
        raise ValueError("LOFIN_PROJECT_DETAIL_DATE_INVALID")
    return text


def project_detail_url(*, project_code, org_code, fiscal_year, snapshot_date):
    project = _clean(project_code, limit=160)
    org = _clean(org_code, limit=20)
    year = int(fiscal_year)
    day = _date_digits(snapshot_date)
    if not project or len(org) != 7 or not org.isdigit() or int(day[:4]) != year:
        raise ValueError("LOFIN_PROJECT_DETAIL_IDENTITY_INVALID")
    query = urllib.parse.urlencode(
        {
            "dbizCd": project,
            "lafCd": org,
            "fyr": year,
            "inqYmd": day,
        }
    )
    return DETAIL_ENDPOINT + "?" + query


def _bounded_read(response, *, max_bytes=MAX_RESPONSE_BYTES):
    cap = max(1024, min(int(max_bytes), MAX_RESPONSE_BYTES))
    raw = response.read(cap + 1)
    if len(raw) > cap:
        raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_RESPONSE_TOO_LARGE")
    charset = ""
    try:
        charset = str(response.headers.get_content_charset() or "")
    except Exception:
        charset = ""
    for encoding in (charset, "utf-8-sig", "utf-8"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_ENCODING_INVALID")


def _extract_balanced_object(text, start):
    begin = str(text).find("{", int(start))
    if begin < 0:
        raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_LIST3_NOT_FOUND")
    depth = 0
    in_string = False
    escaped = False
    for index in range(begin, len(text)):
        ch = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[begin:index + 1]
            if depth < 0:
                break
    raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_LIST3_MALFORMED")


def parse_project_detail_html(text):
    """Extract compact department evidence from the public detail HTML."""
    body = str(text or "")
    match = _LIST3_PREFIX.search(body)
    if not match:
        raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_LIST3_NOT_FOUND")
    fragment = _extract_balanced_object(body, match.end())
    try:
        data = json.loads(fragment)
    except (TypeError, ValueError):
        raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_LIST3_JSON_INVALID") from None
    if not isinstance(data, dict):
        raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_LIST3_JSON_INVALID")

    source_department = _clean(data.get("deptNm"), limit=300)
    if not source_department:
        raise LofinProjectDetailError("LOFIN_PROJECT_DETAIL_DEPARTMENT_MISSING")
    parts = [_clean(part, limit=160) for part in source_department.split("_")]
    department_name = " > ".join(part for part in parts if part)
    bureau_name = _clean(data.get("slngkNm"), limit=200)
    executor_name = _clean(data.get("enfcSujCn"), limit=300)
    project_name = _clean(data.get("dbizNm"), limit=500)
    digest = hashlib.sha256(
        json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "department_name": department_name or source_department,
        "department_source_name": source_department,
        "bureau_name": bureau_name,
        "executor_name": executor_name,
        "project_name": project_name,
        "source_payload_sha256": digest,
    }


def fetch_project_detail(
    *,
    project_code,
    org_code,
    fiscal_year,
    snapshot_date,
    timeout=15,
):
    """Fetch one exact LOFIN detail page under a one-request source context."""
    url = project_detail_url(
        project_code=project_code,
        org_code=org_code,
        fiscal_year=fiscal_year,
        snapshot_date=snapshot_date,
    )
    require_source_request_context(lofin_detail_url=url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SINSUNG-G2B/4.1 LOFIN-Project-Detail",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(3, min(int(timeout), 20)),
        ) as response:
            final = urllib.parse.urlsplit(str(response.geturl() or url))
            if (
                final.scheme != "https"
                or final.netloc != "www.lofin365.go.kr"
            ):
                raise LofinProjectDetailError(
                    "LOFIN_PROJECT_DETAIL_REDIRECT_INVALID"
                )
            text = _bounded_read(response)
    except LofinProjectDetailError:
        raise
    except urllib.error.HTTPError as exc:
        raise LofinProjectDetailError(
            f"LOFIN_PROJECT_DETAIL_HTTP_{int(exc.code)}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LofinProjectDetailError(
            "LOFIN_PROJECT_DETAIL_CONNECTION_FAILED"
        ) from None

    result = parse_project_detail_html(text)
    record_source_transport_success([result], 1)
    return result
