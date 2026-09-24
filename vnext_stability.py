"""Replay verification for receipt-complete vNext source collections.

A page receipt proves what we stored, but not that the upstream listing stayed stable
while offset-based pages were being read. This module re-fetches every committed
page and compares the page identity/payload receipt before historical data can be
considered stable enough for normalization.

RAW and prior receipts are never deleted. If replay proves the listing changed, the
checkpoint is reset to an explicit recollect marker so the next collector invocation
starts a fresh generation from page 1.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

from db import connect
from vnext_collection import verified_checkpoint
from vnext_provenance import VNextProvenanceError, seal_evidence, verify_evidence
from vnext_store import get_checkpoint, save_checkpoint

DEFAULT_STABILITY_MAX_AGE_HOURS = 24
MAX_STABILITY_MAX_AGE_HOURS = 168
STABILITY_PROVENANCE_PURPOSE = "SOURCE_STABILITY_REPLAY"
_CLOCK_SKEW = dt.timedelta(minutes=5)


def _meta(cp):
    try:
        value = json.loads((cp or {}).get("cursor_value") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _payload_digest(row):
    text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _page_hash(keys, digests):
    return hashlib.sha256(json.dumps(sorted(zip(keys, digests))).encode()).hexdigest()


def _receipt_digest(cp, generation):
    aggregate = hashlib.sha256()
    with connect() as conn:
        pages = [dict(row) for row in conn.execute(
            """SELECT page_no,response_hash
               FROM vnext_collection_pages
               WHERE dataset=? AND scope_key=? AND generation=?
               ORDER BY page_no""",
            (cp["dataset"], cp["scope_key"], generation),
        ).fetchall()]
    for receipt in pages:
        aggregate.update(f"{int(receipt['page_no'])}:{receipt['response_hash']};".encode("utf-8"))
    return len(pages), aggregate.hexdigest()


def _stability_evidence(cp, stable):
    return {
        "dataset": str(cp.get("dataset") or ""),
        "scope_key": str(cp.get("scope_key") or ""),
        "generation": str(stable.get("generation") or ""),
        "page_count": int(stable.get("page_count") or 0),
        "digest": str(stable.get("digest") or ""),
        "verified_at_utc": str(stable.get("verified_at_utc") or ""),
        "source_commit_sha": str(stable.get("source_commit_sha") or ""),
    }


def stability_max_age_hours(value=None):
    raw = value if value is not None else os.getenv(
        "G2B_VNEXT_STABILITY_MAX_AGE_HOURS", str(DEFAULT_STABILITY_MAX_AGE_HOURS)
    )
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        raise ValueError("G2B_VNEXT_STABILITY_MAX_AGE_HOURS must be an integer") from None
    if hours < 1 or hours > MAX_STABILITY_MAX_AGE_HOURS:
        raise ValueError(
            f"G2B_VNEXT_STABILITY_MAX_AGE_HOURS must be between 1 and {MAX_STABILITY_MAX_AGE_HOURS}"
        )
    return hours


def stability_verified_checkpoint(cp):
    if not verified_checkpoint(cp):
        return False
    meta = _meta(cp)
    stable = meta.get("stability") if isinstance(meta.get("stability"), dict) else {}
    generation = str(meta.get("generation") or "")
    if (
        stable.get("status") != "VERIFIED"
        or str(stable.get("generation") or "") != generation
        or not generation
        or meta.get("stability_recollect_required")
    ):
        return False
    try:
        page_count, digest = _receipt_digest(cp, generation)
        if page_count < 1:
            return False
        if int(stable.get("page_count") or 0) != page_count:
            return False
        if str(stable.get("digest") or "") != digest:
            return False
        evidence = _stability_evidence(cp, stable)
        verify_evidence(evidence, stable.get("provenance"), purpose=STABILITY_PROVENANCE_PURPOSE)
        source_sha = evidence["source_commit_sha"]
        if source_sha:
            from vnext_live_gate import runtime_source_sha
            current_sha = runtime_source_sha()
            if current_sha and current_sha != source_sha:
                return False
    except (VNextProvenanceError, ValueError, TypeError, KeyError, RuntimeError):
        return False
    return True


def stability_verified_at(cp):
    if not stability_verified_checkpoint(cp):
        return ""
    stable = _meta(cp).get("stability") or {}
    return str(stable.get("verified_at_utc") or "")


def _parse_verified_at(cp):
    value = stability_verified_at(cp)
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def stability_fresh_checkpoint(cp, *, now=None, max_age_hours=None):
    if not stability_verified_checkpoint(cp):
        return False
    verified_at = _parse_verified_at(cp)
    if not verified_at:
        return False
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current = current.astimezone(dt.timezone.utc)
    age = current - verified_at
    if age < -_CLOCK_SKEW:
        return False
    return age <= dt.timedelta(hours=stability_max_age_hours(max_age_hours))


def _checkpoint_values(cp, *, cursor_value, status=None, last_error=None,
                       page_no=None, source_total=None, fetched_count=None, saved_count=None):
    return {
        "cursor_value": cursor_value,
        "range_start": str(cp.get("range_start") or ""),
        "range_end": str(cp.get("range_end") or ""),
        "page_no": int(cp.get("page_no") if page_no is None else page_no),
        "page_size": int(cp.get("page_size") or 0),
        "last_page_fingerprint": str(cp.get("last_page_fingerprint") or ""),
        "source_total": int(cp.get("source_total") if source_total is None else source_total),
        "fetched_count": int(cp.get("fetched_count") if fetched_count is None else fetched_count),
        "saved_count": int(cp.get("saved_count") if saved_count is None else saved_count),
        "status": str(cp.get("status") if status is None else status),
        "last_error": str(cp.get("last_error") if last_error is None else last_error),
    }


def _cas_update(cp, **values):
    dataset = cp["dataset"]
    scope = cp["scope_key"]
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?", (dataset, scope)
        ).fetchone()
        if not current:
            raise RuntimeError("STABILITY_CHECKPOINT_DISAPPEARED")
        fields = ("cursor_value", "page_no", "fetched_count", "saved_count", "status")
        if any(current[field] != cp[field] for field in fields):
            raise RuntimeError("STABILITY_CHECKPOINT_CHANGED")
        save_checkpoint(dataset, scope, _conn=conn, **values)


def _invalidate_for_recollect(cp, reason, generation):
    marker = {
        "stability_recollect_required": True,
        "previous_generation": generation,
        "stability": {"status": "CHANGED", "generation": generation, "reason": reason},
    }
    _cas_update(
        cp,
        **_checkpoint_values(
            cp, cursor_value=json.dumps(marker, sort_keys=True), status="RUNNING",
            last_error=reason, page_no=1, source_total=-1, fetched_count=0, saved_count=0,
        ),
    )


def _proof_source_sha():
    try:
        from vnext_source_guard import current_source_request_context
        context = current_source_request_context() or {}
        source_sha = str(context.get("source_commit_sha") or "").strip()
        if source_sha:
            return source_sha
    except Exception:
        pass
    try:
        from vnext_live_gate import runtime_source_sha
        return str(runtime_source_sha() or "").strip()
    except Exception:
        return ""


def verify_checkpoint_source(*, dataset, scope, fetch, identity, validate_row=None,
                             now=None, max_age_hours=None):
    cp = get_checkpoint(dataset, scope)
    if not cp or not verified_checkpoint(cp):
        return {"stable": False, "reason": "RECEIPT_CHECKPOINT_NOT_COMPLETE", "replayed_pages": 0}
    meta = _meta(cp)
    generation = str(meta.get("generation") or "")
    if not generation:
        return {"stable": False, "reason": "MISSING_COLLECTION_GENERATION", "replayed_pages": 0}
    if stability_fresh_checkpoint(cp, now=now, max_age_hours=max_age_hours):
        stable = meta.get("stability") or {}
        return {"stable": True, "reason": "ALREADY_VERIFIED",
                "replayed_pages": int(stable.get("page_count") or 0),
                "verified_at_utc": str(stable.get("verified_at_utc") or "")}
    with connect() as conn:
        pages = [dict(row) for row in conn.execute(
            """SELECT page_no,page_size,response_hash,item_count,source_total,terminal_reason
               FROM vnext_collection_pages
               WHERE dataset=? AND scope_key=? AND generation=? ORDER BY page_no""",
            (dataset, scope, generation),
        ).fetchall()]
    if not pages:
        return {"stable": False, "reason": "NO_RECEIPT_PAGES", "replayed_pages": 0}
    aggregate = hashlib.sha256()
    positive_totals = set()
    try:
        for receipt in pages:
            page_no = int(receipt["page_no"])
            page_size = int(receipt["page_size"])
            items, reported = fetch(page_no, page_size)
            if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
                _invalidate_for_recollect(cp, "SOURCE_REPLAY_ROW_SHAPE_CHANGED", generation)
                return {"stable": False, "reason": "SOURCE_REPLAY_ROW_SHAPE_CHANGED", "replayed_pages": page_no - 1}
            if validate_row:
                problems = [validate_row(row) for row in items]
                if any(problems):
                    _invalidate_for_recollect(cp, "SOURCE_REPLAY_SCOPE_CHANGED", generation)
                    return {"stable": False, "reason": "SOURCE_REPLAY_SCOPE_CHANGED", "replayed_pages": page_no - 1}
            keys = [str(identity(row)) for row in items]
            if len(set(keys)) != len(keys):
                _invalidate_for_recollect(cp, "SOURCE_REPLAY_IDENTITY_COLLISION", generation)
                return {"stable": False, "reason": "SOURCE_REPLAY_IDENTITY_COLLISION", "replayed_pages": page_no - 1}
            digests = [_payload_digest(row) for row in items]
            response_hash = _page_hash(keys, digests)
            if len(items) != int(receipt["item_count"]) or response_hash != receipt["response_hash"]:
                _invalidate_for_recollect(cp, "SOURCE_ORDER_OR_PAYLOAD_CHANGED", generation)
                return {"stable": False, "reason": "SOURCE_ORDER_OR_PAYLOAD_CHANGED", "replayed_pages": page_no - 1}
            stored_total = int(receipt["source_total"] or 0)
            if stored_total > 0:
                if reported is None or int(reported) != stored_total:
                    _invalidate_for_recollect(cp, "SOURCE_TOTAL_CHANGED_ON_REPLAY", generation)
                    return {"stable": False, "reason": "SOURCE_TOTAL_CHANGED_ON_REPLAY", "replayed_pages": page_no - 1}
            elif reported is not None and int(reported) > 0:
                positive_totals.add(int(reported))
            aggregate.update(f"{page_no}:{response_hash};".encode("utf-8"))
    except Exception as exc:
        return {"stable": False, "reason": f"STABILITY_REPLAY_ERROR:{type(exc).__name__}", "replayed_pages": 0}
    if len(positive_totals) > 1:
        _invalidate_for_recollect(cp, "SOURCE_TOTAL_DRIFT_ON_REPLAY", generation)
        return {"stable": False, "reason": "SOURCE_TOTAL_DRIFT_ON_REPLAY", "replayed_pages": len(pages)}
    if positive_totals and next(iter(positive_totals)) != int(cp["fetched_count"]):
        _invalidate_for_recollect(cp, "SOURCE_TOTAL_COVERAGE_MISMATCH", generation)
        return {"stable": False, "reason": "SOURCE_TOTAL_COVERAGE_MISMATCH", "replayed_pages": len(pages)}
    latest = get_checkpoint(dataset, scope)
    if not latest or latest["cursor_value"] != cp["cursor_value"] or latest["page_no"] != cp["page_no"]:
        raise RuntimeError("STABILITY_CHECKPOINT_CHANGED")
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    verified_at = current.astimezone(dt.timezone.utc).isoformat()
    stable_meta = dict(meta)
    stable_meta["stability_recollect_required"] = False
    stable = {
        "status": "VERIFIED", "generation": generation, "page_count": len(pages),
        "digest": aggregate.hexdigest(), "verified_at_utc": verified_at,
        "source_commit_sha": _proof_source_sha(),
    }
    evidence_cp = dict(cp)
    evidence_cp["dataset"] = dataset
    evidence_cp["scope_key"] = scope
    stable["provenance"] = seal_evidence(
        _stability_evidence(evidence_cp, stable), purpose=STABILITY_PROVENANCE_PURPOSE
    )
    stable_meta["stability"] = stable
    _cas_update(
        cp,
        **_checkpoint_values(cp, cursor_value=json.dumps(stable_meta, sort_keys=True),
                             status="COMPLETE", last_error=""),
    )
    return {"stable": True, "reason": "VERIFIED", "replayed_pages": len(pages),
            "verified_at_utc": verified_at}


_verify_checkpoint_source_unattested = verify_checkpoint_source


def verify_checkpoint_source(*, dataset, scope, fetch, identity, validate_row=None,
                             now=None, max_age_hours=None):
    from vnext_source_guard import current_source_request_context, require_attested_transport_result

    def attested_fetch(page, page_size):
        before = current_source_request_context()
        result = fetch(page, page_size)
        return require_attested_transport_result(
            before, result, error_code="SOURCE_REPLAY_TRANSPORT_NOT_ATTESTED"
        )

    result = _verify_checkpoint_source_unattested(
        dataset=dataset, scope=scope, fetch=attested_fetch, identity=identity,
        validate_row=validate_row, now=now, max_age_hours=max_age_hours,
    )
    if result.get("reason") == "STABILITY_REPLAY_ERROR:VNextSourceTransportAttestationError":
        return {**result, "reason": "SOURCE_REPLAY_TRANSPORT_NOT_ATTESTED"}
    return result
