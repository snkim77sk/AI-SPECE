"""Fail-closed execution contexts for all vNext external source requests.

Low-level G2B/LOFIN HTTP helpers must never issue network traffic merely because a
collector was imported and called directly. Only explicitly bounded validation
contexts are available here. A wider historical context is intentionally absent
while bulk historical remains HOLD.
"""
from __future__ import annotations

import datetime as dt
import urllib.parse
from contextlib import contextmanager
from contextvars import ContextVar


BOUNDED_CANARY = "BOUNDED_CANARY"
SMALL_VALIDATION = "SMALL_VALIDATION"
# Reserved mode name only. No context manager exists while bulk historical is HOLD.
APPROVED_HISTORICAL = "APPROVED_HISTORICAL"
MAX_BOUNDED_CANARY_REQUESTS = 32
MAX_SMALL_VALIDATION_REQUESTS = 64

_G2B_HOST = "apis.data.go.kr"
_G2B_SMALL_VALIDATION_PATHS = {
    "/1230000/ad/BidPublicInfoService/getBidPblancListInfoThng": ("inqryBgnDt", "inqryEndDt", True),
    "/1230000/ad/BidPublicInfoService/getBidPblancListInfoServc": ("inqryBgnDt", "inqryEndDt", True),
    "/1230000/as/ScsbidInfoService/getOpengResultListInfoServc": ("inqryBgnDt", "inqryEndDt", True),
    "/1230000/as/ScsbidInfoService/getScsbidListSttusServc": ("inqryBgnDt", "inqryEndDt", True),
    "/1230000/ao/CntrctInfoService/getCntrctInfoListServc": ("inqryBgnDt", "inqryEndDt", True),
    "/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList": ("inqryBgnDate", "inqryEndDate", False),
}
_LOFIN_SMALL_VALIDATION_KEYS = frozenset({
    "Key", "Type", "pIndex", "pSize", "fyr", "exe_ymd", "dbiz_nm",
})

_STATE = ContextVar("g2b_vnext_source_request_context", default=None)


class VNextSourceAccessError(RuntimeError):
    pass


def _positive_budget(value, upper):
    budget = int(value)
    if budget < 1 or budget > int(upper):
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_BUDGET_INVALID")
    return budget


def _validation_date(value):
    text = str(value or "").strip()
    if not text:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_DATE_REQUIRED")
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_DATE_INVALID") from None


def _single_query_value(query, key):
    values = query.get(key)
    if not isinstance(values, list) or len(values) != 1:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_QUERY_INVALID")
    return str(values[0])


def _positive_int(value, *, upper=None, code):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise VNextSourceAccessError(code) from None
    if number < 1 or (upper is not None and number > int(upper)):
        raise VNextSourceAccessError(code)
    return number


def _validate_small_validation_g2b_url(url, validation_date):
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_URL_INVALID") from None
    if (
        parsed.scheme != "https"
        or parsed.netloc != _G2B_HOST
        or parsed.fragment
        or parsed.path not in _G2B_SMALL_VALIDATION_PATHS
    ):
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_TARGET_INVALID")

    start_key, end_key, requires_inqry_div = _G2B_SMALL_VALIDATION_PATHS[parsed.path]
    allowed = {"serviceKey", "pageNo", "numOfRows", "type", start_key, end_key}
    if requires_inqry_div:
        allowed.add("inqryDiv")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    if set(query) != allowed:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_QUERY_INVALID")
    if not _single_query_value(query, "serviceKey"):
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_QUERY_INVALID")
    if _single_query_value(query, "type").lower() != "json":
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_QUERY_INVALID")
    if requires_inqry_div and _single_query_value(query, "inqryDiv") != "1":
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_QUERY_INVALID")
    _positive_int(
        _single_query_value(query, "pageNo"), code="VNEXT_SMALL_VALIDATION_G2B_PAGE_INVALID"
    )
    _positive_int(
        _single_query_value(query, "numOfRows"), upper=999,
        code="VNEXT_SMALL_VALIDATION_G2B_PAGE_INVALID",
    )

    digits = validation_date.replace("-", "")
    expected_start = digits + "0000" if start_key == "inqryBgnDt" else digits
    expected_end = digits + "2359" if end_key == "inqryEndDt" else digits
    if (
        _single_query_value(query, start_key) != expected_start
        or _single_query_value(query, end_key) != expected_end
    ):
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_G2B_DATE_SCOPE_MISMATCH")


