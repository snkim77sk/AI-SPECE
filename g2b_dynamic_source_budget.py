"""Opt-in 256MiB G2B source admission using measured raw cgroup bytes.

220,000,000 bytes is decimal, NOT 220MiB. Web and isolated source are
charged to the same cgroup, so worker RSS alone is never admission evidence.

The 52MiB start reserve is PROVISIONAL; a 32MiB assumption failed to cover
observed baseline-to-peak in the first disposable synthetic source CI.
Both must be verified by high-pressure disposable
256MiB web+worker/container tests before any production rollout.
"""
from __future__ import annotations

import math
import os
import threading

MIB = 1 << 20
CGROUP_LIMIT_BYTES = 256 * MIB
SOURCE_CEILING_BYTES = 220_000_000
# Low-pressure source CI: parent 74.27MiB, cgroup peak 121.73MiB =>
# ~47.46MiB peak increment. 52MiB is a safety allowance, NOT yet proven
# for raw~173MiB/effective~115MiB. Remain opt-in/OFF until proven.
START_RESERVE_BYTES = 52 * MIB

MIN_STAGE_RESERVES = {
    "worker": 8 * MIB, "request": 4 * MIB,
    "read": 16 * MIB, "parse": 12 * MIB,
    "hash": 8 * MIB, "save": 12 * MIB,
    "classify": 8 * MIB,
}


def enabled(mode=""):
    """Flag inherited by worker; cannot be changed via source request."""
    return bool(
        str(mode or "") in {"shopping", "budget"}
        and str(os.getenv("G2B_256MIB_DYNAMIC_SOURCE_BUDGET", "0")).strip().lower()
        in {"1", "true", "on", "yes"}
    )


def _positive_int(value):
    if isinstance(value, bool):
        return 0
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return 0
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return result if result > 0 else 0


def _probe(snapshot):
    """Return raw bytes; reject estimated, rounded, or stale-format samples."""
    state = snapshot or {}
    source = state.get("source", state.get("cgroup_source", ""))
    limit = _positive_int(state.get("limit_bytes", state.get("cgroup_limit_bytes")))
    raw = _positive_int(state.get("current_bytes", state.get("cgroup_current_bytes")))
    if source != "cgroup-v2" or limit != CGROUP_LIMIT_BYTES or raw <= 0:
        return 0, "CGRP_DYNAMIC_PROBE_UNAVAILABLE"
    if _positive_int(state.get("oom_group", state.get("cgroup_oom_group"))):
        return 0, "CGRP_DYNAMIC_OOM_GROUP"
    if raw > limit:
        return 0, "CGRP_DYNAMIC_PROBE_UNAVAILABLE"
    return raw, ""


def headroom(snapshot, required_bytes, *, phase="worker"):
    """Fail closed BEFORE next allocation, using full raw cgroup memory.current."""
    raw, reason = _probe(snapshot)
    if reason:
        return False, reason, 0
    required = _positive_int(required_bytes)
    if required <= 0:
        return False, "CGRP_DYNAMIC_INVALID_RESERVE", 0
    free = max(0, SOURCE_CEILING_BYTES - raw)
    if required >= free:
        return False, "CGRP_DYNAMIC_" + str(phase).upper() + "_HOLD", free
    return True, "CGRP_DYNAMIC_" + str(phase).upper() + "_OK", free


def admission(snapshot, *, worker_soft_limit_mib=112):
    """Provisional pre-fork reserve; real launch peak remains to be proven."""
    try:
        soft = float(worker_soft_limit_mib)
    except (TypeError, ValueError):
        soft = 0.0
    if not math.isfinite(soft) or soft < 96 or soft > 112:
        return False, "CGRP_DYNAMIC_SOFT_LIMIT_UNSAFE"
    if snapshot.get("guard_ok") is not True or snapshot.get("process_guard_ok") is not True:
        return False, "CGRP_DYNAMIC_PARENT_GUARD_HELD"
    try:
        valid_rss = math.isfinite(float(snapshot.get("rss_mib"))) and float(snapshot.get("rss_mib")) > 0
    except (TypeError, ValueError):
        valid_rss = False
    if not valid_rss:
        return False, "CGRP_DYNAMIC_PARENT_RSS_UNAVAILABLE"
    allowed, reason, _ = headroom(snapshot, START_RESERVE_BYTES, phase="start")
    return allowed, reason


