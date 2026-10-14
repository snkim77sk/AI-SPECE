"""CI ONLY: verify an exact QWGJK page 424/477 durable resume boundary.

Using one SYNTHETIC row/page deliberately tests deep cursor and 423
historical receipts, not a 423,000-row production dataset. V4.1.293 already
tests an independent 1,000-row page under the same 256MiB memory cgroup.
No real source calls, credentials, live DB, or destructive production actions.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CI_DATABASE = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"
DATASET = "budget"
SCOPE = "ci4295:2026-10-10"
SOURCE_DATE = "2026-10-10"
YEAR = "2026"
SOURCE_OPERATION = "QWGJK"
CONTRACT = "G2B_4295_DEEP_424_OF_477_V1"
SIZE = 1
LAST_PAGE = 477
PREVIOUS_PAGES = 423
PHASES = frozenset({"seed", "interrupt", "resume", "finish"})


def _authorized(phase):
    return (
        phase in PHASES
        and os.environ.get("G2B_CI_DEEP_RESUME") == "1"
        and os.environ.get("G2B_DATABASE_URL") == CI_DATABASE
        and os.environ.get("G2B_APP_SCHEMA") == "g2b_app_4292_ci"
        and os.environ.get("G2B_BUDGET_SCHEMA") == "g2b_budget_4292_ci"
        and os.environ.get("G2B_AUTO_SYNC_DISABLE") == "1"
        and not any(os.environ.get(k) for k in (
            "G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"
        ))
    )


def _row(page):
    return {
        "fyr": YEAR,
        "dbiz_cd": f"CI4295-ROW-{page:05d}",
        "dbiz_nm": "CI ONLY SYNTHETIC LED BUDGET",
        "amount": 100000 + int(page),
        "ci_test_only": "SYNTHETIC_QWGJK_NO_SOURCE_IO",
    }


def _key(row):
    return str(row["dbiz_cd"])


def _fingerprint():
    return hashlib.sha256(json.dumps(
        [DATASET, SCOPE, YEAR, SOURCE_DATE, SOURCE_OPERATION, CONTRACT],
        ensure_ascii=False,
    ).encode()).hexdigest()


def _meta():
    return {
        "version": 1,
        "generation": uuid.uuid4().hex,
        "page_size": SIZE,
        "query_fingerprint": _fingerprint(),
        "checkpoint_contract": CONTRACT,
        "completion_reason": "",
        "drift_replay_count": 0,
    }


def _verify(expected_next, expected_status, *, expected_fetched=None):
    import budget_pg_collection as collector
    import budget_pg_store as store
    cp = store.get_checkpoint(DATASET, SCOPE)
    if not cp:
        raise AssertionError("CI4295_CHECKPOINT_MISSING")
    if int(cp["page_no"]) != expected_next:
        raise AssertionError("CI4295_UNEXPECTED_NEXT_PAGE")
    if cp["status"] != expected_status:
        raise AssertionError("CI4295_UNEXPECTED_RUN_STATE")
    fetched = expected_next - 1 if expected_fetched is None else expected_fetched
    if int(cp["fetched_count"]) != fetched or int(cp["saved_count"]) != fetched:
        raise AssertionError("CI4295_CHECKPOINT_COUNT_MISMATCH")
    if int(cp["source_total"]) != LAST_PAGE:
        raise AssertionError("CI4295_SOURCE_TOTAL_CHANGED")
    if not collector.verified_checkpoint(cp):
        raise AssertionError("CI4295_RECEIPT_HASH_OR_OBSERVATION_INVALID")
    return cp


def _seed():
    from sqlalchemy import insert
    import budget_pg_collection as collector
    import budget_pg_store as store
    import memory_guard

    if store.get_checkpoint(DATASET, SCOPE) is not None:
        raise RuntimeError("CI4295_EXISTING_SYNTHETIC_SCOPE_PROTECTED")
    engine, tables = store._engine_and_tables()
    meta = _meta()
    pages = []
    items = []
    with engine.begin() as conn:
        for page in range(1, PREVIOUS_PAGES + 1):
            if page % 16 == 1:
                memory_guard.cooperative_batch_checkpoint(timeout=5)
            row = _row(page)
            key = _key(row)
            digest = store.observation_digest(DATASET, row)
            # Source-free existing normalized PostgreSQL observation path.
            stored = store.preserve_observation(
                DATASET, key, row,
                source_system="CI_SYNTHETIC",
                source_operation=SOURCE_OPERATION,
                source_date=SOURCE_DATE,
                _conn=conn,
            )
            if stored["sha256"] != digest:
                raise AssertionError("CI4295_OBSERVATION_DIGEST_CHANGED")
            pairs = [(key, digest)]
            pages.append({
                "dataset": DATASET,
                "scope_key": SCOPE,
                "generation": meta["generation"],
                "page_no": page,
                "page_size": SIZE,
                "response_hash": hashlib.sha256(
                    json.dumps(sorted(pairs)).encode()
                ).hexdigest(),
                "item_count": 1,
                "source_total": LAST_PAGE,
                "terminal_reason": "",
            })
            items.append({
                "dataset": DATASET,
                "scope_key": SCOPE,
                "generation": meta["generation"],
                "source_key": key,
                "page_no": page,
                "payload_sha256": digest,
            })
        conn.execute(insert(tables["pages"]), pages)
        conn.execute(insert(tables["items"]), items)
        store.save_checkpoint(
            DATASET, SCOPE, _conn=conn,
            cursor_value=json.dumps(meta, sort_keys=True),
            range_start=YEAR,
            range_end=SOURCE_DATE,
            page_no=PREVIOUS_PAGES + 1,
            page_size=SIZE,
            last_page_fingerprint="",
            source_total=LAST_PAGE,
            fetched_count=PREVIOUS_PAGES,
            saved_count=PREVIOUS_PAGES,
            status="RUNNING",
            last_error="",
        )
    cp = _verify(424, "RUNNING")
    if len(pages) != 423 or len(items) != 423:
        raise AssertionError("CI4295_INCOMPLETE_SYNTHETIC_PRIOR_RECEIPTS")
    print(
        "G2B_4295_PAGE_424_OF_477_SEEDED",
        "prior_receipt_pages", len(pages),
        "next_page", cp["page_no"],
        "source_api_calls", 0,
        flush=True,
    )


def _collect(*, max_pages, expect_calls=None, fail_post_fetch=False,
             forbid_fetch=False):
    import budget_pg_collection as collector
    import memory_guard

    calls = []

    def fetch(page, size):
        if forbid_fetch:
            raise AssertionError("CI4295_COMPLETE_CHECKPOINT_MUST_SKIP_SOURCE")
        if page < 424 or page > LAST_PAGE or size != SIZE:
            raise AssertionError("CI4295_SOURCE_PAGE_SCOPE_VIOLATION")
        calls.append(page)
        return [_row(page)], LAST_PAGE

    old_check = collector._memory_checkpoint
    calls_to_check = [0]
    if fail_post_fetch:
        def memory_checkpoint():
            calls_to_check[0] += 1
            if calls_to_check[0] == 2:
                raise memory_guard.MemoryPressureError(
                    "CI4295_PAGE424_POST_FETCH_HOLD"
                )
            return old_check()
        collector._memory_checkpoint = memory_checkpoint
    try:
        result = collector.collect_pages(
            dataset=DATASET,
            scope=SCOPE,
            range_start=YEAR,
            range_end=SOURCE_DATE,
            page_size=SIZE,
            max_pages=max_pages,
            resume=True,
            fetch=fetch,
            identity=_key,
            source_system="CI_SYNTHETIC",
            source_operation=SOURCE_OPERATION,
            source_date=lambda row: SOURCE_DATE,
            checkpoint_contract=CONTRACT,
        )
    finally:
        collector._memory_checkpoint = old_check
    if expect_calls is not None and calls != expect_calls:
        raise AssertionError("CI4295_UNEXPECTED_FETCHED_PAGES")
    return result, calls


def _interrupt():
    import budget_pg_store as store
    import budget_pg_collection as collector
    import memory_guard

    prior = _verify(424, "RUNNING")
    generation = json.loads(prior["cursor_value"])["generation"]
    try:
        _collect(max_pages=1, fail_post_fetch=True)
    except memory_guard.MemoryPressureError:
        pass
    else:
        raise AssertionError("CI4295_POST_FETCH_MEMORY_HOLD_NOT_TRIGGERED")
    cp = _verify(424, "INCOMPLETE")
    if cp.get("last_error") != "MEMORY_PRESSURE":
        raise AssertionError("CI4295_LOST_MEMORY_PRESSURE_REASON")
    if json.loads(cp["cursor_value"])["generation"] != generation:
        raise AssertionError("CI4295_PREVIOUS_RECEIPTS_REPLACED")
    print("G2B_4295_PAGE424_POST_FETCH_HOLD_RETAINED",
          "next_page", 424, "fetched", 423,
          "receipt_generation_preserved", True, flush=True)


def _resume():
    cp = _verify(424, "INCOMPLETE")
    generation = json.loads(cp["cursor_value"])["generation"]
    # A fresh subprocess resumes pages 424..476 only, retaining prior receipts.
    result, calls = _collect(
        max_pages=53,
        expect_calls=list(range(424, 477)),
    )
    if result["status"] != "RUNNING" or result.get("resumed") is not True:
        raise AssertionError("CI4295_NEXT_PAGE_NOT_RESUMED")
    next_cp = _verify(477, "RUNNING")
    if json.loads(next_cp["cursor_value"])["generation"] != generation:
        raise AssertionError("CI4295_RECEIPT_GENERATION_CHANGED_ON_RESUME")
    print(
        "G2B_4295_PAGE424_THROUGH_476_RESUMED",
        "new_pages", len(calls),
        "next_page", next_cp["page_no"],
        "prior_receipts_preserved", True,
        flush=True,
    )


def _finish():
    cp = _verify(477, "RUNNING")
    generation = json.loads(cp["cursor_value"])["generation"]
    result, calls = _collect(max_pages=1, expect_calls=[477])
    if result["status"] != "COMPLETE" or result.get("complete") is not True:
        raise AssertionError("CI4295_FINAL_477_PAGE_NOT_COMPLETE")
    if not result.get("resumed"):
        raise AssertionError("CI4295_FINAL_PAGE_DID_NOT_RESUME")
    cp = _verify(478, "COMPLETE", expected_fetched=477)
    if json.loads(cp["cursor_value"])["generation"] != generation:
        raise AssertionError("CI4295_GENERATION_CHANGED_ON_FINAL_COMMIT")
    no_replay, zero_calls = _collect(
        max_pages=1,
        forbid_fetch=True,
    )
    if zero_calls or no_replay.get("complete") is not True:
        raise AssertionError("CI4295_ALREADY_COMPLETE_PAGE_WAS_REFETCHED")
    print(
        "G2B_4295_ALL_477_SYNTHETIC_PAGES_VERIFIED",
        "pages", 477, "next_page", 478,
        "resume_start", 424, "first_pages_refetched", 0,
        "source_api_calls", 0,
        flush=True,
    )


def main():
    phase = str(sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if not _authorized(phase):
        print("G2B_4295_CI_ONLY_AUTHORIZATION_HOLD", flush=True)
        return 2

    os.environ["G2B_TEST_MODE"] = "0"
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_ISOLATED_HEAVY_WORKER"] = "1"
    os.environ["G2B_SOURCE_ONLY_RUNTIME"] = "1"
    os.environ["G2B_MEMORY_SOFT_LIMIT_MB"] = "112"

    import memory_guard
    memory_guard.apply_default_process_tuning()
    stage = "prepare"
    try:
        import vnext_clean_app as app
        if not app._SOURCE_ONLY_RUNTIME:
            raise RuntimeError("CI4295_SOURCE_ONLY_INIT_REQUIRED")
        if not app.initialize_isolated_source_backend_read_only("budget"):
            raise RuntimeError("CI4295_DISPOSABLE_PG_BOOTSTRAP_REQUIRED")
        # Synthetic transport is implemented in-memory in this *one process*.
        # The production network transport and API quotas remain untouched.
        import vnext_source_guard
        vnext_source_guard.require_attested_transport_result = (
            lambda before, result, **kwargs: result
        )
        import vnext_http
        import lofin_vnext_http

        def forbidden(*_args, **_kwargs):
            raise RuntimeError("CI4295_EXTERNAL_SOURCE_REQUEST_FORBIDDEN")
        vnext_http.request = forbidden
        lofin_vnext_http.fetch_budget_page = forbidden
        # Even the forced post-fetch HOLD has two numeric samples: the
        # pre-phase cgroup snapshot and the normal pre-fetch guard.
        memory_guard.snapshot(collect=False)
        stage = phase
        if phase == "seed":
            _seed()
        elif phase == "interrupt":
            _interrupt()
        elif phase == "resume":
            _resume()
        elif phase == "finish":
            _finish()
        else:
            raise AssertionError("CI4295_UNREACHABLE_PHASE")
        return 0
    except Exception as exc:
        print(
            "G2B_4295_DEEP_RESUME_HOLD", phase, stage,
            type(exc).__name__, flush=True,
        )
        return 1
    finally:
        print(
            "G2B_4295_DEEP_RESUME_MEMORY", phase,
            json.dumps(
                memory_guard.isolated_worker_memory_profile(),
                separators=(",", ":"), sort_keys=True,
            ),
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
