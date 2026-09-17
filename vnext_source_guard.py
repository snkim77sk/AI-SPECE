"""Fail-closed execution contexts for all vNext external source requests.

Low-level G2B/LOFIN HTTP helpers must never issue network traffic merely because a
collector was imported and called directly. Only explicitly bounded validation
contexts are available here. A wider historical context is intentionally absent
while bulk historical remains HOLD.

The request budget is held in a mutable state object. ContextVar copies therefore
share the same counter instead of receiving independent permit balances. Closing the
owner context also marks that shared state inactive so inherited/copy contexts cannot
continue issuing requests after the approved scope exits.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading


BOUNDED_CANARY = "BOUNDED_CANARY"
SMALL_VALIDATION = "SMALL_VALIDATION"
MAX_BOUNDED_CANARY_REQUESTS = 32
MAX_SMALL_VALIDATION_REQUESTS = 64

_STATE = ContextVar("g2b_vnext_source_request_context", default=None)


class VNextSourceAccessError(RuntimeError):
    pass


class _SourceRequestState:
    __slots__ = ("mode", "limit", "used", "source_sha", "active", "lock")

    def __init__(self, mode, limit, source_sha):
        self.mode = str(mode)
        self.limit = int(limit)
        self.used = 0
        self.source_sha = str(source_sha or "")
        self.active = True
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return {
                "mode": self.mode,
                "request_limit": self.limit,
                "requests_used": self.used,
                "source_commit_sha": self.source_sha,
                "active": self.active,
            }

    def consume(self):
        with self.lock:
            if not self.active:
                raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_CLOSED")
            if self.used >= self.limit:
                raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_BUDGET_EXHAUSTED")
            self.used += 1
            return self.mode

    def close(self):
        with self.lock:
            self.active = False


def _positive_budget(value, upper):
    budget = int(value)
    if budget < 1 or budget > int(upper):
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_BUDGET_INVALID")
    return budget


def current_source_request_context():
    state = _STATE.get()
    if not state:
        return None
    return state.snapshot()


def require_source_request_context():
    """Consume one shared HTTP-attempt permit before quota reservation/network I/O."""
    state = _STATE.get()
    if not state:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_REQUIRED")
    return state.consume()


@contextmanager
def _activate(mode, max_requests, source_sha):
    if _STATE.get() is not None:
        raise VNextSourceAccessError("VNEXT_SOURCE_REQUEST_CONTEXT_NESTED")
    state = _SourceRequestState(str(mode), int(max_requests), str(source_sha or ""))
    token = _STATE.set(state)
    try:
        yield state.snapshot()
    finally:
        # Mark the shared object closed before resetting the owner context. Contexts
        # copied while active still hold this object and therefore fail closed.
        state.close()
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