def required_for_phase(phase, *, payload_bytes=0):
    """Bound likely allocation peak; size-sensitive allowance overrides floor."""
    if phase not in MIN_STAGE_RESERVES:
        raise ValueError("UNSUPPORTED_CGROUP_SOURCE_PHASE")
    size = _positive_int(payload_bytes)
    multiple = {"read": 2, "parse": 4, "hash": 2, "save": 2, "classify": 3}.get(phase, 1)
    return max(MIN_STAGE_RESERVES[phase], size * multiple + 4 * MIB)


ALLOCATION_DRIFT_BYTES = 2 * MIB


def allocation_checkpoint(*, reserve_bytes, phase="allocation"):
    """Sample before allocating; retain process protection plus web drift margin."""
    if not enabled("budget") or (
        str(os.getenv("G2B_RUNTIME_ROLE", "")).upper() != "LOCAL_COLLECTOR"
        or os.getenv("G2B_SOURCE_ONLY_RUNTIME") != "1"
        or str(os.getenv("G2B_TEST_MODE", "")).lower() in {"1", "true", "yes", "on"}
    ):
        return None
    import memory_guard
    state = memory_guard.container_budget_snapshot()
    # Only positively verified larger tiers retain their existing policy.
    if (state.get("source") == "cgroup-v2"
            and _positive_int(state.get("limit_bytes")) > CGROUP_LIMIT_BYTES
            and _positive_int(state.get("current_bytes")) > 0):
        return None
    try:
        rss = float(memory_guard.current_rss_mib())
        soft = float(memory_guard.soft_limit_mib())
    except (TypeError, ValueError):
        rss = soft = 0.0
    if not math.isfinite(rss) or not math.isfinite(soft) or rss <= 0 or soft <= 0:
        raise memory_guard.MemoryPressureError("CGRP_DYNAMIC_PROCESS_PROBE_UNAVAILABLE")
    if rss >= soft:
        raise memory_guard.MemoryPressureError("PROCESS_RSS_HOLD")
    reserve = _positive_int(reserve_bytes)
    if not reserve:
        raise memory_guard.MemoryPressureError("CGRP_DYNAMIC_INVALID_RESERVE")
    allowed, reason, _ = headroom(state, reserve + ALLOCATION_DRIFT_BYTES, phase=phase)
    if not allowed:
        raise memory_guard.MemoryPressureError(reason)
    return state


def assert_worker_phase(phase, *, payload_bytes=0):
    return allocation_checkpoint(
        reserve_bytes=required_for_phase(phase, payload_bytes=payload_bytes), phase=phase
    )


def bounded_read(response, max_bytes):
    """Read in 64KiB pieces and resample shared cgroup BEFORE each allocation."""
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("INVALID_SOURCE_RESPONSE_LIMIT")
    body = bytearray()
    chunk_size = 64 * 1024
    while True:
        size = min(chunk_size, max_bytes + 1 - len(body))
        if size <= 0:
            raise ValueError("SOURCE_RESPONSE_TOO_LARGE")
        assert_worker_phase("read", payload_bytes=len(body) + size)
        part = response.read(size)
        if not part:
            break
        if not isinstance(part, bytes):
            raise ValueError("SOURCE_RESPONSE_NOT_BYTES")
        if len(part) > size or len(body) + len(part) > max_bytes:
            raise ValueError("SOURCE_RESPONSE_TOO_LARGE")
        body.extend(part)
    assert_worker_phase("read", payload_bytes=len(body))
    return bytes(body)


def record_estimated_bytes(items):
    """Allocation-free order-of-magnitude estimate for ALREADY resident rows."""
    if not isinstance(items, (list, tuple)):
        return 0
    size = 0
    for row in items:
        size += 512
        if isinstance(row, dict):
            for key, value in row.items():
                size += 64 + (len(key) * 4 if isinstance(key, str) else 128)
                if isinstance(value, str):
                    size += len(value) * 4
                elif isinstance(value, (bytes, bytearray)):
                    size += len(value)
                elif isinstance(value, (list, tuple, dict)):
                    size += 4096
                else:
                    size += 128
        if size >= 64 * MIB:
            return size
    return size