def _validate_small_validation_lofin_params(params, validation_date):
    if not isinstance(params, dict) or set(params) != _LOFIN_SMALL_VALIDATION_KEYS:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_LOFIN_QUERY_INVALID")
    if not str(params.get("Key") or "").strip():
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_LOFIN_QUERY_INVALID")
    if str(params.get("Type") or "").lower() != "json":
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_LOFIN_QUERY_INVALID")
    if str(params.get("dbiz_nm") or "").strip():
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_LOFIN_PREFILTER_FORBIDDEN")
    _positive_int(
        params.get("pIndex"), code="VNEXT_SMALL_VALIDATION_LOFIN_PAGE_INVALID"
    )
    _positive_int(
        params.get("pSize"), upper=1000, code="VNEXT_SMALL_VALIDATION_LOFIN_PAGE_INVALID"
    )
    day = dt.date.fromisoformat(validation_date)
    try:
        fiscal_year = int(params.get("fyr"))
    except (TypeError, ValueError):
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_LOFIN_DATE_SCOPE_MISMATCH") from None
    if fiscal_year != day.year or str(params.get("exe_ymd") or "") != day.strftime("%Y%m%d"):
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_LOFIN_DATE_SCOPE_MISMATCH")


def current_source_request_context():
    state = _STATE.get()
    if not state:
        return None
    mode, limit, used, source_sha, validation_date = state
    return {
        "mode": mode,
        "request_limit": limit,
        "requests_used": used,
        "source_commit_sha": source_sha,
        "validation_date_kst": validation_date,
    }


def require_source_request_mode(expected_mode):
    """Require a specific active execution mode without consuming request budget."""
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    mode, _, _, _, _ = state
    if str(mode) != str(expected_mode):
        raise VNextSourceAccessError(
            f"VNEXT_SOURCE_REQUEST_MODE_MISMATCH:{mode}->{expected_mode}"
        )
    return str(mode)


def require_source_request_context(*, g2b_url=None, lofin_params=None):
    """Authorize and consume one HTTP-attempt permit before quota/network I/O.

    SMALL_VALIDATION is not merely count-bounded: every low-level request must also
    prove it belongs to the context's exact completed KST validation date. This closes
    direct-HTTP and direct-collector routes that could otherwise reuse a one-day
    context for a wider source query.
    """
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    mode, limit, used, source_sha, validation_date = state
    if mode == SMALL_VALIDATION:
        supplied = int(g2b_url is not None) + int(lofin_params is not None)
        if supplied != 1:
            raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_REQUEST_SCOPE_REQUIRED")
        if g2b_url is not None:
            _validate_small_validation_g2b_url(g2b_url, validation_date)
        else:
            _validate_small_validation_lofin_params(lofin_params, validation_date)
    if used >= limit:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED")
    _STATE.set((mode, limit, used + 1, source_sha, validation_date))
    return mode


@contextmanager
def _activate(mode, max_requests, source_sha, validation_date=""):
    if _STATE.get() is not None:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_NESTED")
    token = _STATE.set((str(mode), int(max_requests), 0, str(source_sha or ""), str(validation_date or "")))
    try:
        yield current_source_request_context()
    finally:
        _STATE.reset(token)


@contextmanager
def bounded_canary_source_context(*, max_requests=19):
    """Allow only the hard-bounded pre-approval canary source probes."""
    from vnext_live_gate import runtime_source_sha

    source_sha = runtime_source_sha()
    if not source_sha:
        raise VNextSourceAccessError("CANARY_RUNTIME_SOURCE_SHA_REQUIRED")
    budget = _positive_budget(max_requests, MAX_BOUNDED_CANARY_REQUESTS)
    with _activate(BOUNDED_CANARY, budget, source_sha) as state:
        yield state


@contextmanager
def small_validation_source_context(canary_approval, *, validation_date, max_requests=40):
    """Allow only recent one-day validation after a valid same-commit canary."""
    from vnext_live_gate import require_canary_approval, runtime_source_sha

    day = _validation_date(validation_date)
    approval = require_canary_approval(canary_approval)
    source_sha = runtime_source_sha()
    if not source_sha or str(approval.get("source_commit_sha") or "") != source_sha:
        raise VNextSourceAccessError("SMALL_VALIDATION_SOURCE_SHA_MISMATCH")
    budget = _positive_budget(max_requests, MAX_SMALL_VALIDATION_REQUESTS)
    with _activate(SMALL_VALIDATION, budget, source_sha, day) as state:
        yield state
