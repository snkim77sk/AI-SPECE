"""CI ONLY: exercise real G2B 4.1 page transactions with SYNTHETIC source rows.

This is deliberately NOT a live collector: blank credentials, fixed disposable
PostgreSQL URL, no transport, and synthetic record keys. Never enable on Cafe24.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CI_DATABASE = "postgresql://postgres:postgres@127.0.0.1:5432/g2b_vnext_test"


def main():
    mode = str(sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if (
        mode not in {"shopping", "budget"}
        or os.environ.get("G2B_CI_SYNTHETIC_PAGES") != "1"
        or os.environ.get("G2B_DATABASE_URL") != CI_DATABASE
        or any(os.environ.get(key) for key in (
            "G2B_SERVICE_KEY", "LOFIN_API_KEY", "EDUINFO_API_KEY"
        ))
    ):
        print("G2B_4293_SYNTHETIC_CANARY_AUTH_HOLD", flush=True)
        return 2

    os.environ["G2B_TEST_MODE"] = "0"
    os.environ["G2B_RUNTIME_ROLE"] = "LOCAL_COLLECTOR"
    os.environ["G2B_ISOLATED_HEAVY_WORKER"] = "1"
    os.environ["G2B_SOURCE_ONLY_RUNTIME"] = "1"
    os.environ["G2B_AUTO_SYNC_DISABLE"] = "1"
    os.environ["G2B_MEMORY_SOFT_LIMIT_MB"] = "112"

    import memory_guard

    memory_guard.apply_default_process_tuning()
    stage = "import"
    try:
        import vnext_clean_app as app
        if not app._SOURCE_ONLY_RUNTIME:
            raise RuntimeError("SOURCE_ONLY_RUNTIME_REQUIRED")
        stage = "read_only_backend"
        if not app.initialize_isolated_source_backend_read_only(mode):
            raise RuntimeError("EXISTING_CI_POSTGRES_BOOTSTRAP_REQUIRED")

        import vnext_source_guard
        # Only this disposable Python process bypasses the attested SOURCE
        # TRANSPORT to inject generated page dictionaries. No server module,
        # real HTTP client or production code is modified by the test.
        vnext_source_guard.require_attested_transport_result = (
            lambda before, result, **kwargs: result
        )

        def forbid_live_transport(*args, **kwargs):
            raise RuntimeError("EXTERNAL_SOURCE_HTTP_FORBIDDEN_IN_CI")
        import vnext_http
        import lofin_vnext_http
        vnext_http.request = forbid_live_transport
        lofin_vnext_http.fetch_budget_page = forbid_live_transport

        stage = "synthetic_page"
        if mode == "shopping":
            import shopping_vnext
            shopping_vnext._request = forbid_live_transport
            def shopping_page(start, end, page=1, rows=999):
                if (start, end, page, rows) != (
                    "2026-10-08", "2026-10-08", 1, 999
                ):
                    raise RuntimeError("SYNTHETIC_SHOPPING_PAGE_SCOPE_MISMATCH")
                return [{
                    "dlvrReqNo": f"CI4293-S-{n:05d}",
                    "dlvrReqChgOrd": "0",
                    "prdctSno": "1",
                    "dlvrReqRcptDate": "20261008",
                    "dtilPrdctClsfcNo": "3911160302",
                    "prdctNm": "LED 가로등기구",
                    "dminsttNm": "CI TEST PUBLIC ORGANIZATION",
                    "corpNm": "CI SYNTHETIC LIGHTING",
                    "prdctQty": "1", "prdctUprc": "100000",
                    "prdctAmt": "100000",
                    "ci_synthetic_note": "SOURCE_FREE_TEST_ONLY" * 28,
                } for n in range(999)], 999
            shopping_vnext.fetch_page = shopping_page
            result = shopping_vnext.collect_all(
                "2026-10-08", "2026-10-08",
                page_size=999, max_pages=1, resume=False,
            )
            expected = 999
        else:
            import budget_pg_collection
            def fetch_budget(page, size):
                if (page, size) != (1, 1000):
                    raise RuntimeError("SYNTHETIC_BUDGET_PAGE_SCOPE_MISMATCH")
                return [{
                    "fyr": "2026", "dbiz_cd": f"CI4293-B-{n:05d}",
                    "dbiz_nm": "CI SYNTHETIC LED PROGRAM",
                    "amount": 100000 + n,
                    "ci_synthetic_note": "SOURCE_FREE_TEST_ONLY" * 28,
                } for n in range(1000)], 1000
            result = budget_pg_collection.collect_pages(
                dataset="budget", scope="ci4293:2026-10-08",
                range_start="2026", range_end="2026-10-08",
                page_size=1000, max_pages=1, resume=False,
                fetch=fetch_budget,
                identity=lambda row: row["dbiz_cd"],
                source_system="CI_SYNTHETIC", source_operation="QWGJK",
                source_date=lambda row: "2026-10-08",
                checkpoint_contract="G2B_4293_SYNTHETIC_PG_V1",
            )
            expected = 1000

        if not result.get("complete") or int(result.get("fetched") or 0) != expected:
            raise RuntimeError("SYNTHETIC_PAGE_NOT_COMPLETE")
        if int(result.get("saved") or 0) != expected:
            raise RuntimeError("SYNTHETIC_PAGE_NOT_ALL_PERSISTED")
        print(
            "G2B_4293_SYNTHETIC_PAGE_COMPLETE", mode,
            "rows", expected, "source_api_calls", 0,
            flush=True,
        )
        return 0
    except Exception as exc:
        print("G2B_4293_SYNTHETIC_PAGE_FAILED", mode, stage,
              type(exc).__name__, flush=True)
        return 1
    finally:
        print(
            "G2B_4293_SYNTHETIC_PAGE_MEMORY", mode,
            json.dumps(memory_guard.isolated_worker_memory_profile(),
                       separators=(",", ":"), sort_keys=True),
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