def parent_abort_reason(snapshot):
    """Read web+worker raw usage even when child RSS appears small."""
    raw, reason = _probe(snapshot)
    if reason:
        return reason
    return "CGRP_DYNAMIC_220000000_BYTE_HOLD" if raw >= SOURCE_CEILING_BYTES else ""


# At least 8MiB of real PARENT-BASELINE improvement is needed to re-open
# a source that repeatedly aborts the same durable page without progress.
BASELINE_RECOVERY_BYTES = 8 * MIB


GATE_VERSION = 1
GATE_KEY_PREFIX = "g2b_vnext_dynamic_source_gate_v1:"
GATE_SLOT_KEY = GATE_KEY_PREFIX + "serial_slot"
MAX_PROGRESS_SCOPES = 2048
MAX_GATE_BYTES = 2 * MIB


class GateStateUnavailable(RuntimeError):
    pass


def _encoded(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _valid_progress(proof):
    if not isinstance(proof, dict) or proof.get("version") != GATE_VERSION:
        return False
    scopes = proof.get("scopes")
    if not isinstance(scopes, dict) or len(scopes) > MAX_PROGRESS_SCOPES:
        return False
    for key, values in scopes.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 400:
            return False
        if not isinstance(values, dict) or set(values) != {"generation", "pages", "saved"}:
            return False
        if not isinstance(values["generation"], str) or not 1 <= len(values["generation"]) <= 80:
            return False
        if any(type(values[name]) is not int or values[name] < 0 for name in ("pages", "saved")):
            return False
    return True


def empty_progress():
    return {"version": GATE_VERSION, "scopes": {}}


def _new_record():
    return {
        "version": GATE_VERSION, "consecutive_no_progress": 0,
        "blocked_baseline_bytes": 0, "before_raw_bytes": 0,
        "before_progress": None, "after_progress": empty_progress(),
        "last_outcome": "INITIALIZED", "next_retry_condition": "",
        "run_token": "", "classified_commits": 0,
        "before_classified_commits": 0, "operator_hold": False,
        "operator_retry": False,
    }


def _valid_record(rec):
    if not isinstance(rec, dict) or set(rec) != set(_new_record()):
        return False
    if (rec["version"] != GATE_VERSION or type(rec["operator_hold"]) is not bool
            or type(rec["operator_retry"]) is not bool):
        return False
    for key in ("consecutive_no_progress", "blocked_baseline_bytes", "before_raw_bytes",
                "classified_commits", "before_classified_commits"):
        if type(rec[key]) is not int or rec[key] < 0:
            return False
    if rec["consecutive_no_progress"] > 10:
        return False
    if any(not isinstance(rec[key], str) or len(rec[key]) > 160
           for key in ("last_outcome", "next_retry_condition", "run_token")):
        return False
    return bool(_valid_progress(rec["after_progress"])
                and (rec["before_progress"] is None or _valid_progress(rec["before_progress"]))
                and bool(rec["run_token"]) == (rec["before_progress"] is not None)
                and (not rec["run_token"] or rec["before_raw_bytes"] > 0))


class DatabaseGateStore:
    """Small existing app_settings transactions, no DDL or source I/O.

    Compare-and-swap prevents two web parents overwriting a reservation or a
    committed classification count. Missing/corrupt rows are never repaired by
    an ordinary admission; the first explicit bootstrap is separately marked.
    """
    @staticmethod
    def _key(source):
        if source not in {"shopping", "budget"}:
            raise GateStateUnavailable("NO_PROGRESS_SOURCE_INVALID")
        return GATE_KEY_PREFIX + source

    def initialize(self, source):
        from db import connect
        import json
        key = self._key(source)
        with connect() as conn:
            slot = conn.execute("SELECT value FROM app_settings WHERE key=?", (GATE_SLOT_KEY,)).fetchone()
            if slot is None:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM app_settings WHERE key LIKE ?",
                    (GATE_KEY_PREFIX + "%",),
                ).fetchone()[0]
                if existing:
                    raise GateStateUnavailable("NO_PROGRESS_SERIAL_SLOT_MISSING")
                # All control rows are installed atomically exactly once.
                # Deleting both one source's row and its marker later cannot
                # be mistaken for the first installation of that source.
                for name in ("shopping", "budget"):
                    conn.execute("INSERT INTO app_settings(key,value) VALUES (?,?)",
                                 (self._key(name), _encoded(_new_record())))
                    conn.execute("INSERT INTO app_settings(key,value) VALUES (?,?)",
                                 (self._key(name) + ":initialized", "1"))
                conn.execute("INSERT INTO app_settings(key,value) VALUES (?,?)", (GATE_SLOT_KEY, ""))
            marker = conn.execute("SELECT value FROM app_settings WHERE key=?", (key + ":initialized",)).fetchone()
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            if marker is not None:
                if marker[0] != "1" or row is None:
                    raise GateStateUnavailable("NO_PROGRESS_STATE_MISSING")
                value = str(row[0])
                if len(value) > MAX_GATE_BYTES or not _valid_record(json.loads(value)):
                    raise GateStateUnavailable("NO_PROGRESS_STATE_CORRUPT")
                return
            raise GateStateUnavailable("NO_PROGRESS_STATE_UNMARKED")

    def transact(self, source, change, *, require_idle=False):
        from db import connect
        import json
        key = self._key(source)
        # A losing concurrent CAS is a HOLD, never an implicit retry/overwrite.
        with connect() as conn:
            marker = conn.execute("SELECT value FROM app_settings WHERE key=?", (key + ":initialized",)).fetchone()
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            if marker is None or marker[0] != "1" or row is None:
                raise GateStateUnavailable("NO_PROGRESS_STATE_MISSING")
            old = str(row[0])
            if len(old) > MAX_GATE_BYTES:
                raise GateStateUnavailable("NO_PROGRESS_STATE_CORRUPT")
            try:
                rec = json.loads(old)
            except (ValueError, TypeError):
                raise GateStateUnavailable("NO_PROGRESS_STATE_CORRUPT") from None
            if not _valid_record(rec):
                raise GateStateUnavailable("NO_PROGRESS_STATE_CORRUPT")
            slot_row = conn.execute("SELECT value FROM app_settings WHERE key=?", (GATE_SLOT_KEY,)).fetchone()
            if slot_row is None:
                raise GateStateUnavailable("NO_PROGRESS_SERIAL_SLOT_MISSING")
            slot = str(slot_row[0])
            if slot:
                owner, _, token = slot.partition(":")
                if owner not in {"shopping", "budget"} or len(token) != 32:
                    raise GateStateUnavailable("NO_PROGRESS_SERIAL_SLOT_CORRUPT")
                owner_row = conn.execute("SELECT value FROM app_settings WHERE key=?", (self._key(owner),)).fetchone()
                owner_text = str(owner_row[0]) if owner_row else ""
                if not owner_text or len(owner_text) > MAX_GATE_BYTES:
                    raise GateStateUnavailable("NO_PROGRESS_SERIAL_SLOT_CORRUPT")
                owner_rec = json.loads(owner_text)
                if not _valid_record(owner_rec) or owner_rec["run_token"] != token:
                    raise GateStateUnavailable("NO_PROGRESS_SERIAL_SLOT_CORRUPT")
                if require_idle and owner != source:
                    raise GateStateUnavailable("NO_PROGRESS_OTHER_SOURCE_REAP_UNCONFIRMED")
            old_token = rec["run_token"]
            if old_token and slot != source + ":" + old_token:
                raise GateStateUnavailable("NO_PROGRESS_SERIAL_SLOT_CORRUPT")
            result = change(rec)
            new = _encoded(rec)
            if not _valid_record(rec) or len(new) > MAX_GATE_BYTES:
                raise GateStateUnavailable("NO_PROGRESS_STATE_CORRUPT")
            if new != old:
                changed = conn.execute(
                    "UPDATE app_settings SET value=? WHERE key=? AND value=?", (new, key, old)
                ).rowcount
                if changed != 1:
                    raise GateStateUnavailable("NO_PROGRESS_STATE_CONCURRENT_HOLD")
            new_token = rec["run_token"]
            if new_token != old_token:
                expected = source + ":" + old_token if old_token else ""
                replacement = source + ":" + new_token if new_token else ""
                changed = conn.execute(
                    "UPDATE app_settings SET value=? WHERE key=? AND value=?",
                    (replacement, GATE_SLOT_KEY, expected),
                ).rowcount
                if changed != 1:
                    raise GateStateUnavailable("NO_PROGRESS_STATE_CONCURRENT_HOLD")
        return result, rec


