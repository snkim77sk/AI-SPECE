"""Fail-closed execution contexts for all vNext external source requests.

Low-level G2B/LOFIN HTTP helpers must never issue network traffic merely because a
collector was imported and called directly. Only explicitly bounded validation
contexts are available here. A wider historical context is intentionally absent
while bulk historical remains HOLD.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar


BOUNDED_CANARY = "BOUNDED_CANARY"
SMALL_VALIDATION = "SMALL_VALIDATION"
# Reserved mode name only. No context manager exists while bulk historical is HOLD.
APPROVED_HISTORICAL = "APPROVED_HISTORICAL"
MAX_BOUNDED_CANARY_REQUESTS = 32
MAX_SMALL_VALIDATION_REQUESTS = 64

_STATE = ContextVar("g2b_vnext_source_request_context", default=None)


class VNextSourceAccessError(RuntimeError):
    pass


def _positive_budget(value, upper):
    budget = int(value)
    if budget < 1 or budget > int(upper):
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_BUDGET_INVALID")
    return budget


def current_source_request_context():
    state = _STATE.get()
    if not state:
        return None
    mode, limit, used, source_sha = state
    return {
        "mode": mode,
        "request_limit": limit,
        "requests_used": used,
        "source_commit_sha": source_sha,
    }


def require_source_request_mode(expected_mode):
    """Require a specific active execution mode without consuming request budget."""
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    mode, _, _, _ = state
    if str(mode) != str(expected_mode):
        raise VNextSourceAccessError(
            f"VNEXT_SOURCE_REQUEST_MODE_MISMATCH:{mode}->{expected_mode}"
        )
    return str(mode)


def require_source_request_context():
    """Consume one HTTP-attempt permit before quota reservation/network I/O."""
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    mode, limit, used, source_sha = state
    if used >= limit:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED")
    _STATE.set((mode, limit, used + 1, source_sha))
    return mode


@contextmanager
def _activate(mode, max_requests, source_sha):
    if _STATE.get() is not None:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_NESTED")
    token = _STATE.set((str(mode), int(max_requests), 0, str(source_sha or "")))
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
def small_validation_source_context(canary_approval, *, max_requests=40):
    """Allow only recent one-day validation after a valid same-commit canary."""
    from vnext_live_gate import require_canary_approval, runtime_source_sha

    approval = require_canary_approval(canary_approval)
    source_sha = runtime_source_sha()
    if not source_sha or str(approval.get("source_commit_sha") or "") != source_sha:
        raise VNextSourceAccessError("SMALL_VALIDATION_SOURCE_SHA_MISMATCH")
    budget = _positive_budget(max_requests, MAX_SMALL_VALIDATION_REQUESTS)
    with _activate(SMALL_VALIDATION, budget, source_sha) as state:
        yield state
