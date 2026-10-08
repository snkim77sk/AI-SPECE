"""Transactional PostgreSQL page collector for budget RAW.

Budget payload, immutable observation, current-state pointer, page receipt and checkpoint
commit in one PostgreSQL transaction.  This avoids cross-database partial commits.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid

from sqlalchemy import and_, func, insert, select

import budget_pg_store

COLLECTION_VERSION = 1
BUDGET_REPLAYABLE_DRIFT_ERRORS = frozenset({
    "REPEATED_OR_OVERLAPPING_PAGE",
})
BUDGET_AUTO_DRIFT_REPLAY_LIMIT = 1
_TRUE_ENV = {"1", "true", "yes", "on"}


def _memory_checkpoint():
    if str(os.getenv("G2B_TEST_MODE", "0") or "0").strip().lower() in _TRUE_ENV:
        return
    import memory_guard

    memory_guard.cooperative_batch_checkpoint(timeout=5.0)



def _meta(cp):
    try:
        value = json.loads((cp or {}).get("cursor_value") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _result(cp, resumed=False):
    return {
        "dataset": cp["dataset"],
        "scope": cp["scope_key"],
        "fetched": int(cp["fetched_count"]),
        "saved": int(cp["saved_count"]),
        "source_total": None if int(cp["source_total"]) < 0 else int(cp["source_total"]),
        "complete": cp.get("status") == "COMPLETE",
        "resumed": bool(resumed),
        "status": cp.get("status"),
        "reason": cp.get("last_error", ""),
        "completion_reason": _meta(cp).get("completion_reason", ""),
        "raw_backend": "POSTGRESQL",
        "drift_replay_count": int(_meta(cp).get("drift_replay_count") or 0),
        "drift_replay_exhausted": (
            str(cp.get("last_error") or "")
            == "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED"
        ),
    }


def _safe_error_label(exc):
    name = type(exc).__name__
    if name == "MemoryPressureError":
        return "MEMORY_PRESSURE"
    code = str(getattr(exc, "code", "") or "").strip()
    return f"{name}:{code}" if code else name


def _failure_checkpoint_status(exc):
    return (
        "INCOMPLETE"
        if type(exc).__name__ == "MemoryPressureError"
        else "FAILED"
    )


def verified_checkpoint(cp, *, require_current=True):
    """Verify one checkpoint without materializing dataset-wide receipt/state sets.

    Receipt items can be hundreds of thousands of rows for one QWGJK snapshot.
    Validate referential bindings in SQL, then stream only one receipt page worth
    of key/hash pairs at a time for the page fingerprint check.
    """
    meta = _meta(cp)
    complete = (cp or {}).get("status") == "COMPLETE"
    if not cp or meta.get("version") != COLLECTION_VERSION:
        return False
    generation = str(meta.get("generation") or "")
    if not generation:
        return False
    try:
        page_size = int(cp["page_size"])
        next_page = int(cp["page_no"])
        fetched = int(cp["fetched_count"])
        saved = int(cp["saved_count"])
        source_total = int(cp["source_total"])
    except (KeyError, TypeError, ValueError):
        return False
    if page_size < 1 or fetched < 0 or saved != fetched:
        return False

    engine, t = budget_pg_store._engine_and_tables()
    pages_t, items_t = t["pages"], t["items"]
    obs_t, state_t = t["observations"], t["states"]
    key_filter = and_(
        pages_t.c.dataset == cp["dataset"],
        pages_t.c.scope_key == cp["scope_key"],
        pages_t.c.generation == generation,
    )
    item_filter = and_(
        items_t.c.dataset == cp["dataset"],
        items_t.c.scope_key == cp["scope_key"],
        items_t.c.generation == generation,
    )

    with engine.connect() as conn:
        pages = conn.execute(
            select(pages_t).where(key_filter).order_by(pages_t.c.page_no)
        ).mappings().all()
        if next_page != len(pages) + 1:
            return False

        item_count = int(conn.execute(
            select(func.count()).select_from(items_t).where(item_filter)
        ).scalar_one() or 0)
        if fetched != item_count:
            return False

        revision_missing = int(conn.execute(
            select(func.count())
            .select_from(
                items_t.outerjoin(
                    obs_t,
                    and_(
                        obs_t.c.dataset == items_t.c.dataset,
                        obs_t.c.record_key == items_t.c.source_key,
                        obs_t.c.sha256 == items_t.c.payload_sha256,
                    ),
                )
            )
            .where(item_filter, obs_t.c.id.is_(None))
        ).scalar_one() or 0)
        if revision_missing:
            return False

        if require_current:
            current_missing = int(conn.execute(
                select(func.count())
                .select_from(
                    items_t.outerjoin(
                        state_t,
                        and_(
                            state_t.c.dataset == items_t.c.dataset,
                            state_t.c.record_key == items_t.c.source_key,
                            state_t.c.payload_sha256 == items_t.c.payload_sha256,
                        ),
                    )
                )
                .where(item_filter, state_t.c.record_key.is_(None))
            ).scalar_one() or 0)
            if current_missing:
                return False

        item_result = conn.execution_options(
            stream_results=True,
            max_row_buffer=max(1, min(page_size, 2000)),
        ).execute(
            select(
                items_t.c.page_no,
                items_t.c.source_key,
                items_t.c.payload_sha256,
            )
            .where(item_filter)
            .order_by(
                items_t.c.page_no,
                items_t.c.source_key,
                items_t.c.payload_sha256,
            )
        ).mappings()
        item_iter = iter(item_result)
        current_item = next(item_iter, None)

        count = 0
        total = -1
        full_page_seen = False
        terminal_reason = ""

        for number, page in enumerate(pages, 1):
            page_no = int(page["page_no"])
            if page_no != number or int(page["page_size"]) != page_size:
                return False

            pairs = []
            while current_item is not None and int(current_item["page_no"]) == page_no:
                pairs.append((
                    str(current_item["source_key"]),
                    str(current_item["payload_sha256"]),
                ))
                current_item = next(item_iter, None)

            if current_item is not None and int(current_item["page_no"]) < page_no:
                return False
            if (
                int(page["item_count"]) != len(pairs)
                or len(pairs) > page_size
                or hashlib.sha256(json.dumps(sorted(pairs)).encode()).hexdigest()
                != str(page["response_hash"])
            ):
                return False

            reported = int(page["source_total"])
            if reported != total and (total > 0 or reported <= 0):
                return False
            total = reported
            count += len(pairs)
            done = count == total if total > 0 else (
                not pairs or (len(pairs) < page_size and full_page_seen)
            )
            terminal_reason = (
                "TOTAL_REACHED" if done and total > 0
                else "EMPTY_PAGE" if done and not pairs
                else "SHORT_PAGE_UNKNOWN_TOTAL" if done
                else ""
            )
            if str(page["terminal_reason"] or "") != terminal_reason:
                return False
            if done and number != len(pages):
                return False
            full_page_seen = full_page_seen or len(pairs) == page_size

        if current_item is not None:
            return False

    return bool(
        count == fetched
        and saved == fetched
        and total == source_total
        and terminal_reason == str(meta.get("completion_reason") or "")
        and bool(terminal_reason) == complete
    )


def collect_pages(*, dataset, scope, range_start, range_end, page_size, max_pages,
                  resume, fetch, identity, source_system, source_operation, source_date,
                  validate_row=None, checkpoint_contract="", advance_current=True):
    if dataset not in budget_pg_store.BUDGET_DATASETS:
        raise ValueError("UNSUPPORTED_BUDGET_DATASET")
    size = int(page_size)
    if size < 1 or (max_pages is not None and int(max_pages) < 1):
        raise ValueError("page size and page budget must be positive")

    engine, t = budget_pg_store._engine_and_tables()
    cp_t, pages_t, items_t = t["checkpoints"], t["pages"], t["items"]
    observed = budget_pg_store.get_checkpoint(dataset, scope)
    cp = observed if resume else None
    meta = _meta(cp)
    replay_scope = False
    replay_count = 0
    contract = str(checkpoint_contract or "").strip()
    fingerprint = hashlib.sha256(json.dumps(
        [dataset, scope, str(range_start), str(range_end), source_operation, contract],
        ensure_ascii=False,
    ).encode()).hexdigest()

    if meta.get("version") == COLLECTION_VERSION:
        if str(meta.get("checkpoint_contract") or "") != contract:
            raise ValueError("resume collection contract changed; replay explicitly with resume=False")
        replay_count = max(0, int(meta.get("drift_replay_count") or 0))
        checkpoint_status = str((cp or {}).get("status") or "").upper()
        checkpoint_error = str((cp or {}).get("last_error") or "")
        if (
            str(dataset) == "budget"
            and checkpoint_status == "INCOMPLETE"
            and checkpoint_error in BUDGET_REPLAYABLE_DRIFT_ERRORS
            and replay_count < BUDGET_AUTO_DRIFT_REPLAY_LIMIT
        ):
            # QWGJK can drift between deep pages. Continuing the same cursor repeats
            # the same overlap forever. Start a fresh receipt generation from page 1
            # while preserving all normalized observations already stored.
            cp = None
            replay_scope = True
        elif checkpoint_error == "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED":
            # Do not burn one source request every scheduler wake after a replay
            # already proved the overlap persistent. A later repair can switch to
            # region partitions without losing normalized data.
            return _result(cp, resumed=True)
        elif meta.get("page_size") != size or meta.get("query_fingerprint") != fingerprint:
            raise ValueError("resume query/page size changed; replay explicitly with resume=False")
        else:
            receipt_valid = verified_checkpoint(
                cp, require_current=bool(advance_current)
            )
            if cp.get("status") == "COMPLETE":
                if receipt_valid:
                    return _result(cp, resumed=True)
                cp = None
            elif cp.get("status") in {"RUNNING", "FAILED", "INCOMPLETE"}:
                if not receipt_valid:
                    cp = None
            else:
                cp = None
    else:
        cp = None

    resumed_from_checkpoint = cp is not None

    if cp is None:
        # Explicit replays clear old receipts. Automatic overlap recovery keeps the
        # abandoned generation until normal retention so deleting hundreds of
        # thousands of receipts cannot itself create memory/lock pressure.
        if not replay_scope:
            budget_pg_store.clear_collection_receipts(dataset, scope)
        meta = {
            "version": COLLECTION_VERSION,
            "generation": uuid.uuid4().hex,
            "page_size": size,
            "query_fingerprint": fingerprint,
            "checkpoint_contract": contract,
            "completion_reason": "",
            "drift_replay_count": (
                replay_count + 1 if replay_scope else 0
            ),
        }
        cp = {
            "dataset": dataset, "scope_key": scope, "page_no": 1,
            "source_total": -1, "fetched_count": 0, "saved_count": 0,
        }

    values = dict(
        cursor_value=json.dumps(meta, sort_keys=True),
        range_start=str(range_start), range_end=str(range_end),
        page_no=int(cp["page_no"]), page_size=size,
        last_page_fingerprint="", source_total=int(cp["source_total"]),
        fetched_count=int(cp["fetched_count"]), saved_count=int(cp["saved_count"]),
        status="RUNNING", last_error="",
    )
    # Match the SQLite collector's compare-and-set start. A stale process from a
    # rolling deployment must never move a PostgreSQL checkpoint backwards after
    # another process has already advanced it.
    with engine.begin() as conn:
        current = conn.execute(
            select(cp_t).where(and_(
                cp_t.c.dataset == dataset,
                cp_t.c.scope_key == scope,
            )).with_for_update()
        ).mappings().first()
        fields = ("cursor_value", "page_no", "fetched_count", "saved_count", "status")
        if bool(current) != bool(observed) or (
            current and any(current[name] != observed[name] for name in fields)
        ):
            raise RuntimeError("CONCURRENT_CHECKPOINT_CHANGED")
        budget_pg_store.save_checkpoint(
            dataset, scope, _conn=conn, **values
        )
    committed = dict(values)
    generation = str(meta["generation"])

    with engine.connect() as conn:
        full_page_seen = bool(conn.execute(
            select(pages_t.c.page_no).where(and_(
                pages_t.c.dataset == dataset,
                pages_t.c.scope_key == scope,
                pages_t.c.generation == generation,
                pages_t.c.item_count == pages_t.c.page_size,
            )).limit(1)
        ).first())

    for _ in range(int(max_pages) if max_pages is not None else 1000000):
        page_no = int(committed["page_no"])
        try:
            # Re-check memory before every PostgreSQL-backed source page so a
            # long collection cannot outrun the admission guard.
            _memory_checkpoint()
            from vnext_source_guard import (
                current_source_request_context,
                require_attested_transport_result,
            )
            before = current_source_request_context()
            source_result = require_attested_transport_result(
                before, fetch(page_no, size),
                error_code="BUDGET_SOURCE_COLLECTION_TRANSPORT_NOT_ATTESTED",
            )
            rows, reported = source_result
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("invalid source row shape")
            known = int(reported) if reported is not None and int(reported) > 0 else -1
            total = int(committed["source_total"])
            problem = ""
            if known > 0:
                if total > 0 and total != known:
                    problem = "SOURCE_TOTAL_CHANGED"
                total = known
            if validate_row:
                problems = [validate_row(row) for row in rows]
                problem = next((value for value in problems if value), problem)

            keys = [str(identity(row)) for row in rows]
            digests = [
                budget_pg_store.observation_digest(dataset, row)
                for row in rows
            ]
            if len(set(keys)) != len(keys):
                problem = "DUPLICATE_OR_COLLIDING_SOURCE_ID"
            if len(rows) > size:
                problem = "OVERSIZED_PAGE"
            fetched = int(committed["fetched_count"]) + len(rows)
            if total > 0 and fetched > total:
                problem = "SOURCE_TOTAL_UNDERRUN"
            if not rows and total > 0 and fetched < total:
                problem = "PREMATURE_EMPTY_PAGE"

            with engine.begin() as conn:
                current = conn.execute(
                    select(cp_t).where(and_(
                        cp_t.c.dataset == dataset, cp_t.c.scope_key == scope
                    ))
                ).mappings().first()
                if (
                    not current
                    or str(current["cursor_value"]) != str(committed["cursor_value"])
                    or int(current["page_no"]) != page_no
                    or int(current["fetched_count"]) != int(committed["fetched_count"])
                    or str(current["status"]) != "RUNNING"
                ):
                    raise RuntimeError("CONCURRENT_CHECKPOINT_CHANGED")

                if keys:
                    repeated = conn.execute(
                        select(items_t.c.source_key).where(and_(
                            items_t.c.dataset == dataset,
                            items_t.c.scope_key == scope,
                            items_t.c.generation == generation,
                            items_t.c.source_key.in_(keys),
                        )).limit(1)
                    ).first()
                    if repeated:
                        problem = (
                            "REPEATED_OR_OVERLAPPING_PAGE_REPLAY_EXHAUSTED"
                            if int(meta.get("drift_replay_count") or 0)
                            >= BUDGET_AUTO_DRIFT_REPLAY_LIMIT
                            else "REPEATED_OR_OVERLAPPING_PAGE"
                        )

                for row, key in zip(rows, keys):
                    budget_pg_store.preserve_observation(
                        dataset, key, row,
                        source_system=source_system,
                        source_operation=source_operation,
                        source_date=source_date(row),
                        _conn=conn,
                        advance_current=bool(advance_current),
                    )

                if problem:
                    stopped = dict(committed, status="INCOMPLETE", last_error=problem)
                    budget_pg_store.save_checkpoint(
                        dataset, scope, _conn=conn, **stopped
                    )
                else:
                    if total > 0:
                        done = fetched == total
                    elif not rows:
                        done = True
                    else:
                        done = len(rows) < size and full_page_seen
                    reason = (
                        "TOTAL_REACHED" if total > 0 and done
                        else "EMPTY_PAGE" if done and not rows
                        else "SHORT_PAGE_UNKNOWN_TOTAL" if done
                        else ""
                    )
                    terminal = dict(meta, completion_reason=reason)
                    conn.execute(insert(pages_t).values(
                        dataset=dataset, scope_key=scope, generation=generation,
                        page_no=page_no, page_size=size,
                        response_hash=hashlib.sha256(
                            json.dumps(sorted(zip(keys, digests))).encode()
                        ).hexdigest(),
                        item_count=len(rows), source_total=total,
                        terminal_reason=reason,
                    ))
                    if rows:
                        conn.execute(insert(items_t), [
                            {
                                "dataset": dataset, "scope_key": scope,
                                "generation": generation, "source_key": key,
                                "page_no": page_no, "payload_sha256": digest,
                            }
                            for key, digest in zip(keys, digests)
                        ])
                    next_values = dict(
                        committed,
                        cursor_value=json.dumps(terminal, sort_keys=True),
                        page_no=page_no + 1, source_total=total,
                        fetched_count=fetched, saved_count=fetched,
                        status="COMPLETE" if done else "RUNNING",
                        last_error="",
                    )
                    budget_pg_store.save_checkpoint(
                        dataset, scope, _conn=conn, **next_values
                    )

            if problem:
                return _result(
                    {**stopped, "dataset": dataset, "scope_key": scope},
                    resumed=resumed_from_checkpoint,
                )
            committed = next_values
            meta = terminal
            if len(rows) == size:
                full_page_seen = True
            if done:
                return _result(
                    {**committed, "dataset": dataset, "scope_key": scope},
                    resumed=resumed_from_checkpoint,
                )

        except Exception as exc:
            try:
                current = budget_pg_store.get_checkpoint(dataset, scope)
                if (
                    current
                    and str(current["cursor_value"]) == str(committed["cursor_value"])
                    and int(current["page_no"]) == int(committed["page_no"])
                    and int(current["fetched_count"]) == int(committed["fetched_count"])
                ):
                    budget_pg_store.save_checkpoint(
                        dataset,
                        scope,
                        **dict(
                            committed,
                            status=_failure_checkpoint_status(exc),
                            last_error=_safe_error_label(exc),
                        ),
                    )
            except Exception:
                pass
            raise

    return _result(
        {**committed, "dataset": dataset, "scope_key": scope},
        resumed=resumed_from_checkpoint,
    )