class NoProgressGate:
    """Durable parent-only before-spawn/confirmed-reap no-progress latch.

    A web restart never proves a child was reaped: an outstanding run token
    remains held until its owning parent finalizes it. Gates never call APIs.
    """
    def __init__(self, *, store=None):
        import threading
        self._lock = threading.Lock()
        self._store = store if store is not None else DatabaseGateStore()
        self._state = {}
        self._tokens = {}

    def initialize(self, source):
        try:
            self._store.initialize(source)
            self._apply(source, lambda rec: True)
            return True
        except Exception:
            self._unavailable(source)
            return False

    def _unavailable(self, source):
        self._state[str(source)] = {
            "consecutive_no_progress": 2, "blocked_baseline_bytes": 0,
            "before_raw_bytes": 0, "last_outcome": "NO_PROGRESS_STATE_UNAVAILABLE",
            "next_retry_condition": "REQUIRES_VALID_DURABLE_STATE", "operator_hold": True,
            "active_generation": False,
        }

    def _apply(self, source, change, *, require_idle=False):
        result, rec = self._store.transact(source, change, require_idle=require_idle)
        self._state[str(source)] = rec
        return result

    @staticmethod
    def _progressed(before, after):
        if not _valid_progress(before) or not _valid_progress(after):
            return False
        # Compare only identical scopes. A newly initialized generation/page1
        # or an unrelated scope does not release a latched page.
        for key, old in before["scopes"].items():
            new = after["scopes"].get(key)
            if (new and new["generation"] == old["generation"]
                    and new["pages"] > old["pages"] and new["saved"] >= old["saved"]):
                return True
        return False

    @classmethod
    def _decision(cls, rec, raw, proof, *, explicit_operator_retry=False):
        if not raw or not _valid_progress(proof):
            return False, "NO_PROGRESS_PROOF_UNAVAILABLE"
        if rec["run_token"]:
            return False, "NO_PROGRESS_REAP_UNCONFIRMED"
        if rec["operator_hold"]:
            if explicit_operator_retry is True:
                return True, "NO_PROGRESS_EXPLICIT_OPERATOR_RETRY"
            return False, "NO_PROGRESS_OPERATOR_HOLD"
        if rec["consecutive_no_progress"] < 2:
            return True, "NO_PROGRESS_GATE_OPEN"
        if cls._progressed(rec["after_progress"], proof):
            rec.update(consecutive_no_progress=0, blocked_baseline_bytes=0,
                       last_outcome="DURABLE_PROGRESS_FOUND", next_retry_condition="")
            return True, "NO_PROGRESS_DURABLE_ADVANCE"
        baseline = rec["blocked_baseline_bytes"]
        if baseline > 0 and raw + BASELINE_RECOVERY_BYTES <= baseline:
            rec.update(consecutive_no_progress=0, blocked_baseline_bytes=0,
                       last_outcome="REAL_PARENT_BASELINE_RECOVERED", next_retry_condition="")
            return True, "NO_PROGRESS_BASELINE_RECOVERED"
        return False, "NO_PROGRESS_SAME_PAGE_MEMORY_HOLD"

    def may_start(self, source, raw_bytes, receipt_counters=None, *, explicit_operator_retry=False):
        with self._lock:
            try:
                return self._apply(source, lambda rec: self._decision(
                    rec, _positive_int(raw_bytes), receipt_counters,
                    explicit_operator_retry=explicit_operator_retry), require_idle=True)
            except GateStateUnavailable as exc:
                self._unavailable(source)
                if str(exc) == "NO_PROGRESS_OTHER_SOURCE_REAP_UNCONFIRMED":
                    self._state[source]["last_outcome"] = str(exc)
                    self._state[source]["next_retry_condition"] = "REQUIRES_CONFIRMED_REAP_OF_OTHER_SOURCE"
                    return False, str(exc)
                return False, "NO_PROGRESS_STATE_UNAVAILABLE"
            except Exception:
                self._unavailable(source)
                return False, "NO_PROGRESS_STATE_UNAVAILABLE"

    def record_launch(self, source, raw_bytes, receipt_counters, *, explicit_operator_retry=False):
        import uuid
        with self._lock:
            token = uuid.uuid4().hex
            def reserve(rec):
                allowed, _ = self._decision(rec, _positive_int(raw_bytes), receipt_counters,
                                           explicit_operator_retry=explicit_operator_retry)
                if not allowed:
                    return ""
                rec["operator_retry"] = bool(rec["operator_hold"] and explicit_operator_retry is True)
                if rec["operator_retry"]:
                    rec.update(operator_hold=False, consecutive_no_progress=0,
                               next_retry_condition="", blocked_baseline_bytes=0)
                rec.update(before_raw_bytes=_positive_int(raw_bytes), before_progress=receipt_counters,
                           run_token=token, before_classified_commits=rec["classified_commits"])
                return token
            try:
                result = self._apply(source, reserve, require_idle=True)
                if result:
                    self._tokens[source] = result
                return result
            except Exception:
                self._unavailable(source)
                return ""

    def launch_token(self, source):
        return self._tokens.get(source, "")

    def record_exit(self, source, *, raw_after_bytes, receipt_after, memory_held,
                    operator_hold=False, spawn_failed=False):
        with self._lock:
            token = self._tokens.get(source, "")
            def complete(rec):
                if not token or token != rec["run_token"]:
                    raise GateStateUnavailable("NO_PROGRESS_GENERATION_MISMATCH")
                before = rec["before_progress"]
                proof_ok = _valid_progress(receipt_after)
                progressed = (proof_ok and (self._progressed(before, receipt_after)
                    or rec["classified_commits"] > rec["before_classified_commits"]))
                # New scope/generation counts only after a committed page, and
                # only for the run that actually produced it, never latched admission.
                if proof_ok and not progressed:
                    progressed = any(v["pages"] > 0 and (
                        key not in before["scopes"] or
                        before["scopes"][key]["generation"] != v["generation"])
                        for key, v in receipt_after["scopes"].items())
                after_raw = _positive_int(raw_after_bytes)
                recovery = bool(after_raw and rec["before_raw_bytes"]
                    and after_raw + BASELINE_RECOVERY_BYTES <= rec["before_raw_bytes"])
                rec["operator_hold"] = bool(rec["operator_hold"] or operator_hold
                                             or (rec["operator_retry"] and (spawn_failed or not progressed)))
                if rec["operator_hold"]:
                    rec.update(consecutive_no_progress=2, last_outcome="SOURCE_RESPONSE_LIMIT",
                               next_retry_condition="REQUIRES_OPERATOR_REVIEW_OF_SOURCE_LIMIT")
                elif not proof_ok or not after_raw:
                    rec.update(consecutive_no_progress=2, last_outcome="NO_PROGRESS_PROOF_UNAVAILABLE",
                               next_retry_condition="REQUIRES_VALID_DURABLE_STATE")
                    # Unknown post-reap baseline must not be replaced by an
                    # arbitrary lower later value and called real recovery.
                    rec["blocked_baseline_bytes"] = 0
                elif spawn_failed:
                    rec["last_outcome"] = "SPAWN_FAILED_NO_CHILD"
                elif progressed or not memory_held or recovery:
                    rec.update(consecutive_no_progress=0, blocked_baseline_bytes=0,
                               next_retry_condition="", last_outcome=("DURABLE_PROGRESS" if progressed
                               else "REAL_PARENT_BASELINE_RECOVERY" if recovery else "NON_MEMORY_TERMINATION"))
                else:
                    rec["consecutive_no_progress"] = min(10, rec["consecutive_no_progress"] + 1)
                    rec["last_outcome"] = "MEMORY_HOLD_WITHOUT_DURABLE_PROGRESS"
                    if rec["consecutive_no_progress"] >= 2:
                        rec["blocked_baseline_bytes"] = after_raw
                        rec["next_retry_condition"] = "REQUIRES_8MIB_PARENT_BASELINE_DROP_OR_DURABLE_ADVANCE"
                if proof_ok:
                    rec["after_progress"] = receipt_after
                rec.update(run_token="", before_progress=None, before_raw_bytes=0, operator_retry=False)
                return dict(rec)
            try:
                result = self._apply(source, complete)
                self._tokens.pop(source, None)
                return result
            except Exception:
                self._unavailable(source)
                return dict(self._state[source])

    def status(self, source):
        # No SQL on status/health paths. This cache is telemetry, never authority.
        with self._lock:
            if str(source) not in self._state:
                self._unavailable(source)
            rec = dict(self._state[str(source)])
        rec["active_generation"] = bool(rec.pop("run_token", rec.get("active_generation", False)))
        if rec["active_generation"]:
            rec["next_retry_condition"] = "REQUIRES_CONFIRMED_REAP_BY_OWNING_PARENT"
            if not self._tokens.get(source):
                rec["last_outcome"] = "NO_PROGRESS_REAP_UNCONFIRMED"
        for key in ("before_progress", "after_progress", "classified_commits", "before_classified_commits", "version"):
            rec.pop(key, None)
        return rec


