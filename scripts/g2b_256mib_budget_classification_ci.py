"""Disposable CI ONLY: bounded budget classification memory benchmark in 256MiB.

Reuses 2,000 QWGJK 1,000-row synthetic pages from V4.1.293/294 and the 477
one-row-per-page deep-resume synthetic observations from V4.1.295.
The unchanged production classify_dataset() function writes exact-current
PostgreSQL and compatibility classifications. No source API access.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CI_DATABASE = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"
EXPECTED_MIN_CURRENT = 2477
DATASETS = ("budget", "budget_appropriation", "education_budget")
BATCH_SIZE = 250
MAX_BATCHES = 16


def authorized():
    return (
        os.environ.get("G2B_CI_BUDGET_CLASSIFY") == "1"
        and os.environ.get("G2B_DATABASE_URL") == CI_DATABASE
        and os.environ.get("G2B_APP_SCHEMA") == "g2b_app_4292_ci"
        and os.environ.get("G2B_BUDGET_SCHEMA") == "g2b_budget_4292_ci"
        and os.environ.get("G2B_RUNTIME_ROLE") == "UNIFIED"
        and os.environ.get("G2B_TEST_MODE") == "0"
        and os.environ.get("G2B_AUTO_SYNC_DISABLE") == "1"
        and not any(os.environ.get(k) for k in (
            "G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"
        ))
    )


def main():
    if not authorized():
        print("G2B_4296_CLASSIFICATION_CI_AUTH_HOLD", flush=True)
        return 2
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_SOURCE_ONLY_RUNTIME"] = "1"
    os.environ["G2B_ISOLATED_HEAVY_WORKER"] = "1"
    os.environ["G2B_MEMORY_SOFT_LIMIT_MB"] = "112"

    import memory_guard
    memory_guard.apply_default_process_tuning()
    stage = "import"
    try:
        import vnext_clean_app as app
        if not app._SOURCE_ONLY_RUNTIME:
            raise RuntimeError("SOURCE_ONLY_RUNTIME_REQUIRED")
        stage = "read_only_backend"
        if not app.initialize_isolated_source_backend_read_only("budget"):
            raise RuntimeError("CI_POSTGRES_SCHEMA_NOT_READY")
        import budget_pg_store
        import classification_vnext
        import vnext_http
        import lofin_vnext_http

        def no_source_requests(*_args, **_kwargs):
            raise RuntimeError("CI4296_EXTERNAL_SOURCE_HTTP_FORBIDDEN")
        vnext_http.request = no_source_requests
        lofin_vnext_http.fetch_budget_page = no_source_requests

        stage = "current_count"
        current_count = budget_pg_store.current_state_count("budget")
        if current_count < EXPECTED_MIN_CURRENT:
            raise RuntimeError("CI4296_SYNTHETIC_PRIOR_PAGES_INCOMPLETE")
        # Use the real production classifier_version; do not overwrite its
        # formulas, choose/force a new version, or bypass the batch memory guard.
        version = classification_vnext.CLASSIFIER_VERSION
        initial = budget_pg_store.current_classified_counts(
            DATASETS, version
        ).get("budget", 0)
        if initial != 0:
            raise RuntimeError("CI4296_EXPECTED_UNCLASSIFIED_SYNTHETIC_ROWS")

        stage = "classify_budget"
        memory_guard.snapshot(collect=False)
        results = {}
        for dataset in DATASETS:
            result = classification_vnext.classify_dataset(
                dataset,
                batch_size=BATCH_SIZE,
                max_batches=MAX_BATCHES,
                force=False,
            )
            if result.get("batch_limit_reached"):
                raise RuntimeError("CI4296_BATCH_LIMIT_INCOMPLETE")
            results[dataset] = {
                "classified": int(result.get("classified") or 0),
                "batches_done": int(result.get("batches_done") or 0),
                "current_rows_scanned": int(result.get("current_rows_scanned") or 0),
            }

        if results["budget"]["classified"] != current_count:
            raise RuntimeError("CI4296_MISSING_NEW_OR_CHANGED_CLASSIFICATIONS")
        after = budget_pg_store.current_classified_counts(
            DATASETS, version
        ).get("budget", 0)
        if after != current_count:
            raise RuntimeError("CI4296_POSTGRES_CURRENT_HASH_JOIN_MISMATCH")

        # Fresh second classification pass is required to have no new writes.
        # This proves repeat scheduling does not scan and rewrite every row.
        stage = "idempotent_repeat"
        duplicate = classification_vnext.classify_dataset(
            "budget",
            batch_size=BATCH_SIZE,
            max_batches=MAX_BATCHES,
            force=False,
        )
        if int(duplicate.get("classified") or 0) != 0:
            raise RuntimeError("CI4296_REPEAT_RECLASSIFIED_EXISTING_ROWS")

        # Deep source receipt still remains complete after classification;
        # no classifier is authorized to reset it.
        import budget_pg_collection
        checkpoint = budget_pg_store.get_checkpoint(
            "budget", "ci4295:2026-10-10"
        )
        if (
            not checkpoint
            or checkpoint.get("status") != "COMPLETE"
            or int(checkpoint.get("page_no") or 0) != 478
            or not budget_pg_collection.verified_checkpoint(checkpoint)
        ):
            raise RuntimeError("CI4296_DEEP_477_RECEIPTS_LOST")

        memory_guard.snapshot(collect=False)
        print(
            "G2B_4296_BUDGET_CLASSIFICATION_COMPLETE",
            json.dumps({
                "budget_current_rows": current_count,
                "budget_rows_classified": after,
                "batch_size": BATCH_SIZE,
                "max_batches": MAX_BATCHES,
                "source_free": True,
                "repeat_classified": 0,
                "checkpoint_477_complete": True,
                "source_api_calls": 0,
                "dataset_results": results,
            }, sort_keys=True, separators=(",", ":")),
            flush=True,
        )
        return 0
    except Exception as exc:
        print(
            "G2B_4296_BUDGET_CLASSIFICATION_HOLD",
            stage, type(exc).__name__, flush=True,
        )
        return 1
    finally:
        print(
            "G2B_4296_BUDGET_CLASSIFICATION_MEMORY",
            json.dumps(
                memory_guard.isolated_worker_memory_profile(),
                sort_keys=True, separators=(",", ":"),
            ),
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
