"""Replay verification for receipt-complete vNext source collections.

A page receipt proves what we stored, but not that the upstream listing stayed stable
while offset-based pages were being read.  This module re-fetches every committed
page and compares the page identity/payload receipt before historical data can be
considered stable enough for normalization.

RAW and prior receipts are never deleted.  If replay proves the listing changed, the
checkpoint is reset to an explicit recollect marker so the next collector invocation
starts a fresh generation from page 1.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from db import connect
from vnext_collection import verified_checkpoint
from vnext_store import get_checkpoint, save_checkpoint


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
    # Must match vnext_collection.py's receipt hash semantics.
    return hashlib.sha256(json.dumps(sorted(zip(keys, digests))).encode()).hexdigest()


def stability_verified_checkpoint(cp):
    """True only when receipt completeness and source replay both match one generation."""
    if not verified_checkpoint(cp):
        return False
    meta = _meta(cp)
    stable = meta.get("stability") if isinstance(meta.get("stability"), dict) else {}
    return (
        stable.get("status") == "VERIFIED"
        and stable.get("generation") == meta.get("generation")
        and not meta.get("stability_recollect_required")
    )


def stability_verified_at(cp):
    """Return the UTC replay-verification timestamp for audit/display, if recorded.

    Older VERIFIED checkpoints remain valid for backward compatibility but return an
    empty timestamp.  The timestamp is deliberately informational: hard expiration is
    an operator policy because large historical backfills may legitimately take more
    than a fixed number of hours.
    """
    if not stability_verified_checkpoint(cp):
        return ""
    stable = _meta(cp).get("stability") or {}
    return str(stable.get("verified_at_utc") or "")


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
    """Update the checkpoint only if the replayed generation is still current."""
    dataset = cp["dataset"]
    scope = cp["scope_key"]
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM collection_checkpoints WHERE dataset=? AND scope_key=?",
            (dataset, scope),
        ).fetchone()
        if not current:
            raise RuntimeError("STABILITY_CHECKPOINT_DISAPPEARED")
        fields = ("cursor_value", "page_no", "fetched_count", "saved_count", "status")
        if any(current[field] != cp[field] for field in fields):
            raise RuntimeError("STABILITY_CHECKPOINT_CHANGED")
        save_checkpoint(dataset, scope, _conn=conn, **values)


def _invalidate_for_recollect(cp, reason, generation):
    """Leave RAW/receipts intact but force the next collection to start a new generation."""
    marker = {
        "stability_recollect_required": True,
        "previous_generation": generation,
        "stability": {"status": "CHANGED", "generation": generation, "reason": reason},
    }
    _cas_update(
        cp,
        **_checkpoint_values(
            cp,
            cursor_value=json.dumps(marker, sort_keys=True),
            status="RUNNING",
            last_error=reason,
            page_no=1,
            source_total=-1,
            fetched_count=0,
            saved_count=0,
        ),
    )


def verify_checkpoint_source(*, dataset, scope, fetch, identity, validate_row=None):
    """Replay every receipt page and verify that upstream identity/payload boundaries match.

    ``fetch(page_no, page_size)`` must return ``(items, source_total)``.  No source
    values are returned from this function; the result contains only counts/status.
    """
    cp = get_checkpoint(dataset, scope)
    if not cp or not verified_checkpoint(cp):
        return {"stable": False, "reason": "RECEIPT_CHECKPOINT_NOT_COMPLETE", "replayed_pages": 0}

    meta = _meta(cp)
    generation = str(meta.get("generation") or "")
    if not generation:
        return {"stable": False, "reason": "MISSING_COLLECTION_GENERATION", "replayed_pages": 0}
    if stability_verified_checkpoint(cp):
        stable = meta.get("stability") or {}
        return {
            "stable": True,
            "reason": "ALREADY_VERIFIED",
            "replayed_pages": int(stable.get("page_count") or 0),
            "verified_at_utc": str(stable.get("verified_at_utc") or ""),
        }

    with connect() as conn:
        pages = [dict(row) for row in conn.execute(
            """SELECT page_no,page_size,response_hash,item_count,source_total,terminal_reason
               FROM vnext_collection_pages
               WHERE dataset=? AND scope_key=? AND generation=?
               ORDER BY page_no""",
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
        # Transport/transient failures do not destroy a valid receipt generation.
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
    verified_at = dt.datetime.now(dt.timezone.utc).isoformat()
    stable_meta = dict(meta)
    stable_meta["stability_recollect_required"] = False
    stable_meta["stability"] = {
        "status": "VERIFIED",
        "generation": generation,
        "page_count": len(pages),
        "digest": aggregate.hexdigest(),
        "verified_at_utc": verified_at,
    }
    _cas_update(
        cp,
        **_checkpoint_values(
            cp,
            cursor_value=json.dumps(stable_meta, sort_keys=True),
            status="COMPLETE",
            last_error="",
        ),
    )
    return {"stable": True, "reason": "VERIFIED", "replayed_pages": len(pages),
            "verified_at_utc": verified_at}