def _worker_progress_change(change):
    # Deliberately independent of TEST_MODE: disposable integration fixtures
    # can exercise durable control state without authorizing source traffic.
    if not enabled("budget") or os.getenv("G2B_SOURCE_DYNAMIC_CHILD") != "1":
        return False
    source = os.getenv("G2B_DYNAMIC_SOURCE_MODE", "")
    token = os.getenv("G2B_DYNAMIC_SOURCE_GENERATION", "")
    def update(rec):
        if not token or token != rec["run_token"]:
            raise GateStateUnavailable("NO_PROGRESS_GENERATION_MISMATCH")
        change(rec)
        return True
    try:
        return DatabaseGateStore().transact(source, update)[0]
    except Exception:
        import memory_guard
        raise memory_guard.MemoryPressureError("NO_PROGRESS_DURABLE_WRITE_HOLD") from None


_CLASSIFICATION_PROGRESS_LOCK = threading.Lock()
_CLASSIFICATION_PROGRESS_RECORDED = None


def record_classification_commit(*, rows=1):
    """Persist the first successful commit once per child/source/generation.

    The gate needs evidence of any committed advancement, not a per-row WAL
    counter. Cache only AFTER the durable update succeeds. A failed write must
    still HOLD, and a retry/new child/new token must attempt persistence again.
    """
    global _CLASSIFICATION_PROGRESS_RECORDED
    if type(rows) is not int or rows < 1:
        raise ValueError("INVALID_CLASSIFICATION_COMMIT_COUNT")
    if not enabled("budget") or os.getenv("G2B_SOURCE_DYNAMIC_CHILD") != "1":
        return False
    key = (os.getpid(), os.getenv("G2B_DYNAMIC_SOURCE_MODE", ""),
           os.getenv("G2B_DYNAMIC_SOURCE_GENERATION", ""))
    with _CLASSIFICATION_PROGRESS_LOCK:
        if _CLASSIFICATION_PROGRESS_RECORDED == key:
            return True
        recorded = _worker_progress_change(
            lambda rec: rec.update(classified_commits=rec["classified_commits"] + 1))
        if recorded:
            _CLASSIFICATION_PROGRESS_RECORDED = key
        return recorded


def record_operator_hold():
    """Persist deterministic response/row bound latch before worker termination."""
    return _worker_progress_change(lambda rec: rec.update(operator_hold=True))
