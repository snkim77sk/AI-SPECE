"""Fail-closed execution contexts for all vNext external source requests.

Low-level G2B/LOFIN HTTP helpers must never issue network traffic merely because a
collector was imported and called directly. Explicitly bounded validation contexts
remain fail-closed. Production additionally permits a shopping-only operational context so delivery
requests can be collected forward from the approved 2026-01-01 boundary without
unlocking the wider historical context, which remains intentionally absent while
bulk historical collection is HOLD.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import urllib.parse
from contextlib import contextmanager
from contextvars import ContextVar
from zoneinfo import ZoneInfo


BOUNDED_CANARY = "BOUNDED_CANARY"
SMALL_VALIDATION = "SMALL_VALIDATION"
OPERATIONAL_RECENT = "OPERATIONAL_RECENT"
OPERATIONAL_BUDGET = "OPERATIONAL_BUDGET"
# Reserved mode name only. No context manager exists while bulk historical is HOLD.
APPROVED_HISTORICAL = "APPROVED_HISTORICAL"
MAX_BOUNDED_CANARY_REQUESTS = 32
MAX_BOUNDED_CANARY_AGE_DAYS = 7
MAX_SMALL_VALIDATION_REQUESTS = 64
MAX_OPERATIONAL_RECENT_REQUESTS = 64
MAX_OPERATIONAL_BUDGET_REQUESTS = 512
MAX_OPERATIONAL_BUDGET_AGE_DAYS = 365
OPERATIONAL_SHOPPING_EARLIEST_DATE = dt.date(2026, 1, 1)

_G2B_HOST = "apis.data.go.kr"
_G2B_SMALL_VALIDATION_PATHS = {
    # G2B 4.x validates only the retained shopping/delivery source. Bid/service,
    # opening, award and contract traffic belongs to NO1 and is not allowlisted.
    "/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList": (
        "inqryBgnDate", "inqryEndDate", True
    ),
}
_LOFIN_SMALL_VALIDATION_KEYS = frozenset({
    "Key", "Type", "pIndex", "pSize", "fyr", "exe_ymd", "dbiz_nm",
})

_STATE = ContextVar("g2b_vnext_source_request_context", default=None)
_OFFICIAL_TRANSPORT_REQUESTS = ContextVar(
    "g2b_vnext_official_transport_requests", default=0
)
_OFFICIAL_TRANSPORT_SUCCESSES = ContextVar(
    "g2b_vnext_official_transport_successes", default=0
)
_LAST_OFFICIAL_TRANSPORT_RESULT_SHA256 = ContextVar(
    "g2b_vnext_last_official_transport_result_sha256", default=""
)


class VNextSourceAccessError(RuntimeError):
    pass


class VNextSourceTransportAttestationError(RuntimeError):
    pass


def _positive_budget(value, upper):
    budget = int(value)
    if budget < 1 or budget > int(upper):
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_BUDGET_INVALID")
    return budget


def _today_kst():
    return dt.datetime.now(ZoneInfo("Asia/Seoul")).date()


def _validation_date(value, *, max_age_days):
    text = str(value or "").strip()
    if not text:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_DATE_REQUIRED")
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_DATE_INVALID") from None
    today = _today_kst()
    if day >= today:
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_DATE_NOT_COMPLETED")
    if day < today - dt.timedelta(days=int(max_age_days)):
        raise VNextSourceAccessError("VNEXT_SMALL_VALIDATION_DATE_TOO_OLD")
    return day.isoformat()


def _operational_collection_date(value):
    text = str(value or "").strip()
    if not text:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_DATE_REQUIRED")
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_DATE_INVALID") from None
    today = _today_kst()
    # The shopping source is complete through D-1; never authorize an open/current day.
    if day >= today:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_DATE_NOT_COMPLETED")
    # This dedicated operational exception is intentionally fixed to the user-approved
    # 2026-01-01 bootstrap boundary rather than a rolling age window.
    if day < OPERATIONAL_SHOPPING_EARLIEST_DATE:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_DATE_BEFORE_BOOTSTRAP")
    return day.isoformat()


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


def _bounded_validation(fn, *args):
    try:
        return fn(*args)
    except VNextSourceAccessError as exc:
        code = str(exc)
        if code.startswith("VNEXT_SMALL_VALIDATION_"):
            code = (
                "VNEXT_BOUNDED_CANARY_"
                + code[len("VNEXT_SMALL_VALIDATION_"):]
            )
        raise VNextSourceAccessError(code) from None


def _validate_operational_recent_g2b_url(url, collection_date):
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_G2B_URL_INVALID") from None
    shopping_path = "/1230000/at/ShoppingMallPrdctInfoService/getDlvrReqDtlInfoList"
    if (
        parsed.scheme != "https"
        or parsed.netloc != _G2B_HOST
        or parsed.fragment
        or parsed.path != shopping_path
    ):
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_G2B_TARGET_INVALID")

    allowed = {
        "serviceKey", "pageNo", "numOfRows", "type", "inqryDiv",
        "inqryBgnDate", "inqryEndDate",
    }
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    if set(query) != allowed:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_G2B_QUERY_INVALID")

    def one(key):
        values = query.get(key)
        if not isinstance(values, list) or len(values) != 1:
            raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_G2B_QUERY_INVALID")
        return str(values[0])

    if (
        not one("serviceKey")
        or one("type").lower() != "json"
        or one("inqryDiv") != "1"
    ):
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_G2B_QUERY_INVALID")
    _positive_int(
        one("pageNo"), code="VNEXT_OPERATIONAL_RECENT_G2B_PAGE_INVALID"
    )
    _positive_int(
        one("numOfRows"), upper=999,
        code="VNEXT_OPERATIONAL_RECENT_G2B_PAGE_INVALID",
    )
    digits = str(collection_date).replace("-", "")
    if one("inqryBgnDate") != digits or one("inqryEndDate") != digits:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_G2B_DATE_SCOPE_MISMATCH")


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


def _validate_operational_budget_lofin_params(params, snapshot_date):
    """Authorize current QWGJK plus current/next-year AIDFA budget pages."""
    if not isinstance(params, dict):
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_REQUEST_SCOPE_REQUIRED")
    values = dict(params)
    service_code = str(values.pop("__service_code", "") or "").strip().upper()
    allowed = {
        "Key", "Type", "pIndex", "pSize", "fyr", "exe_ymd", "dbiz_nm", "wa_laf_cd"
    }
    if set(values) - allowed:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_PARAMETER_NOT_ALLOWED")
    if not str(values.get("Key") or "").strip():
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_KEY_REQUIRED")
    if str(values.get("Type") or "").lower() != "json":
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_TYPE_INVALID")
    _positive_int(
        values.get("pIndex"), upper=1000000,
        code="VNEXT_OPERATIONAL_BUDGET_PAGE_INVALID",
    )
    _positive_int(
        values.get("pSize"), upper=1000,
        code="VNEXT_OPERATIONAL_BUDGET_PAGE_SIZE_INVALID",
    )
    day = dt.date.fromisoformat(str(snapshot_date))
    try:
        fiscal_year = int(values.get("fyr"))
    except (TypeError, ValueError):
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_YEAR_INVALID") from None

    exe = str(values.get("exe_ymd") or "").strip()
    if service_code == "AIDFA":
        if exe or "dbiz_nm" in values:
            raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_AIDFA_SHAPE_INVALID")
        if fiscal_year not in {day.year, day.year + 1}:
            raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_YEAR_MISMATCH")
        return

    # QWGJK remains strictly bound to the current fiscal year and exact snapshot.
    if service_code not in {"", "QWGJK"}:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_SERVICE_NOT_ALLOWED")
    if fiscal_year != day.year:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_YEAR_MISMATCH")
    if not exe or exe != day.strftime("%Y%m%d"):
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_DATE_MISMATCH")
    if str(values.get("dbiz_nm") or "").strip():
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_KEYWORD_MUST_BE_EMPTY")


def _legacy_lofin_params_from_exact_transport_caller(*, include_service_code=False):
    """Read actual params only from the audited LOFIN `_request` call site."""
    try:
        caller = sys._getframe(2)
    except (ValueError, AttributeError):
        return None
    if (
        caller.f_globals.get("__name__") != "lofin_vnext_http"
        or caller.f_code.co_name != "_request"
    ):
        return None
    params = caller.f_locals.get("params")
    if not isinstance(params, dict):
        return None
    result = dict(params)
    if include_service_code:
        result["__service_code"] = str(
            caller.f_locals.get("service_code") or ""
        ).strip().upper()
    return result


def _official_transport_caller():
    """Return True only for the two low-level transports that can issue source I/O."""
    try:
        caller = sys._getframe(2)
    except (ValueError, AttributeError):
        return False
    return (
        caller.f_globals.get("__name__"),
        caller.f_code.co_name,
    ) in {
        ("vnext_http", "request"),
        ("lofin_vnext_http", "_request"),
    }


def _official_success_caller():
    """Allow success recording only at audited source-return boundaries."""
    try:
        caller = sys._getframe(2)
    except (ValueError, AttributeError):
        return False
    return (
        caller.f_globals.get("__name__"),
        caller.f_code.co_name,
    ) in {
        ("vnext_http", "request"),
        ("lofin_vnext_http", "_request"),
        ("budget_vnext", "fetch_page"),
    }


def _runtime_source_identity(mode):
    from vnext_live_gate import runtime_source_sha

    try:
        current = runtime_source_sha()
    except Exception as exc:
        raise VNextSourceAccessError(
            f"VNEXT_SOURCE_REQUEST_RUNTIME_SHA_INVALID:{type(exc).__name__}"
        ) from None
    if current:
        return str(current)
    if str(mode) in {OPERATIONAL_RECENT, OPERATIONAL_BUDGET}:
        # Managed Cafe24 deployments do not always expose a Git SHA. Recent
        # shopping collection remains bounded to one exact day and endpoint, so
        # the checked-in application version is a deterministic operational
        # identity without weakening canary/historical SHA requirements.
        from app_version import APP_VERSION
        return f"VERSION:{APP_VERSION}"
    raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_RUNTIME_SHA_REQUIRED")


def _require_runtime_source_identity(mode, source_identity):
    current = _runtime_source_identity(mode)
    if str(current) != str(source_identity or ""):
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_RUNTIME_SHA_MISMATCH")
    return str(current)


def source_transport_result_sha256(items, reported_total):
    """Canonical digest of the source rows/total returned by an official transport."""
    payload = {
        "items": items,
        "reported_total": reported_total,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def record_source_transport_success(items, reported_total):
    """Record one successfully parsed result from an audited source-return boundary."""
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    mode, _, used, source_sha, _ = state
    _require_runtime_source_identity(mode, source_sha)
    if used < 1 or not _official_success_caller():
        raise VNextSourceAccessError("VNEXT_SOURCE_TRANSPORT_SUCCESS_CALLER_INVALID")
    digest = source_transport_result_sha256(items, reported_total)
    _OFFICIAL_TRANSPORT_SUCCESSES.set(
        int(_OFFICIAL_TRANSPORT_SUCCESSES.get()) + 1
    )
    _LAST_OFFICIAL_TRANSPORT_RESULT_SHA256.set(digest)
    return digest


def current_source_request_context():
    state = _STATE.get()
    if not state:
        return None
    mode, limit, used, source_sha, validation_date = state
    return {
        "mode": mode,
        "request_limit": limit,
        "requests_used": int(_OFFICIAL_TRANSPORT_REQUESTS.get()),
        "transport_successes_used": int(_OFFICIAL_TRANSPORT_SUCCESSES.get()),
        "last_transport_result_sha256": str(
            _LAST_OFFICIAL_TRANSPORT_RESULT_SHA256.get() or ""
        ),
        "permits_used": used,
        "source_commit_sha": source_sha,
        "validation_date_kst": validation_date,
    }


def require_attested_transport_result(before, result, *, error_code):
    """Bind a collector/replay return value to one new successful official transport."""
    if before is None:
        return result
    after = current_source_request_context()
    same_context = bool(
        after
        and after.get("mode") == before.get("mode")
        and after.get("source_commit_sha") == before.get("source_commit_sha")
        and after.get("validation_date_kst") == before.get("validation_date_kst")
    )
    try:
        items, reported_total = result
    except (TypeError, ValueError):
        raise VNextSourceTransportAttestationError(error_code) from None
    expected_digest = source_transport_result_sha256(items, reported_total)
    if (
        not same_context
        or int(after.get("requests_used") or 0) <= int(before.get("requests_used") or 0)
        or int(after.get("permits_used") or 0) <= int(before.get("permits_used") or 0)
        or int(after.get("transport_successes_used") or 0)
        != int(before.get("transport_successes_used") or 0) + 1
        or str(after.get("last_transport_result_sha256") or "") != expected_digest
    ):
        raise VNextSourceTransportAttestationError(error_code)
    return result


def require_source_request_mode(expected_mode):
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    mode, _, _, source_sha, _ = state
    _require_runtime_source_identity(mode, source_sha)
    if str(mode) != str(expected_mode):
        raise VNextSourceAccessError(
            f"VNEXT_SOURCE_REQUEST_MODE_MISMATCH:{mode}->{expected_mode}"
        )
    return str(mode)


def require_source_request_context(*, g2b_url=None, lofin_params=None):
    """Authorize and consume one source-attempt permit before quota/network I/O."""
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    mode, limit, used, source_sha, validation_date = state
    _require_runtime_source_identity(mode, source_sha)
    if mode in {BOUNDED_CANARY, SMALL_VALIDATION}:
        supplied = int(g2b_url is not None) + int(lofin_params is not None)
        if supplied == 0:
            lofin_params = _legacy_lofin_params_from_exact_transport_caller()
            supplied = int(lofin_params is not None)
        if supplied != 1:
            code = (
                "VNEXT_BOUNDED_CANARY_REQUEST_SCOPE_REQUIRED"
                if mode == BOUNDED_CANARY
                else "VNEXT_SMALL_VALIDATION_REQUEST_SCOPE_REQUIRED"
            )
            raise VNextSourceAccessError(code)
        if g2b_url is not None:
            if mode == BOUNDED_CANARY:
                _bounded_validation(
                    _validate_small_validation_g2b_url,
                    g2b_url,
                    validation_date,
                )
            else:
                _validate_small_validation_g2b_url(g2b_url, validation_date)
        else:
            if mode == BOUNDED_CANARY:
                _bounded_validation(
                    _validate_small_validation_lofin_params,
                    lofin_params,
                    validation_date,
                )
            else:
                _validate_small_validation_lofin_params(
                    lofin_params, validation_date
                )
    elif mode == OPERATIONAL_RECENT:
        if g2b_url is None or lofin_params is not None:
            raise VNextSourceAccessError("VNEXT_OPERATIONAL_RECENT_REQUEST_SCOPE_REQUIRED")
        _validate_operational_recent_g2b_url(g2b_url, validation_date)
    elif mode == OPERATIONAL_BUDGET:
        if lofin_params is None and g2b_url is None:
            lofin_params = _legacy_lofin_params_from_exact_transport_caller(
                include_service_code=True
            )
        if lofin_params is None or g2b_url is not None:
            raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_REQUEST_SCOPE_REQUIRED")
        _validate_operational_budget_lofin_params(lofin_params, validation_date)
    if used >= limit:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED")
    official_transport = _official_transport_caller()
    _STATE.set((mode, limit, used + 1, source_sha, validation_date))
    if official_transport:
        _OFFICIAL_TRANSPORT_REQUESTS.set(int(_OFFICIAL_TRANSPORT_REQUESTS.get()) + 1)
    return mode


@contextmanager
def _activate(mode, max_requests, source_sha, validation_date=""):
    if _STATE.get() is not None:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_NESTED")
    token = _STATE.set((str(mode), int(max_requests), 0, str(source_sha or ""), str(validation_date or "")))
    transport_token = _OFFICIAL_TRANSPORT_REQUESTS.set(0)
    success_token = _OFFICIAL_TRANSPORT_SUCCESSES.set(0)
    digest_token = _LAST_OFFICIAL_TRANSPORT_RESULT_SHA256.set("")
    try:
        yield current_source_request_context()
    finally:
        _LAST_OFFICIAL_TRANSPORT_RESULT_SHA256.reset(digest_token)
        _OFFICIAL_TRANSPORT_SUCCESSES.reset(success_token)
        _OFFICIAL_TRANSPORT_REQUESTS.reset(transport_token)
        _STATE.reset(token)


@contextmanager
def bounded_canary_source_context(*, validation_date, max_requests=19):
    from vnext_live_gate import runtime_source_sha

    day = _validation_date(
        validation_date,
        max_age_days=MAX_BOUNDED_CANARY_AGE_DAYS,
    )
    source_sha = runtime_source_sha()
    if not source_sha:
        raise VNextSourceAccessError("CANARY_RUNTIME_SOURCE_SHA_REQUIRED")
    budget = _positive_budget(max_requests, MAX_BOUNDED_CANARY_REQUESTS)
    with _activate(BOUNDED_CANARY, budget, source_sha, day) as state:
        yield state


@contextmanager
def operational_recent_source_context(*, collection_date, max_requests=32):
    day = _operational_collection_date(collection_date)
    source_identity = _runtime_source_identity(OPERATIONAL_RECENT)
    budget = _positive_budget(max_requests, MAX_OPERATIONAL_RECENT_REQUESTS)
    with _activate(OPERATIONAL_RECENT, budget, source_identity, day) as state:
        yield state


@contextmanager
def operational_budget_source_context(*, snapshot_date, max_requests=256):
    day = dt.date.fromisoformat(str(snapshot_date))
    today = _today_kst()
    if day > today:
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_FUTURE_DATE")
    if day < today - dt.timedelta(days=MAX_OPERATIONAL_BUDGET_AGE_DAYS):
        raise VNextSourceAccessError("VNEXT_OPERATIONAL_BUDGET_DATE_TOO_OLD")
    source_identity = _runtime_source_identity(OPERATIONAL_BUDGET)
    budget = _positive_budget(max_requests, MAX_OPERATIONAL_BUDGET_REQUESTS)
    with _activate(OPERATIONAL_BUDGET, budget, source_identity, day.isoformat()) as state:
        yield state


@contextmanager
def small_validation_source_context(canary_approval, *, validation_date, max_requests=40):
    from vnext_live_gate import (
        MAX_SMALL_VALIDATION_AGE_DAYS,
        require_canary_approval,
        runtime_source_sha,
    )

    day = _validation_date(
        validation_date, max_age_days=MAX_SMALL_VALIDATION_AGE_DAYS
    )
    approval = require_canary_approval(canary_approval)
    source_sha = runtime_source_sha()
    if not source_sha or str(approval.get("source_commit_sha") or "") != source_sha:
        raise VNextSourceAccessError("SMALL_VALIDATION_SOURCE_SHA_MISMATCH")
    budget = _positive_budget(max_requests, MAX_SMALL_VALIDATION_REQUESTS)
    with _activate(SMALL_VALIDATION, budget, source_sha, day) as state:
        yield state
